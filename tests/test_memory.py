"""
tests/test_memory.py

Robustness tests for MemoryStore persistence. History is stored as JSON
Lines (one snapshot per line, append-only); corrupt lines, interrupted
writes, concurrent writers and the legacy entries.json format must never
silently lose history.
"""

import json
import warnings
from datetime import datetime, timedelta

from agent import memory as memory_mod


def _store(tmp_path, monkeypatch):
    path = tmp_path / "entries.jsonl"
    monkeypatch.setattr(memory_mod, "MEMORY_PATH", str(path))
    return path


def _lines(path):
    return path.read_text().splitlines()


def _cmds(store):
    return [e["data"]["step"]["cmd"] for e in store.history]


def test_each_snapshot_is_one_json_line(tmp_path, monkeypatch):
    path = _store(tmp_path, monkeypatch)
    store = memory_mod.MemoryStore()
    store.add_snapshot({"tool": "shell", "cmd": "a"}, {"returncode": 0})
    store.add_snapshot({"tool": "shell", "cmd": "b"}, {"returncode": 1})

    lines = _lines(path)
    assert len(lines) == 2
    assert [json.loads(line)["data"]["step"]["cmd"] for line in lines] == ["a", "b"]


def test_append_leaves_existing_lines_untouched(tmp_path, monkeypatch):
    path = _store(tmp_path, monkeypatch)
    old = '{"timestamp":  "t0",  "data": {"step": {"cmd": "old"}, "result": {}}}\n'
    path.write_text(old)

    memory_mod.MemoryStore().add_snapshot({"cmd": "new"}, {"returncode": 0})

    assert path.read_text().startswith(old)


def test_bad_line_is_skipped_with_warning_and_kept_on_disk(tmp_path, monkeypatch, capsys):
    path = _store(tmp_path, monkeypatch)
    good = json.dumps({"timestamp": "t", "data": {"step": {"cmd": "a"}, "result": {}}})
    path.write_text(f"{good}\nnot json at all\n{good}\n")

    store = memory_mod.MemoryStore()

    assert _cmds(store) == ["a", "a"]
    assert "not json at all" in path.read_text()
    assert "skipped 1" in capsys.readouterr().err


def test_cut_off_last_line_does_not_swallow_next_entry(tmp_path, monkeypatch):
    path = _store(tmp_path, monkeypatch)
    good = json.dumps({"timestamp": "t", "data": {"step": {"cmd": "a"}, "result": {}}})
    path.write_text(f'{good}\n{{"timestamp": "t", "da')  # write killed mid-line

    memory_mod.MemoryStore().add_snapshot({"cmd": "b"}, {"returncode": 0})

    assert _cmds(memory_mod.MemoryStore()) == ["a", "b"]


def test_two_stores_do_not_lose_each_others_entries(tmp_path, monkeypatch):
    _store(tmp_path, monkeypatch)
    first = memory_mod.MemoryStore()
    second = memory_mod.MemoryStore()  # e.g. a second codebud process

    first.add_snapshot({"tool": "shell", "cmd": "a"}, {"returncode": 0})
    second.add_snapshot({"tool": "shell", "cmd": "b"}, {"returncode": 0})

    assert _cmds(memory_mod.MemoryStore()) == ["a", "b"]


def test_snapshot_timestamp_is_timezone_aware_utc(tmp_path, monkeypatch):
    path = _store(tmp_path, monkeypatch)
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        memory_mod.MemoryStore().add_snapshot({"tool": "shell"}, {"returncode": 0})

    ts = datetime.fromisoformat(json.loads(_lines(path)[0])["timestamp"])
    assert ts.utcoffset() == timedelta(0)


def test_legacy_json_array_is_migrated(tmp_path, monkeypatch):
    path = _store(tmp_path, monkeypatch)
    legacy = tmp_path / "entries.json"
    entries = [
        {"timestamp": "t1", "data": {"step": {"cmd": "a"}, "result": {}}},
        {"timestamp": "t2", "data": {"step": {"cmd": "b"}, "result": {}}},
    ]
    legacy.write_text(json.dumps(entries, indent=2))

    store = memory_mod.MemoryStore()

    assert store.history == entries
    assert [json.loads(line) for line in _lines(path)] == entries
    assert not legacy.exists()
    assert json.loads((tmp_path / "entries.json.migrated").read_text()) == entries


def test_corrupt_legacy_file_is_preserved_not_migrated(tmp_path, monkeypatch, capsys):
    path = _store(tmp_path, monkeypatch)
    legacy = tmp_path / "entries.json"
    legacy.write_text('[{"timestamp": "x", "data": {')  # truncated JSON

    store = memory_mod.MemoryStore()
    store.add_snapshot({"cmd": "new"}, {"returncode": 0})

    backups = list(tmp_path.glob("entries.json.corrupt-*"))
    assert len(backups) == 1
    assert backups[0].read_text() == '[{"timestamp": "x", "data": {'
    assert len(_lines(path)) == 1
    assert "corrupt" in capsys.readouterr().err.lower()
