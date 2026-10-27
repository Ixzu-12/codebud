"""
tests/test_rag_search.py

Hybrid search over the project index (phase 9, C1): vector similarity,
BM25 keywords and exact symbol names, merged by reciprocal-rank fusion.
Vectors are written directly so the ranking is deterministic.
"""

import importlib.util
import sys

import pytest

from codebud.rag import search
from codebud.rag.chunker import Chunk
from codebud.rag.store import IndexStore, ModelMismatch

DIM = 4

# numpy is optional (the codebud[rag] extra): without it these tests skip,
# so `make check` passes with a Python that only has the core dependencies.
needs_numpy = pytest.mark.skipif(
    importlib.util.find_spec("numpy") is None, reason="numpy (codebud[rag]) not installed"
)


def _chunk(path, text, symbol=None, start=1, end=None):
    return Chunk(path, start, end or start + text.count("\n"), symbol, "python", text)


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    return root


def _fill(project, entries):
    """entries: [(chunk, vector)], one file per distinct path."""
    with IndexStore.open(project, create=True) as store:
        store.ensure_model("stub", DIM)
        by_path = {}
        for chunk, vector in entries:
            by_path.setdefault(chunk.path, []).append((chunk, vector))
        for path, items in by_path.items():
            store.replace_file(path, "h", items)


@pytest.fixture
def query_vector(monkeypatch):
    holder = {"v": [1.0, 0.0, 0.0, 0.0]}
    monkeypatch.setattr(search, "embed_query", lambda _text: holder["v"])
    return holder


def _paths(hits):
    return [h.chunk.path for h in hits]


@needs_numpy
def test_vector_similarity_orders_results(project, query_vector):
    _fill(
        project,
        [
            (_chunk("far.py", "unrelated words"), [0.0, 0.0, 0.0, 1.0]),
            (_chunk("near.py", "other words"), [0.9, 0.1, 0.0, 0.0]),
            (_chunk("mid.py", "more words"), [0.5, 0.5, 0.0, 0.0]),
        ],
    )
    hits = search.search(project, "zzz", top_k=3)
    assert _paths(hits) == ["near.py", "mid.py", "far.py"]
    assert hits[0].score > hits[1].score > hits[2].score


@needs_numpy
def test_keyword_match_lifts_a_chunk_vectors_miss(project, query_vector):
    _fill(
        project,
        [
            (_chunk("a.py", "alpha"), [1.0, 0.0, 0.0, 0.0]),
            (_chunk("b.py", "beta"), [0.8, 0.2, 0.0, 0.0]),
            (_chunk("c.py", "raise PolicyRejected here"), [0.0, 0.0, 1.0, 0.0]),
        ],
    )
    assert "c.py" in _paths(search.search(project, "PolicyRejected", top_k=2))


@needs_numpy
def test_exact_symbol_name_beats_files_that_only_mention_it(project, query_vector):
    """BM25 alone ranked tests mentioning compact_history above its definition."""
    mentions = "compact_history(); compact_history(); assert compact_history"
    _fill(
        project,
        [
            (_chunk("tests/test_core.py", mentions, "test_compact"), [0.6, 0.4, 0.0, 0.0]),
            (
                _chunk(
                    "core.py", "def compact_history(self):\n    ...", "AgentCore.compact_history"
                ),
                [0.5, 0.5, 0.0, 0.0],
            ),
        ],
    )
    hits = search.search(project, "where is compact_history defined?", top_k=2)
    assert _paths(hits)[0] == "core.py"


@needs_numpy
def test_overlapping_windows_of_one_file_are_returned_once(project, query_vector):
    _fill(
        project,
        [
            (_chunk("big.py", "window one", "f", start=1, end=40), [1.0, 0.0, 0.0, 0.0]),
            (_chunk("big.py", "window two", "f", start=33, end=72), [0.99, 0.01, 0.0, 0.0]),
            (_chunk("other.py", "x"), [0.5, 0.5, 0.0, 0.0]),
        ],
    )
    hits = search.search(project, "zzz", top_k=3)
    assert _paths(hits) == ["big.py", "other.py"]
    assert hits[0].chunk.start_line == 1


