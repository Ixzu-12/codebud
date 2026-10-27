"""
tests/test_private_files.py

Codebud's data is readable only by its owner (phase 8, Part B1): every
directory it creates is 0700 and every file 0600, whatever the umask, and
`codebud doctor` warns about anything looser.
"""

import os
import stat

import pytest

from codebud import fsutil, paths


def _mode(path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


@pytest.fixture(autouse=True)
def permissive_umask():
    """Run with umask 022 (typical), so 0644/0755 would show up if used."""
    old = os.umask(0o022)
    yield
    os.umask(old)


def test_private_dir_makes_every_missing_level_private(tmp_path):
    target = tmp_path / "a" / "b" / "c"
    fsutil.private_dir(target)
    for level in (tmp_path / "a", tmp_path / "a" / "b", target):
        assert _mode(level) == 0o700, level
    fsutil.private_dir(target)  # existing: fine


def test_create_private_makes_a_0600_file_and_keeps_contents(tmp_path):
    path = tmp_path / "f"
    fsutil.create_private(path)
    assert _mode(path) == 0o600
    path.write_text("kept")
    fsutil.create_private(path)
    assert path.read_text() == "kept"


def test_memory_log_and_its_directories_are_private():
    from codebud.memory import MemoryStore

    MemoryStore().add_snapshot({"id": "s", "tool": "command"}, {"returncode": 0})
    memory_dir = paths.data_dir() / "memory"
    assert _mode(paths.data_dir()) == 0o700
    assert _mode(memory_dir) == 0o700
    for f in memory_dir.iterdir():
        assert _mode(f) == 0o600, f


def test_session_files_and_locks_are_private():
    from codebud import sessions

    session = sessions.Session.create(name="private")
    session.save([{"role": "user", "content": "secret"}], model="m", mode="ask")
    session_dir = paths.data_dir() / "sessions"
    assert _mode(session_dir) == 0o700
    files = list(session_dir.iterdir())
    assert files
    for f in files:
        assert _mode(f) == 0o600, f
    session.close()


def test_migrated_legacy_files_are_private(tmp_path, monkeypatch):
    legacy = tmp_path / "legacy-data"
    (legacy / "memory").mkdir(parents=True)
    old = legacy / "memory" / "entries.jsonl"
    old.write_text("{}\n")
    os.chmod(old, 0o644)
    monkeypatch.setattr(paths, "LEGACY_DATA_DIR", legacy)
    paths.migrate_legacy_data()
    copied = paths.data_dir() / "memory" / "entries.jsonl"
    assert copied.read_text() == "{}\n"
    assert _mode(copied) == 0o600
    assert _mode(old) == 0o644  # the original is left exactly as it was


def test_doctor_warns_about_readable_data(monkeypatch):
    from codebud.cli import doctor

    fsutil.private_dir(paths.data_dir() / "memory")
    log = paths.data_dir() / "memory" / "entries.jsonl"
    log.write_text("{}\n")
    os.chmod(log, 0o600)
    passed, label, _hint = doctor.check_private_data()
    assert passed is True and "private" in label

    os.chmod(paths.data_dir() / "memory", 0o755)
    os.chmod(log, 0o644)
    passed, label, hint = doctor.check_private_data()
    assert passed is None  # a warning, not a failure
    assert "2" in label
    assert f"chmod -R go-rwx {paths.data_dir()}" in hint


def test_doctor_without_any_data_yet():
    from codebud.cli import doctor

    passed, _label, _hint = doctor.check_private_data()
    assert passed is True
