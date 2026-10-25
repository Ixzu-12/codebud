"""
tests/test_sessions.py

Session checkpoints (backlog item 8): each chat conversation is saved to
data/sessions/<id>.json so it can be resumed later. Several codebud chats
may run at once, so IDs must never collide and a session that is open in
one chat must not be opened by another.
"""

import json
import re

import pytest

from codebud import sessions


@pytest.fixture(autouse=True)
def sessions_dir(tmp_path, monkeypatch):
    d = tmp_path / "sessions"
    monkeypatch.setattr(sessions, "SESSIONS_DIR", str(d))
    return d


_HISTORY = [
    {"role": "user", "content": "list the python files"},
    {"role": "assistant", "content": "planned: command(list python files)"},
]


def test_create_reserves_a_file_with_timestamp_and_random_id(sessions_dir):
    s = sessions.Session.create()
    try:
        assert re.fullmatch(r"\d{8}-\d{6}-[0-9a-f]{4}", s.id)
        data = json.loads((sessions_dir / f"{s.id}.json").read_text())
        assert data["version"] == sessions.FORMAT_VERSION
        assert data["id"] == s.id
        assert data["history"] == []
    finally:
        s.close()


def test_same_second_collision_gets_a_new_id(monkeypatch):
    suffixes = iter(["aaaa", "aaaa", "bbbb"])
    monkeypatch.setattr(sessions, "_random_suffix", lambda: next(suffixes))
    monkeypatch.setattr(sessions, "_timestamp", lambda: "20261025-093012")

    first = sessions.Session.create()
    second = sessions.Session.create()
    try:
        assert first.id == "20261025-093012-aaaa"
        assert second.id == "20261025-093012-bbbb"
    finally:
        first.close()
        second.close()


def test_save_then_open_round_trips_history_and_model():
    s = sessions.Session.create()
    s.save(_HISTORY, model="m:latest")
    s.close()

    reopened = sessions.Session.open(s.id)
    try:
        assert reopened.history == _HISTORY
        assert reopened.model == "m:latest"
    finally:
        reopened.close()


def test_open_by_name():
    s = sessions.Session.create()
    s.set_name("refactor-memory")
    s.save(_HISTORY, model="m")
    s.close()

    reopened = sessions.Session.open("refactor-memory")
    try:
        assert reopened.id == s.id
        assert reopened.name == "refactor-memory"
    finally:
        reopened.close()


def test_name_must_be_unique_and_well_formed():
    a = sessions.Session.create()
    b = sessions.Session.create()
    try:
        a.set_name("work")
        a.save(_HISTORY, model="m")
        with pytest.raises(sessions.SessionError, match="already used"):
            b.set_name("work")
        with pytest.raises(sessions.SessionError, match="name"):
            b.set_name("bad name/with slash")
    finally:
        a.close()
        b.close()


def test_session_open_in_another_chat_is_refused():
    s = sessions.Session.create()
    try:
        with pytest.raises(sessions.SessionInUseError):
            sessions.Session.open(s.id)
    finally:
        s.close()
    sessions.Session.open(s.id).close()  # free again once closed


def test_unknown_session_is_reported():
    with pytest.raises(sessions.SessionNotFoundError):
        sessions.Session.open("no-such-session")


def test_list_sessions_newest_first_with_summary(monkeypatch):
    stamps = iter(["2026-10-25T09:00:00+00:00", "2026-10-25T10:00:00+00:00"])
    monkeypatch.setattr(sessions, "_now_iso", lambda: next(stamps, "2026-10-25T11:00:00+00:00"))

    old = sessions.Session.create()
    old.save(_HISTORY, model="m")
    old.close()
    new = sessions.Session.create()
    new.save(_HISTORY[:1], model="m")

    try:
        listed = sessions.list_sessions()
        assert [e["id"] for e in listed] == [new.id, old.id]
        assert listed[0]["in_use"] is True
        assert listed[1]["in_use"] is False
        assert listed[1]["turns"] == 2
        assert listed[1]["first_request"] == "list the python files"
    finally:
        new.close()


def test_most_recent_available_skips_sessions_in_use(monkeypatch):
    stamps = iter(["2026-10-25T09:00:00+00:00", "2026-10-25T10:00:00+00:00"])
    monkeypatch.setattr(sessions, "_now_iso", lambda: next(stamps, "2026-10-25T11:00:00+00:00"))

    older = sessions.Session.create()
    older.save(_HISTORY, model="m")
    older.close()
    busy = sessions.Session.create()
    busy.save(_HISTORY, model="m")
    try:
        assert sessions.most_recent_available() == older.id
    finally:
        busy.close()


def test_corrupt_session_file_is_reported_and_skipped_in_listing(sessions_dir, capsys):
    s = sessions.Session.create()
    s.save(_HISTORY, model="m")
    s.close()
    (sessions_dir / f"{s.id}.json").write_text('{"version": 1, "id": ')

    with pytest.raises(sessions.SessionError, match="corrupt"):
        sessions.Session.open(s.id)
    assert sessions.list_sessions() == []


def test_newer_format_version_is_refused(sessions_dir):
    s = sessions.Session.create()
    s.close()
    path = sessions_dir / f"{s.id}.json"
    data = json.loads(path.read_text())
    data["version"] = sessions.FORMAT_VERSION + 1
    path.write_text(json.dumps(data))

    with pytest.raises(sessions.SessionError, match="newer"):
        sessions.Session.open(s.id)


def test_session_held_by_another_process_is_refused(sessions_dir):
    """Two separate codebud processes: the second cannot open a busy session."""
    import subprocess
    import sys

    s = sessions.Session.create()
    s.close()
    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import sys; from codebud import sessions; "
            f"sessions.SESSIONS_DIR = {str(sessions_dir)!r}; "
            f"s = sessions.Session.open({s.id!r}); "
            "print('held', flush=True); sys.stdin.read()",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout.readline().strip() == "held"
        with pytest.raises(sessions.SessionInUseError):
            sessions.Session.open(s.id)
        assert sessions.list_sessions()[0]["in_use"] is True
    finally:
        holder.stdin.close()  # holder exits, releasing its lock
        holder.wait(timeout=10)
    sessions.Session.open(s.id).close()
