"""
tests/test_rag_hints.py

Keeping the project index fresh without auto-embedding (phase 9, D5 and
D13d): a cheap staleness check, the toolbar indicator, and one-line hints
at the right moments.
"""

import os
import subprocess
import time

import pytest

from codebud.cli import chat_input
from codebud.cli.index_hints import IndexHints, rag_indicator
from codebud.rag import indexer


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
    (root / "a.py").write_text("x = 1\n")
    (root / "b.py").write_text("y = 2\n")
    return root


def _later(path):
    """Give ``path`` a modification time after the index was written."""
    future = time.time() + 5
    os.utime(path, (future, future))


class _Agent:
    def __init__(self, rag=True):
        self._rag = rag

    def get_rag(self):
        return self._rag


# --- quick_status -----------------------------------------------------------


def test_quick_status_without_an_index(repo):
    assert indexer.quick_status(repo) is None


def test_quick_status_right_after_indexing(repo):
    indexer.index_project(repo)
    assert indexer.quick_status(repo) == {"changed": 0, "new": 0, "deleted": 0}


def test_quick_status_sees_changed_new_and_deleted_files(repo):
    indexer.index_project(repo)
    _later(repo / "a.py")
    (repo / "b.py").unlink()
    (repo / "c.py").write_text("z = 3\n")
    (repo / ".env").write_text("TOKEN=x\n")  # never indexed, so never "new"
    assert indexer.quick_status(repo) == {"changed": 1, "new": 1, "deleted": 1}


# --- toolbar ----------------------------------------------------------------


def test_toolbar_shows_the_rag_state():
    text = chat_input.toolbar_text("ask", "m", 100, 1000, "RAG stale")
    assert "context 10% · RAG stale" in text
    assert "RAG" not in chat_input.toolbar_text("ask", "m", 100, 1000)


def test_indicator_states(repo):
    assert rag_indicator(_Agent(), repo) == ""  # no index: nothing to show
    indexer.index_project(repo)
    assert rag_indicator(_Agent(), repo) == "RAG"
    assert rag_indicator(_Agent(rag=False), repo) == "RAG off"
    _later(repo / "a.py")
    assert rag_indicator(_Agent(), repo) == "RAG stale"


# --- hints ------------------------------------------------------------------


def test_start_suggests_indexing_a_git_project_without_one(repo):
    hint = IndexHints(_Agent(), repo).at_start()
    assert "/index" in hint and "CODEBUD_RAG_HINTS=0" in hint


def test_no_suggestion_outside_a_git_project(tmp_path):
    (tmp_path / "x.py").write_text("x = 1\n")
    assert IndexHints(_Agent(), tmp_path).at_start() is None


def test_start_reports_a_stale_index(repo):
    indexer.index_project(repo)
    assert IndexHints(_Agent(), repo).at_start() is None
    _later(repo / "a.py")
    (repo / "c.py").write_text("z = 3\n")
    hint = IndexHints(_Agent(), repo).at_start()
    assert "1 changed, 1 new, 0 deleted" in hint and "/index" in hint


def test_changed_files_hint_once_until_the_next_index(repo):
    indexer.index_project(repo)
    hints = IndexHints(_Agent(), repo)
    assert "/index" in hints.after_changes()
    assert hints.after_changes() is None
    hints.refreshed()
    assert "/index" in hints.after_changes()


def test_changed_files_hint_needs_an_index(repo):
    assert IndexHints(_Agent(), repo).after_changes() is None


def test_hints_respect_the_switches(repo, monkeypatch):
    assert IndexHints(_Agent(rag=False), repo).at_start() is None
    monkeypatch.setenv("CODEBUD_RAG_HINTS", "0")
    assert IndexHints(_Agent(), repo).at_start() is None
    indexer.index_project(repo)
    assert IndexHints(_Agent(), repo).after_changes() is None
