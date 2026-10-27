"""
codebud/tools/embeddings.py

Embeds text with Ollama's /api/embed endpoint, and provides the
EmbeddingSearchTool ("search_embeddings"): hybrid search over the
project index that `codebud index` builds (see codebud.rag).  If the
embedding model is unreachable, an error is reported; fake vectors are
never substituted.

Environment variables:
  OLLAMA_BASE_URL   Base URL of the Ollama server (default: http://localhost:11434)
  EMBED_MODEL       Embedding model (default: all-minilm).  A dedicated
                    embedding model, not the text-generation OLLAMA_MODEL.
  CODEBUD_EMBED_STUB  Tests only: "1" uses a deterministic offline stub
                    instead of Ollama.
"""

import hashlib
import json
import math
import os
from typing import Any

import requests

from codebud.llm_stub import num_ctx, ollama_proxies

_OLLAMA_BASE = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
# all-minilm: on scripts/rag_eval.py it matched nomic-embed-text with hybrid
# search while indexing 8x faster on a CPU (66 s vs 522 s for this repository).
DEFAULT_EMBED_MODEL = "all-minilm"

# Texts sent to Ollama per /api/embed request when indexing.
BATCH_SIZE = 32

# Some embedding models expect a task prefix on every input: (document, query).
_TASK_PREFIXES = {
    "nomic-embed-text": ("search_document: ", "search_query: "),
    # Qwen3-Embedding: an instruction on queries only (per its model card).
    "qwen3-embedding": (
        "",
        "Instruct: Given a question about a code base, retrieve the code or "
        "documentation that answers it\nQuery: ",
    ),
}


class EmbeddingUnavailable(RuntimeError):
    """The embedding model could not be reached or returned nothing."""


def embed_model() -> str:
    """Return the embedding model: EMBED_MODEL, else DEFAULT_EMBED_MODEL."""
    return os.environ.get("EMBED_MODEL") or DEFAULT_EMBED_MODEL


def active_model() -> str:
    """Name recorded in an index for the vectors produced now ("stub" in tests)."""
    return "stub" if _stub_enabled() else embed_model()


def _task_prefixes(model: str) -> tuple[str, str]:
    return _TASK_PREFIXES.get(model.split(":")[0], ("", ""))


# ---------------------------------------------------------------------------
# Embedding
# ---------------------------------------------------------------------------


def _ollama_embed_many(texts: list[str], model: str) -> list[list[float]]:
    try:
        r = requests.post(
            f"{_OLLAMA_BASE}/api/embed",
            json={"model": model, "input": texts, "options": {"num_ctx": num_ctx()}},
            timeout=120,
            proxies=ollama_proxies(_OLLAMA_BASE),
        )
        r.raise_for_status()
        vectors = r.json().get("embeddings") or []
    except Exception as exc:
        raise EmbeddingUnavailable(
            f"Embedding model unavailable ({model} at {_OLLAMA_BASE}): {exc}. "
            f"Start Ollama and run: ollama pull {model}"
        ) from exc
    if len(vectors) != len(texts) or not all(vectors):
        raise EmbeddingUnavailable(
            f"{model} returned {len(vectors)} embeddings for {len(texts)} texts"
        )
    return vectors


def embed_documents(texts: list[str]) -> list[list[float]]:
    """Embed texts for storing in an index, BATCH_SIZE per request."""
    if _stub_enabled():
        return [stub_embedding(t) for t in texts]
    model = embed_model()
    prefix = _task_prefixes(model)[0]
    vectors: list[list[float]] = []
    for start in range(0, len(texts), BATCH_SIZE):
        batch = [prefix + t for t in texts[start : start + BATCH_SIZE]]
        vectors.extend(_ollama_embed_many(batch, model))
    return vectors


def embed_query(text: str) -> list[float]:
    """Embed a search query (with the model's query prefix, if it has one)."""
    if _stub_enabled():
        return stub_embedding(text)
    model = embed_model()
    return _ollama_embed_many([_task_prefixes(model)[1] + text], model)[0]


def stub_embedding(text: str) -> list[float]:
    """Deterministic 16-float unit vector from SHA-256 (tests only).

    Unlike hash(), SHA-256 is not salted per process, so the same text
    always gets the same vector.
    """
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    raw = [b - 127.5 for b in digest[:16]]
    norm = math.sqrt(sum(x * x for x in raw)) or 1.0
    return [x / norm for x in raw]


def _stub_enabled() -> bool:
    return os.environ.get("CODEBUD_EMBED_STUB") == "1"


# ---------------------------------------------------------------------------
# Tool
# ---------------------------------------------------------------------------


class EmbeddingSearchTool:
    """Search the project index (built by `codebud index`) for a query."""

    description = "Search this project's code and documentation by meaning and by name."
    usage_hint = (
        'Required: "query" (str). Optional: "top_k" (int, default 5). Returns the most '
        "relevant chunks with path and line numbers. Needs an index (codebud index)."
    )
    required_args = ("query",)

    def run(self, args: dict[str, Any]) -> dict[str, Any]:
        # Imported here: codebud.rag.search imports this module.
        from codebud.rag.indexer import project_root
        from codebud.rag.search import SearchUnavailable, search
        from codebud.rag.store import ModelMismatch

        query = args.get("query")
        # "top_k" is the name in usage_hint; "k" is accepted for compatibility.
        k = args.get("top_k", args.get("k", 5))
        if not query:
            return {"stdout": "", "stderr": "Missing required argument: query", "returncode": 1}
        try:
            hits = search(project_root(), query, top_k=int(k))
        except (SearchUnavailable, ModelMismatch, EmbeddingUnavailable) as exc:
            return {"stdout": "", "stderr": str(exc), "returncode": 1}
        results = [
            {
                "path": h.chunk.path,
                "lines": f"{h.chunk.start_line}-{h.chunk.end_line}",
                "symbol": h.chunk.symbol,
                "score": round(h.score, 4),
                "text": h.chunk.text,
            }
            for h in hits
        ]
        return {"stdout": json.dumps(results, indent=2), "stderr": "", "returncode": 0}
