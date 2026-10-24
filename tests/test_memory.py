"""
tests/test_memory.py

Robustness tests for MemoryStore persistence: corrupt files, interrupted
writes, and concurrent writers must never silently lose history.
"""

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
