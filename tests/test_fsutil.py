"""
tests/test_fsutil.py

atomic_write_text() must replace a file's contents all-or-nothing: an
interrupted write leaves the previous contents and no stray temp files.
"""

import os
import stat

import pytest

from codebud import fsutil


def test_writes_new_file(tmp_path):
    path = tmp_path / "f.json"
    fsutil.atomic_write_text(str(path), "hello\n")
    assert path.read_text() == "hello\n"


def test_replaces_existing_contents(tmp_path):
    path = tmp_path / "f.json"
    path.write_text("old")
    fsutil.atomic_write_text(str(path), "new")
    assert path.read_text() == "new"


def test_interrupted_write_keeps_previous_contents(tmp_path, monkeypatch):
    path = tmp_path / "f.json"
    path.write_text("old")

    def die(*args):
        raise KeyboardInterrupt

    monkeypatch.setattr(fsutil.os, "replace", die)
    with pytest.raises(KeyboardInterrupt):
        fsutil.atomic_write_text(str(path), "new")

    assert path.read_text() == "old"
    assert os.listdir(tmp_path) == ["f.json"]


def test_writes_bytes_privately(tmp_path):
    path = tmp_path / "f.age"
    path.write_bytes(b"old")
    os.chmod(path, 0o644)
    fsutil.atomic_write_bytes(path, b"\x00new")
    assert path.read_bytes() == b"\x00new"
    assert os.stat(path).st_mode & 0o777 == 0o600


def test_the_directory_is_synced_after_the_rename(tmp_path, monkeypatch):
    synced = []  # for each fsync: was it a directory?
    real_fsync = os.fsync

    def spy(fd):
        synced.append(stat.S_ISDIR(os.fstat(fd).st_mode))
        return real_fsync(fd)

    monkeypatch.setattr(fsutil.os, "fsync", spy)
    fsutil.atomic_write_bytes(tmp_path / "key", b"x")
    assert synced == [False, True]  # the file, then its directory
