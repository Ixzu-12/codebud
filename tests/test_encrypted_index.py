"""
tests/test_encrypted_index.py

The project index encrypted at rest (phase 10, D14g). With encryption on,
a new index is stored as index.sqlite3.age and used from a private copy
under $XDG_RUNTIME_DIR, removed on close. Building a new one needs no
passphrase; reading or refreshing one needs the key unlocked, and opening
it never asks. A plaintext index from before stays as it is until
`codebud key encrypt-existing` converts it.
"""

import os
import sys
from unittest.mock import patch

import pytest

pytest.importorskip("pyrage")

from codebud import crypto, paths  # noqa: E402
from codebud.cli import common  # noqa: E402
from codebud.rag import context, indexer  # noqa: E402
from codebud.rag.store import DB_NAME, SEALED_NAME, IndexStore, index_dir  # noqa: E402

pytestmark = pytest.mark.usefixtures("fast_passphrase")  # see conftest.py

PASSPHRASE = "s3cret"


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    directory = tmp_path / "run-user"
    directory.mkdir(mode=0o700)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(directory))
    return directory


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "proj"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "a.py").write_text("def alpha():\n    return 1\n")
    (root / "README.md").write_text("# Title\nintro\n")
    return root


@pytest.fixture
def no_prompt(monkeypatch):
    monkeypatch.setattr("getpass.getpass", lambda _p="": pytest.fail("asked for the passphrase"))


@pytest.fixture
def encrypted(monkeypatch, runtime):
    crypto.create_key(PASSPHRASE)
    crypto.lock()
    monkeypatch.setenv("CODEBUD_ENCRYPT", "1")


def _leftovers(runtime):
    return [p for p in runtime.rglob("*") if p.is_file()]


def _codebud(*argv):
    from codebud.cli import main as cli

    with patch.object(sys, "argv", ["codebud", *argv]):
        try:
            cli.main()
        except SystemExit as exc:
            return exc.code or 0
    return 0


def test_a_new_index_is_encrypted_without_the_passphrase(encrypted, project, runtime, no_prompt):
    report = indexer.index_project(project)
    assert report.indexed == 2
    directory = index_dir(project)
    assert (directory / SEALED_NAME).exists()
    assert not (directory / DB_NAME).exists()
    sealed = (directory / SEALED_NAME).read_bytes()
    assert crypto.is_encrypted(sealed) and b"alpha" not in sealed
    assert _leftovers(runtime) == []  # the decrypted copy is gone


def test_reading_needs_the_key_and_never_prompts(encrypted, project, no_prompt):
    indexer.index_project(project)
    with pytest.raises(crypto.CryptoError, match="locked"):
        IndexStore.open(project)
    crypto.unlock(PASSPHRASE)
    with IndexStore.open(project) as store:
        stats = store.stats()
    assert stats["files"] == 2 and stats["encrypted"]
    assert stats["path"].endswith(SEALED_NAME)


def test_refresh_keeps_it_encrypted_and_cleans_up(encrypted, project, runtime):
    indexer.index_project(project)
    crypto.unlock(PASSPHRASE)
    (project / "pkg" / "b.py").write_text("def beta():\n    return 2\n")
    report = indexer.index_project(project)
    assert (report.indexed, report.unchanged) == (1, 2)
    assert not (index_dir(project) / DB_NAME).exists()
    assert _leftovers(runtime) == []
    assert indexer.index_status(project)["files"] == 3


def test_retrieval_with_a_locked_key_returns_nothing(encrypted, project, no_prompt, monkeypatch):
    indexer.index_project(project)
    monkeypatch.chdir(project)
    monkeypatch.setattr(context, "project_root", lambda: project)
    assert context.retrieve("where is alpha defined") == []


def test_without_a_runtime_dir_it_refuses(monkeypatch, project):
    crypto.create_key(PASSPHRASE)
    monkeypatch.setenv("CODEBUD_ENCRYPT", "1")
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    with pytest.raises(crypto.CryptoError, match="XDG_RUNTIME_DIR"):
        indexer.index_project(project)
    assert not (index_dir(project) / DB_NAME).exists()  # nothing decrypted to disk


