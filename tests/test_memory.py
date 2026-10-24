"""
tests/test_memory.py

Robustness tests for MemoryStore persistence: corrupt files, interrupted
writes, and concurrent writers must never silently lose history.
"""

import json

import pytest

from agent import memory as memory_mod


def _store(tmp_path, monkeypatch):
    path = tmp_path / "entries.json"
    monkeypatch.setattr(memory_mod, "MEMORY_PATH", str(path))
    return path


def test_corrupt_file_is_preserved_not_overwritten(tmp_path, monkeypatch, capsys):
    path = _store(tmp_path, monkeypatch)
    path.write_text('[{"timestamp": "x", "data": {')  # truncated JSON

    store = memory_mod.MemoryStore()
    store.add_snapshot({"tool": "shell"}, {"returncode": 0})

    backups = list(tmp_path.glob("entries.json.corrupt-*"))
    assert len(backups) == 1
    assert backups[0].read_text() == '[{"timestamp": "x", "data": {'
    assert "corrupt" in capsys.readouterr().err.lower()


def test_interrupted_write_keeps_previous_file(tmp_path, monkeypatch):
    path = _store(tmp_path, monkeypatch)
    store = memory_mod.MemoryStore()
    store.add_snapshot({"tool": "shell"}, {"returncode": 0})
    before = path.read_text()

    def dump_then_die(obj, fp, **kwargs):
        fp.write('[{"timestamp": ')
        raise KeyboardInterrupt

    monkeypatch.setattr(memory_mod.json, "dump", dump_then_die)
    with pytest.raises(KeyboardInterrupt):
        store.add_snapshot({"tool": "shell"}, {"returncode": 1})

    assert path.read_text() == before
    assert json.loads(path.read_text())[0]["data"]["result"]["returncode"] == 0
    assert [p.name for p in tmp_path.iterdir()] == ["entries.json"]
