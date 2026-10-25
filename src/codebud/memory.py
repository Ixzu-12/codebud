"""
codebud/memory.py

This module defines the MemoryStore class, which maintains a timeline of
execution snapshots. Each snapshot records:

- timestamp
- step metadata
- tool execution result

Memory is persisted as JSON Lines (one snapshot per line, append-only) to:
    $XDG_DATA_HOME/codebud/memory/entries.jsonl   (see codebud/paths.py)

A legacy entries.json (a single JSON array) next to it is migrated
automatically on first load and kept as entries.json.migrated.

The MemoryStore exposes:
- add_snapshot(step, result)
- history (list of snapshots)
"""

import fcntl
import json
import os
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any

from . import paths
from .fsutil import atomic_write_text

# Overridable for tests; None means the default location in the data dir.
MEMORY_PATH: str | None = None


def _path() -> str:
    """Return the memory file in use (MEMORY_PATH, else the data dir's)."""
    return MEMORY_PATH or str(paths.data_dir() / "memory" / "entries.jsonl")


class MemoryStore:
    """
    Timeline-based memory store.

    Responsibilities:
    - Load memory snapshots from disk
    - Append new snapshots
    - Persist snapshots
    - Expose history for UI
    """

    def __init__(self):
        self.history: list[dict[str, Any]] = []
        os.makedirs(os.path.dirname(_path()), exist_ok=True)
        with self._locked():
            self._migrate_legacy_file()
            self._load()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add_snapshot(self, step: dict[str, Any], result: dict[str, Any]):
        """
        Add a new memory snapshot.

        Snapshot format (one per line in the file):
        {
            "timestamp": "...",
            "data": {
                "step": {...},
                "result": {...}
            }
        }
        """
        snapshot = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "data": {"step": step, "result": result},
        }

        # Appending only touches the end of the file, so earlier entries
        # can never be lost to an interrupted write. The lock keeps lines
        # from two codebud processes from interleaving.
        with self._locked():
            with open(_path(), "a+b") as f:
                if f.tell() > 0:
                    f.seek(-1, os.SEEK_END)
                    if f.read(1) != b"\n":
                        # A previous write was cut off mid-line; end that
                        # line so it can't swallow this entry.
                        f.write(b"\n")
                f.write(json.dumps(snapshot).encode() + b"\n")
                f.flush()
                os.fsync(f.fileno())
        self.history.append(snapshot)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @contextmanager
    def _locked(self):
        """Hold an exclusive flock(2) on the memory lock file."""
        with open(f"{_path()}.lock", "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield

    def _load(self):
        """Load memory snapshots from disk, skipping unreadable lines.

        Bad lines are left in the file untouched (nothing is ever
        rewritten), so they stay recoverable by hand.
        """
        self.history = []
        skipped = 0
        try:
            with open(_path()) as f:
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        entry = json.loads(line)
                    except ValueError:
                        skipped += 1
                        continue
                    if isinstance(entry, dict):
                        self.history.append(entry)
                    else:
                        skipped += 1
        except FileNotFoundError:
            return
        if skipped:
            print(
                f"codebud: skipped {skipped} unreadable line(s) in {_path()}",
                file=sys.stderr,
            )

    def _migrate_legacy_file(self):
        """Convert a legacy entries.json array into entries.jsonl.

        Runs only while entries.jsonl does not exist yet. The new file is
        written atomically before the old one is renamed to
        entries.json.migrated, so a crash part-way loses nothing.
        """
        legacy = os.path.splitext(_path())[0] + ".json"
        if legacy == _path() or os.path.exists(_path()):
            return
        if not os.path.exists(legacy):
            return
        try:
            with open(legacy) as f:
                entries = json.load(f)
            if not isinstance(entries, list):
                raise ValueError("not a JSON array")
        except ValueError:
            self._quarantine_corrupt_file(legacy)
            return

        lines = "".join(json.dumps(e) + "\n" for e in entries)
        atomic_write_text(_path(), lines)
        os.replace(legacy, f"{legacy}.migrated")

    def _quarantine_corrupt_file(self, path):
        """Rename an unparseable memory file to <path>.corrupt-<ts>."""
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        backup = f"{path}.corrupt-{stamp}"
        os.replace(path, backup)
        print(
            f"codebud: memory file {path} is corrupt; "
            f"moved it to {backup} and started a new history",
            file=sys.stderr,
        )
