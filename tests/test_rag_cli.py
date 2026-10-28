"""
tests/test_rag_cli.py

`codebud index` (phase 9, B4): build/refresh, --status, --rebuild, --drop.
Embeddings come from the offline stub (tests/conftest.py).
"""

import sys
from unittest.mock import patch

import pytest

from codebud.rag import indexer
from codebud.rag.store import IndexStore, index_dir
from codebud.tools import embeddings


def _codebud(*argv):
    from codebud.cli import main as cli

    with patch.object(sys, "argv", ["codebud", *argv]):
        try:
            cli.main()
        except SystemExit as exc:
            return exc.code or 0
    return 0


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "app.py").write_text("def main():\n    return 0\n")
    (root / ".env").write_text("TOKEN=abc\n")
    monkeypatch.chdir(root)
    return root


def test_index_builds_and_reports(project, capsys):
    assert _codebud("--no-progress", "index") == 0
    out = capsys.readouterr().out
    assert "Indexed 1 file (1 chunk)" in out
    assert "Skipped: 1 secret" in out
    assert IndexStore.open(project) is not None


def test_index_takes_a_path(project, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert _codebud("--no-progress", "index", str(project)) == 0
    assert index_dir(project).is_dir()


def test_second_run_says_up_to_date(project, capsys):
    _codebud("--no-progress", "index")
    capsys.readouterr()
    _codebud("--no-progress", "index")
    assert "Index is up to date (1 file)" in capsys.readouterr().out


def test_status_without_an_index(project, capsys):
    assert _codebud("index", "--status") == 0
    out = capsys.readouterr().out
    assert "No index" in out and "codebud index" in out


def test_status_shows_model_counts_and_staleness(project, capsys):
    _codebud("--no-progress", "index")
    (project / "app.py").write_text("def main():\n    return 1\n")
    (project / "new.py").write_text("x = 1\n")
    capsys.readouterr()

    assert _codebud("index", "--status") == 0
    out = capsys.readouterr().out
    assert "stub" in out  # model
    assert "1 file, 1 chunk" in out
    assert "1 changed, 1 new, 0 deleted" in out
    assert "codebud index" in out  # how to refresh


def test_unavailable_model_is_a_clear_error(project, monkeypatch, capsys):
    monkeypatch.delenv("CODEBUD_EMBED_STUB", raising=False)

    def down(*_a, **_k):
        raise embeddings.requests.ConnectionError("refused")

    monkeypatch.setattr(embeddings.requests, "post", down)
    assert _codebud("--no-progress", "index") == 1
    err = capsys.readouterr().err
    assert "Embedding model unavailable" in err and "ollama pull" in err


def test_model_mismatch_points_at_rebuild(project, capsys):
    with IndexStore.open(project, create=True) as store:
        store.ensure_model("other-model", 16)
    assert _codebud("--no-progress", "index") == 1
    assert "--rebuild" in capsys.readouterr().err

    assert _codebud("--no-progress", "index", "--rebuild") == 0
    with IndexStore.open(project) as store:
        assert store.stats()["model"] == "stub"


def test_drop_needs_yes_when_not_interactive(project, monkeypatch, capsys):
    _codebud("--no-progress", "index")
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    assert _codebud("index", "--drop") == 1
    assert "--yes" in capsys.readouterr().err
    assert index_dir(project).is_dir()


def test_drop_with_yes_lists_what_it_removes(project, capsys):
    _codebud("--no-progress", "index")
    capsys.readouterr()
    assert _codebud("index", "--drop", "--yes") == 0
    out = capsys.readouterr().out
    assert "index.sqlite3" in out
    assert not index_dir(project).exists()


def test_drop_asks_on_a_terminal(project, monkeypatch, capsys):
    _codebud("--no-progress", "index")
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt: "n")
    assert _codebud("index", "--drop") == 1
    assert index_dir(project).is_dir()

    monkeypatch.setattr("builtins.input", lambda _prompt: "y")
    assert _codebud("index", "--drop") == 0
    assert not index_dir(project).exists()


def test_drop_without_an_index(project, capsys):
    assert _codebud("index", "--drop", "--yes") == 0
    assert "No index" in capsys.readouterr().out


def test_options_are_exclusive(project, capsys):
    with pytest.raises(SystemExit):
        from codebud.cli.parser import build_parser

        build_parser().parse_args(["index", "--status", "--drop"])


def test_index_runs_from_a_subdirectory_of_a_git_project(tmp_path, monkeypatch):
    import subprocess

    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / "src" / "m.py").write_text("x = 1\n")
    subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
    monkeypatch.chdir(root / "src")
    assert indexer.project_root() == root.resolve()
    assert _codebud("--no-progress", "index") == 0
    assert index_dir(root).is_dir()
