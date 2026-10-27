"""
tests/test_rag_store.py

The project index store (phase 9, B3): one private SQLite file per
project, chunks with unit-length float32 vectors and a keyword index, and
one embedding model per index (A3).
"""

import math
import stat
from array import array

import pytest

from codebud import paths
from codebud.rag.chunker import Chunk
from codebud.rag.store import IndexStore, ModelMismatch, index_dir


def _chunk(path="a.py", text="def alpha():\n    return 1", symbol="alpha", start=1):
    return Chunk(path, start, start + text.count("\n"), symbol, "python", text)


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    return root


@pytest.fixture
def store(project):
    with IndexStore.open(project, create=True) as s:
        s.ensure_model("test-embed", 3)
        yield s


def test_index_lives_privately_in_the_data_dir(project, store):
    directory = index_dir(project)
    assert directory.parent == paths.data_dir() / "index"
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    assert stat.S_IMODE(store.db_path.stat().st_mode) == 0o600
    assert store.db_path.parent == directory


def test_each_project_gets_its_own_index(tmp_path):
    (tmp_path / "one").mkdir()
    (tmp_path / "two").mkdir()
    assert index_dir(tmp_path / "one") != index_dir(tmp_path / "two")
    assert index_dir(tmp_path / "one") == index_dir(tmp_path / "one" / ".." / "one")


def test_open_without_create_reports_a_missing_index(project):
    assert IndexStore.open(project) is None
    assert not index_dir(project).exists()


def test_index_refuses_a_different_embedding_model(project, store):
    """Vectors from two models are not comparable (A3)."""
    store.ensure_model("test-embed", 3)  # same model: fine
    with pytest.raises(ModelMismatch, match="--rebuild"):
        store.ensure_model("other-embed", 3)
    with pytest.raises(ModelMismatch):
        store.ensure_model("test-embed", 768)


def test_replace_file_stores_chunks_with_unit_vectors(store):
    store.replace_file("a.py", "hash1", [(_chunk(), [3.0, 4.0, 0.0])])
    assert store.file_hashes() == {"a.py": "hash1"}

    [(chunk_id, blob)] = list(store.vectors())
    vector = array("f", blob)
    assert list(vector) == pytest.approx([0.6, 0.8, 0.0])
    row = store.chunk(chunk_id)
    assert (row.path, row.start_line, row.end_line, row.symbol) == ("a.py", 1, 2, "alpha")
    assert row.text == "def alpha():\n    return 1"


def test_replace_file_drops_the_old_chunks(store):
    store.replace_file("a.py", "h1", [(_chunk(text="def old(): pass", symbol="old"), [1, 0, 0])])
    store.replace_file("a.py", "h2", [(_chunk(text="def new(): pass", symbol="new"), [0, 1, 0])])
    assert store.file_hashes() == {"a.py": "h2"}
    assert [store.chunk(i).symbol for i, _ in store.vectors()] == ["new"]
    assert store.keyword_search("old") == []


def test_wrong_vector_length_is_rejected(store):
    with pytest.raises(ValueError, match="3"):
        store.replace_file("a.py", "h", [(_chunk(), [1.0, 0.0])])
    assert store.file_hashes() == {}  # nothing half-written


def test_keyword_search_finds_identifiers_and_symbols(store):
    if not store.has_fts:
        pytest.skip("SQLite built without FTS5")
    store.replace_file("a.py", "h", [(_chunk(), [1, 0, 0])])
    store.replace_file(
        "b.py",
        "h",
        [(_chunk("b.py", "def compact_history(self):\n    pass", "compact_history"), [0, 1, 0])],
    )
    hits = store.keyword_search("compact_history")
    assert [store.chunk(i).path for i in hits] == ["b.py"]


def test_remove_files_deletes_their_chunks(store):
    store.replace_file("a.py", "h", [(_chunk(), [1, 0, 0])])
    store.replace_file("b.py", "h", [(_chunk("b.py"), [0, 1, 0])])
    store.remove_files(["a.py"])
    assert store.file_hashes() == {"b.py": "h"}
    assert {store.chunk(i).path for i, _ in store.vectors()} == {"b.py"}


def test_index_survives_reopening_and_reports_stats(project):
    with IndexStore.open(project, create=True) as s:
        s.ensure_model("test-embed", 3)
        s.replace_file("a.py", "h", [(_chunk(), [1, 0, 0]), (_chunk(start=5), [0, 0, 1])])
    with IndexStore.open(project) as s:
        stats = s.stats()
    assert stats["root"] == str(project.resolve())
    assert (stats["model"], stats["dim"]) == ("test-embed", 3)
    assert (stats["files"], stats["chunks"]) == (1, 2)
    assert stats["updated"]


def test_zero_vector_is_stored_as_is(store):
    store.replace_file("a.py", "h", [(_chunk(), [0.0, 0.0, 0.0])])
    [(_id, blob)] = list(store.vectors())
    assert not any(math.isnan(x) for x in array("f", blob))