def test_a_plaintext_index_stays_plaintext(project, runtime, monkeypatch):
    indexer.index_project(project)
    crypto.create_key(PASSPHRASE)
    monkeypatch.setenv("CODEBUD_ENCRYPT", "1")
    (project / "pkg" / "b.py").write_text("def beta():\n    return 2\n")
    indexer.index_project(project)
    assert (index_dir(project) / DB_NAME).exists()
    assert not (index_dir(project) / SEALED_NAME).exists()


def test_encrypt_existing_converts_a_plaintext_index(project, runtime, monkeypatch, no_prompt):
    indexer.index_project(project)
    db = index_dir(project) / DB_NAME
    crypto.create_key(PASSPHRASE)
    crypto.lock()
    assert _codebud("key", "encrypt-existing", "--yes") == 0
    assert not db.exists() and (index_dir(project) / SEALED_NAME).exists()
    (backup,) = paths.data_dir().glob("plaintext-backup-*")
    assert (backup / db.relative_to(paths.data_dir())).exists()
    crypto.unlock(PASSPHRASE)
    assert indexer.index_status(project)["files"] == 2


def test_index_status_cli_unlocks(encrypted, project, monkeypatch, capsys):
    indexer.index_project(project)
    monkeypatch.setattr("getpass.getpass", lambda _p="": PASSPHRASE)
    assert _codebud("index", str(project), "--status") == 0
    out = capsys.readouterr().out
    assert "yes (age)" in out and SEALED_NAME in out


def test_rebuild_needs_no_passphrase(encrypted, project, no_prompt):
    indexer.index_project(project)
    assert _codebud("index", str(project), "--rebuild") == 0
    assert (index_dir(project) / SEALED_NAME).exists()


# --- unlocking at startup ---------------------------------------------------


def test_startup_unlock_asks_once(encrypted, project, monkeypatch):
    indexer.index_project(project)
    monkeypatch.chdir(project)
    monkeypatch.setattr(indexer, "project_root", lambda *_: project)
    asked = []
    monkeypatch.setattr("getpass.getpass", lambda _p="": asked.append(1) or PASSPHRASE)
    common.unlock_project_index()
    assert crypto.is_unlocked() and asked == [1]


def test_startup_unlock_failure_turns_context_off(encrypted, project, monkeypatch, capsys):
    indexer.index_project(project)
    monkeypatch.setattr(indexer, "project_root", lambda *_: project)
    monkeypatch.setattr(crypto, "_terminal_available", lambda: False)
    monkeypatch.setenv("CODEBUD_RAG", "1")
    common.unlock_project_index()
    assert os.environ["CODEBUD_RAG"] == "0"
    assert "Project context off" in capsys.readouterr().err


def test_startup_unlock_not_needed_without_an_encrypted_index(project, monkeypatch, no_prompt):
    monkeypatch.setattr(indexer, "project_root", lambda *_: project)
    common.unlock_project_index()
    assert not crypto.is_unlocked()


def test_toolbar_shows_a_locked_index(encrypted, project):
    from codebud.cli.index_hints import rag_indicator

    indexer.index_project(project)

    class Agent:
        def get_rag(self):
            return True

    assert rag_indicator(Agent(), project) == "RAG locked"
    crypto.unlock(PASSPHRASE)
    assert rag_indicator(Agent(), project) == "RAG"


# --- the decrypted copy (audit M2, L2) --------------------------------------


def test_a_decrypted_copy_keeps_sqlite_temp_files_in_memory(encrypted, project):
    indexer.index_project(project)
    crypto.unlock(PASSPHRASE)
    with IndexStore.open(project) as store:
        assert store._db.execute("PRAGMA temp_store").fetchone()[0] == 2  # MEMORY


def test_a_plaintext_index_keeps_the_default_temp_store(project, runtime):
    indexer.index_project(project)
    with IndexStore.open(project) as store:
        assert store._db.execute("PRAGMA temp_store").fetchone()[0] == 0


def test_the_encrypted_file_alone_holds_every_write(encrypted, project, tmp_path):
    import sqlite3

    indexer.index_project(project)
    crypto.unlock(PASSPHRASE)
    plain = tmp_path / "check.sqlite3"
    plain.write_bytes(crypto.decrypt((index_dir(project) / SEALED_NAME).read_bytes()))
    con = sqlite3.connect(plain)  # no -wal file beside it
    try:
        assert con.execute("SELECT COUNT(*) FROM files").fetchone()[0] == 2
    finally:
        con.close()
