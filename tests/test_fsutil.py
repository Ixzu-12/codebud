"""
tests/test_fsutil.py

atomic_write_text() must replace a file's contents all-or-nothing: an
interrupted write leaves the previous contents and no stray temp files.
"""

import os

import pytest

from agent import fsutil


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
