"""
codebud/sessions.py

Chat session checkpoints. Each conversation is saved to

    $XDG_DATA_HOME/codebud/sessions/<id>.json   (see agent/paths.py)

after every turn, so it can be resumed later with `codebud chat --resume`.
This is separate from MemoryStore, which records executed *steps*; a
session records the *conversation*.

Several codebud chats may run at the same time, so:

- IDs are a UTC timestamp plus a random suffix (20261025-093012-7f3a), and
  the file is reserved with O_EXCL, so two chats can never share an ID.
- A chat holds an exclusive flock(2) on <id>.lock while the session is
  open; opening a session that another chat has open is refused.
- Every save is atomic (temp file + rename), so a crash never leaves a
  half-written session.
"""

import fcntl
import json
import logging
import os
import re
import secrets
from datetime import datetime, timezone
from typing import Any

from . import paths
from .fsutil import atomic_write_text

logger = logging.getLogger(__name__)

# Overridable for tests; None means the default location in the data dir.
SESSIONS_DIR: str | None = None
FORMAT_VERSION = 1

_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")


class SessionError(Exception):
    """A session could not be created, opened or saved."""


class SessionInUseError(SessionError):
    """The session is open in another codebud chat."""


class SessionNotFoundError(SessionError):
    """No session matches the given ID or name."""


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def _random_suffix() -> str:
    return secrets.token_hex(2)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _dir() -> str:
    """Return the sessions directory (SESSIONS_DIR, else the data dir's)."""
    return SESSIONS_DIR or str(paths.data_dir() / "sessions")


def _path(session_id: str) -> str:
    return os.path.join(_dir(), f"{session_id}.json")


def _lock_path(session_id: str) -> str:
    return os.path.join(_dir(), f"{session_id}.lock")


def _try_lock(session_id: str):
    """Return an open, exclusively flock'ed lock file, or None if held elsewhere."""
    lock = open(_lock_path(session_id), "a")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        return None
    return lock


def _read(session_id: str) -> dict[str, Any]:
    try:
        with open(_path(session_id)) as f:
            data = json.load(f)
    except FileNotFoundError:
        raise SessionNotFoundError(f"No session '{session_id}'.") from None
    except ValueError:
        raise SessionError(f"Session file {_path(session_id)} is corrupt.") from None
    if not isinstance(data, dict):
        raise SessionError(f"Session file {_path(session_id)} is corrupt.")
    if data.get("version", 0) > FORMAT_VERSION:
        raise SessionError(
            f"Session '{session_id}' was saved by a newer codebud "
            f"(format {data.get('version')}); upgrade to open it."
        )
    return data


def _all_session_data() -> list[dict[str, Any]]:
    """Return the parsed contents of every readable session file."""
    if not os.path.isdir(_dir()):
        return []
    found = []
    for entry in sorted(os.listdir(_dir())):
        if not entry.endswith(".json") or entry.startswith("."):
            continue
        try:
            found.append(_read(entry[: -len(".json")]))
        except SessionError as exc:
            logger.warning("Skipping session: %s", exc)
    return found


def validate_name(name: str) -> None:
    """Raise SessionError unless ``name`` is a valid session name."""
    if not _NAME_RE.fullmatch(name):
        raise SessionError(f"Invalid session name '{name}': use letters, digits, '.', '_' or '-'.")


class Session:
    """An open chat session. Holds the session's lock until close()."""

    def __init__(self, data: dict[str, Any], lock):
        self.id: str = data["id"]
        self.name: str | None = data.get("name")
        self.created: str = data.get("created", "")
        self.updated: str = data.get("updated", "")
        self.model: str = data.get("model", "")
        self.mode: str = data.get("mode", "")
        self.history: list[dict[str, str]] = list(data.get("history", []))
        self._lock = lock

    # ------------------------------------------------------------------
    # Creating and opening
    # ------------------------------------------------------------------

    @classmethod
    def create(cls, name: str | None = None) -> "Session":
        """Reserve a new, unique session file and open it."""
        os.makedirs(_dir(), exist_ok=True)
        for _ in range(100):
            session_id = f"{_timestamp()}-{_random_suffix()}"
            try:
                os.close(os.open(_path(session_id), os.O_CREAT | os.O_EXCL | os.O_WRONLY))
            except FileExistsError:
                continue
            break
        else:
            raise SessionError("Could not allocate a unique session ID.")

        lock = _try_lock(session_id)
        if lock is None:  # pragma: no cover - the file was only just created
            raise SessionInUseError(f"Session '{session_id}' is already open.")
        now = _now_iso()
        session = cls({"id": session_id, "created": now, "updated": now}, lock)
        session._write()
        if name is not None:
            session.set_name(name)
        return session

    @classmethod
    def open(cls, ref: str) -> "Session":
        """Open the session whose ID or name is ``ref``."""
        session_id = ref
        if not os.path.exists(_path(ref)):
            matches = [d["id"] for d in _all_session_data() if d.get("name") == ref]
            if not matches:
                raise SessionNotFoundError(f"No session with ID or name '{ref}'.")
            session_id = matches[0]

        lock = _try_lock(session_id)
        if lock is None:
            raise SessionInUseError(f"Session '{ref}' is open in another codebud chat.")
        try:
            data = _read(session_id)
        except BaseException:
            lock.close()
            raise
        return cls(data, lock)

    # ------------------------------------------------------------------
    # Saving
    # ------------------------------------------------------------------

    def save(self, history: list[dict[str, str]], model: str, mode: str = "") -> None:
        """Checkpoint ``history``, ``model`` and agent ``mode`` atomically."""
        self.history = list(history)
        self.model = model
        self.mode = mode
        self.updated = _now_iso()
        self._write()

    def set_name(self, name: str) -> None:
        """Give the session a unique, human-friendly name and save it."""
        validate_name(name)
        for other in _all_session_data():
            if other.get("name") == name and other["id"] != self.id:
                raise SessionError(f"Session name '{name}' is already used by {other['id']}.")
        self.name = name
        self._write()

    def close(self) -> None:
        """Release the session so another chat can open it."""
        if self._lock is not None:
            self._lock.close()  # closing the file releases the flock
            self._lock = None

    def __enter__(self) -> "Session":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _write(self) -> None:
        data = {
            "version": FORMAT_VERSION,
            "id": self.id,
            "name": self.name,
            "created": self.created,
            "updated": self.updated,
            "model": self.model,
            "mode": self.mode,
            "history": self.history,
        }
        atomic_write_text(_path(self.id), json.dumps(data, indent=2) + "\n")


# ----------------------------------------------------------------------
# Listing
# ----------------------------------------------------------------------


def _in_use(session_id: str) -> bool:
    lock = _try_lock(session_id)
    if lock is None:
        return True
    lock.close()
    return False


def list_sessions() -> list[dict[str, Any]]:
    """Return a summary of every saved session, most recently updated first."""
    summaries = []
    for data in _all_session_data():
        history = data.get("history", [])
        first = next((t["content"] for t in history if t.get("role") == "user"), "")
        summaries.append(
            {
                "id": data["id"],
                "name": data.get("name"),
                "created": data.get("created", ""),
                "updated": data.get("updated", ""),
                "model": data.get("model", ""),
                "turns": len(history),
                "first_request": first,
                "in_use": _in_use(data["id"]),
            }
        )
    summaries.sort(key=lambda s: s["updated"], reverse=True)
    return summaries


def most_recent_available() -> str | None:
    """Return the ID of the most recently updated session not open elsewhere."""
    for summary in list_sessions():
        if not summary["in_use"]:
            return summary["id"]
    return None