@needs_numpy
def test_top_k_limits_results(project, query_vector):
    _fill(project, [(_chunk(f"f{i}.py", "w"), [1.0, i, 0.0, 0.0]) for i in range(8)])
    assert len(search.search(project, "zzz", top_k=3)) == 3


@needs_numpy
def test_new_chunks_are_seen_by_the_next_search(project, query_vector):
    _fill(project, [(_chunk("a.py", "w"), [0.0, 1.0, 0.0, 0.0])])
    assert _paths(search.search(project, "zzz")) == ["a.py"]
    _fill(project, [(_chunk("b.py", "w"), [1.0, 0.0, 0.0, 0.0])])
    assert _paths(search.search(project, "zzz"))[0] == "b.py"


@needs_numpy
def test_empty_index_returns_nothing(project, query_vector):
    with IndexStore.open(project, create=True) as store:
        store.ensure_model("stub", DIM)
    assert search.search(project, "anything") == []


@needs_numpy
def test_missing_index_says_how_to_build_one(project):
    with pytest.raises(search.SearchUnavailable, match="codebud index"):
        search.search(project, "q")


@needs_numpy
def test_index_from_another_model_is_refused(project):
    with IndexStore.open(project, create=True) as store:
        store.ensure_model("other-model", DIM)
    with pytest.raises(ModelMismatch):
        search.search(project, "q")


def test_missing_numpy_names_the_extra(project, monkeypatch, query_vector):
    _fill(project, [(_chunk("a.py", "w"), [1.0, 0.0, 0.0, 0.0])])
    monkeypatch.setitem(sys.modules, "numpy", None)  # import numpy -> ImportError
    with pytest.raises(search.SearchUnavailable, match=r"codebud\[rag\]"):
        search.search(project, "q")


@needs_numpy
def test_lists_choose_which_rankings_are_fused(project, query_vector, monkeypatch):
    _fill(
        project,
        [
            (_chunk("vec.py", "unrelated"), [1.0, 0.0, 0.0, 0.0]),
            (_chunk("kw.py", "PolicyRejected raised here"), [0.0, 0.0, 1.0, 0.0]),
        ],
    )
    assert _paths(search.search(project, "PolicyRejected", top_k=1, lists=("vector",))) == [
        "vec.py"
    ]
    # Keyword-only search does not embed the query at all.
    monkeypatch.setattr(search, "embed_query", lambda _t: pytest.fail("embedded"))
    assert _paths(search.search(project, "PolicyRejected", top_k=1, lists=("keyword",))) == [
        "kw.py"
    ]
    with pytest.raises(ValueError, match="unknown"):
        search.search(project, "q", lists=("nonsense",))


@needs_numpy
def test_test_files_can_be_down_weighted_unless_the_query_is_about_tests(project, query_vector):
    _fill(
        project,
        [
            (_chunk("tests/test_a.py", "alpha check"), [1.0, 0.0, 0.0, 0.0]),
            (_chunk("src/a.py", "alpha code"), [0.9, 0.1, 0.0, 0.0]),
        ],
    )
    assert _paths(search.search(project, "zzz", top_k=1, test_weight=1.0)) == ["tests/test_a.py"]
    assert _paths(search.search(project, "zzz", top_k=1, test_weight=0.5)) == ["src/a.py"]
    assert _paths(search.search(project, "the zzz tests", top_k=1, test_weight=0.5)) == [
        "tests/test_a.py"
    ]


@pytest.mark.parametrize(
    "path, is_test",
    [
        ("tests/test_core.py", True),
        ("pkg/tests/helpers.py", True),
        ("test_x.py", True),
        ("src/x_test.py", True),
        ("src/codebud/core.py", False),
        ("docs/testing.texi", False),
        ("src/attest.py", False),
    ],
)
def test_which_paths_count_as_tests(path, is_test):
    assert search._is_test_path(path) is is_test
