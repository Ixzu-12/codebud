"""
agent/memory.py

This module defines the MemoryStore class, which maintains a timeline of
execution snapshots. Each snapshot records:

- timestamp
- step metadata
- tool execution result

Memory is persisted to:
    data/memory/entries.json

The MemoryStore exposes:
- add_snapshot(step, result)
- history (list of snapshots)
"""

import json
import os
import sys
from datetime import datetime, timezone
from typing import Any

MEMORY_PATH = "data/memory/entries.json"


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
        self._ensure_storage()
        self._load()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add_snapshot(self, step: dict[str, Any], result: dict[str, Any]):
        """
        Add a new memory snapshot.

        Snapshot format:
        {
            "timestamp": "...",
            "data": {
                "step": {...},
                "result": {...}
            }
        }
        """
        snapshot = {
            "timestamp": datetime.utcnow().isoformat(),
            "data": {"step": step, "result": result},
        }

        self.history.append(snapshot)
        self._save()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _ensure_storage(self):
        """Ensure the memory directory and file exist."""
        os.makedirs(os.path.dirname(MEMORY_PATH), exist_ok=True)
        if not os.path.exists(MEMORY_PATH):
            with open(MEMORY_PATH, "w") as f:
                json.dump([], f, indent=2)

    def _load(self):
        """Load memory snapshots from disk.

        An unparseable file is moved aside rather than discarded, so the
        next save cannot overwrite the only copy of the old history.
        """
        try:
            with open(MEMORY_PATH) as f:
                self.history = json.load(f)
        except ValueError:
            self._quarantine_corrupt_file()
            self.history = []
        except Exception:
            self.history = []

    def _quarantine_corrupt_file(self):
        """Rename an unparseable memory file to entries.json.corrupt-<ts>."""
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        backup = f"{MEMORY_PATH}.corrupt-{stamp}"
        os.replace(MEMORY_PATH, backup)
        print(
            f"codebud: memory file {MEMORY_PATH} is corrupt; "
            f"moved it to {backup} and started a new history",
            file=sys.stderr,
        )

    def _save(self):
        """Persist memory snapshots to disk."""
        with open(MEMORY_PATH, "w") as f:
            json.dump(self.history, f, indent=2)
