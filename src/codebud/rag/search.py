"""
codebud/rag/search.py

Hybrid search over a project index (phase 9, C1).

Three ranked lists are merged by reciprocal-rank fusion (RRF: each list
adds weight / (RRF_K + rank) to a chunk's score):

- vectors:  cosine similarity of the query embedding to every chunk.
            Vectors are stored at unit length, so this is one matrix-vector
            product (numpy/BLAS; decision D13c -- the codebud[rag] extra);
- keywords: BM25 over path, symbol and text (SQLite FTS5) -- exact
            identifiers and error strings, where embeddings are weak;
- symbols:  chunks whose symbol is a word of the query (``compact_history``
            or ``AgentCore.compact_history``), weighted double: naming a
            definition is strong evidence, and BM25 alone ranks the files
            that merely mention a name above the one that defines it.

Overlapping windows of the same file are returned once (the better one).
The vector matrix is cached per index and reloaded when the index changes.
"""

import re
from dataclasses import dataclass
from pathlib import Path

from codebud.rag.chunker import Chunk
from codebud.rag.store import IndexStore
from codebud.tools.embeddings import active_model, embed_query

RRF_K = 60
LISTS = ("vector", "keyword", "symbol")
CANDIDATES = 50  # taken from each list before fusion
SYMBOL_WEIGHT = 2.0

# Fused score multiplier for chunks from test files, unless the query is
# about tests: tests mention every name they test, so they crowd out the
# code that defines it. Chosen with scripts/rag_eval.py.
TEST_WEIGHT = 0.5

_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")
_ABOUT_TESTS = re.compile(r"\b(tests?|pytest|testing)\b", re.IGNORECASE)

# db path -> (generation, chunk ids, unit-vector matrix)
_matrix_cache: dict[str, tuple] = {}


class SearchUnavailable(RuntimeError):
    """Project search cannot run (no index for the project, or no numpy)."""


@dataclass(frozen=True)
class Hit:
    chunk: Chunk
    score: float


def search(
    root: Path | str,
    query: str,
    top_k: int = 5,
    lists: tuple[str, ...] = LISTS,
    test_weight: float | None = None,
) -> list[Hit]:
    """Return the ``top_k`` chunks of ``root``'s index most relevant to ``query``.

    ``lists`` chooses the rankings to fuse (all three by default); without
    "vector" the query is not embedded. ``test_weight`` overrides
    TEST_WEIGHT. scripts/rag_eval.py compares the settings.
    """
    unknown = set(lists) - set(LISTS)
    if unknown:
        raise ValueError(f"unknown ranking(s): {', '.join(sorted(unknown))}")
    try:
        import numpy as np
    except ImportError as exc:
        raise SearchUnavailable(
            "Project search needs numpy: pip install 'codebud[rag]' (or your distribution's numpy)"
        ) from exc
    root = Path(root).resolve()
    store = IndexStore.open(root)
    if store is None:
        raise SearchUnavailable(f"No project index for {root}. Create one with: codebud index")
    with store:
        store.check_model(active_model())
        ids, matrix = _vectors(store, np)
        if not len(ids):
            return []
        scores: dict[int, float] = {}
        if "vector" in lists:
            query_vec = np.asarray(embed_query(query), dtype=np.float32)
            norm = float(np.linalg.norm(query_vec))
            if norm:
                query_vec /= norm
            similarity = matrix @ query_vec
            best = np.argsort(-similarity)[:CANDIDATES]
            _fuse(scores, [int(ids[i]) for i in best])
        if "keyword" in lists:
            _fuse(scores, store.keyword_search(query, CANDIDATES))
        if "symbol" in lists:
            _fuse(scores, store.symbol_search(_IDENTIFIER.findall(query)), SYMBOL_WEIGHT)

        weight = TEST_WEIGHT if test_weight is None else test_weight
        if weight != 1.0 and not _ABOUT_TESTS.search(query):
            for chunk_id in scores:
                if _is_test_path(store.chunk(chunk_id).path):
                    scores[chunk_id] *= weight

        hits: list[Hit] = []
        for chunk_id, score in sorted(scores.items(), key=lambda kv: -kv[1]):
            chunk = store.chunk(chunk_id)
            if not any(_overlaps(chunk, h.chunk) for h in hits):
                hits.append(Hit(chunk, score))
                if len(hits) == top_k:
                    break
        return hits


def _fuse(scores: dict[int, float], ranked: list[int], weight: float = 1.0) -> None:
    for rank, chunk_id in enumerate(ranked):
        scores[chunk_id] = scores.get(chunk_id, 0.0) + weight / (RRF_K + rank + 1)


def _is_test_path(path: str) -> bool:
    parts = path.split("/")
    name = parts[-1]
    return (
        bool({"tests", "test"} & set(parts[:-1]))
        or name.startswith("test_")
        or name.endswith(("_test.py", "_tests.py"))
    )


def _overlaps(a: Chunk, b: Chunk) -> bool:
    return a.path == b.path and a.start_line <= b.end_line and b.start_line <= a.end_line


def _vectors(store: IndexStore, np):
    """(ids, matrix) for every chunk in ``store``, cached until it changes."""
    key = str(store.db_path)
    cached = _matrix_cache.get(key)
    if cached and cached[0] == store.generation:
        return cached[1], cached[2]
    rows = list(store.vectors())
    ids = np.array([r[0] for r in rows], dtype=np.int64)
    if rows:
        matrix = np.vstack([np.frombuffer(r[1], dtype=np.float32) for r in rows])
    else:
        matrix = np.zeros((0, 0), dtype=np.float32)
    _matrix_cache[key] = (store.generation, ids, matrix)
    return ids, matrix
