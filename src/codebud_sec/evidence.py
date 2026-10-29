"""
codebud/evidence.py

The evidence log (phase 10): a hash-chained, append-only record of a
security engagement -- the chain of custody for incident response on hosts
the operator owns, or for a contracted penetration test.

The log is a JSON Lines file. Each entry records what happened (``kind``
and ``data``), when (UTC), the SHA-256 of any artefact the step collected,
the hash of the previous entry (``prev``) and its own hash (``hash``, the
SHA-256 of the entry's canonical JSON without that field). The first entry
(``open``) links to GENESIS and names the engagement; ``close`` ends the
log. Editing, removing or reordering an entry breaks the chain, and
verify() reports the first line where it breaks.

A chain cannot show that entries were cut off the *end*: record the head
hash verify() reports (or close the log) somewhere outside the log.

Codebud only ever appends; the file is 0600 and appends hold an exclusive
flock(2). Entries are stored verbatim -- secret masking would alter the
evidence -- and the whole log is encrypted at rest by the security profiles.
"""

import fcntl
import hashlib
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from codebud.fsutil import private_dir

GENESIS = "0" * 64
KINDS = ("open", "step", "warning", "note", "close")


class EvidenceError(Exception):
    """An evidence log that cannot be read, written or trusted."""


def _canonical(entry: dict) -> bytes:
    return json.dumps(entry, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def entry_hash(entry: dict) -> str:
    """The SHA-256 of ``entry``'s canonical JSON, without its ``hash`` field."""
    body = {k: v for k, v in entry.items() if k != "hash"}
    return hashlib.sha256(_canonical(body)).hexdigest()


def file_sha256(path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _artefact(path) -> dict:
    resolved = Path(path).resolve()
    try:
        return {
            "path": str(resolved),
            "sha256": file_sha256(resolved),
            "size": resolved.stat().st_size,
        }
    except OSError as exc:
        raise EvidenceError(f"cannot hash artefact {resolved}: {exc}") from exc


@dataclass
class VerifyResult:
    ok: bool
    entries: int = 0
    closed: bool = False
    head: str | None = None  # hash of the last good entry
    error: str | None = None
    artefact_problems: list[str] = field(default_factory=list)


def _check_chain(lines: list[str]) -> VerifyResult:
    """Walk the entries; stop at the first one that breaks the chain."""
    if not lines:
        return VerifyResult(False, error="the log is empty")
    prev, closed = GENESIS, False
    for seq, line in enumerate(lines):
        where = f"line {seq + 1}"
        partial = VerifyResult(False, entries=seq, head=None if seq == 0 else prev)
        try:
            entry = json.loads(line)
        except ValueError:
            partial.error = f"{where}: not a JSON entry"
            return partial
        if not isinstance(entry, dict):
            partial.error = f"{where}: not a JSON entry"
            return partial
        if closed:
            partial.error = f"{where}: entry after the log was closed"
            return partial
        if entry.get("seq") != seq:
            partial.error = f"{where}: sequence number {entry.get('seq')!r}, expected {seq}"
            return partial
        if entry.get("prev") != prev:
            partial.error = f"{where}: does not link to the previous entry"
            return partial
        if entry.get("hash") != entry_hash(entry):
            partial.error = f"{where}: hash does not match the entry (edited?)"
            return partial
        if (seq == 0) != (entry.get("kind") == "open"):
            partial.error = f"{where}: the log must start with one 'open' entry"
            return partial
        closed = entry.get("kind") == "close"
        prev = entry["hash"]
    return VerifyResult(True, entries=len(lines), closed=closed, head=prev)


def _read_lines(path: Path) -> list[str]:
    try:
        return path.read_text().splitlines()
    except OSError as exc:
        raise EvidenceError(f"cannot read evidence log {path}: {exc}") from exc


def verify(path, check_artefacts: bool = False) -> VerifyResult:
    """Check the chain of the log at ``path``.

    With ``check_artefacts``, also re-hash every artefact the log recorded
    and report any that are missing or no longer match.
    """
    path = Path(path)
    lines = _read_lines(path)
    result = _check_chain(lines)
    if not result.ok or not check_artefacts:
        return result
    for line in lines:
        for record in json.loads(line).get("artefacts", []):
            try:
                if file_sha256(record["path"]) != record["sha256"]:
                    result.artefact_problems.append(
                        f"{record['path']}: changed since it was logged"
                    )
            except FileNotFoundError:
                result.artefact_problems.append(f"{record['path']}: missing")
            except OSError as exc:
                result.artefact_problems.append(f"{record['path']}: cannot read ({exc})")
    result.ok = not result.artefact_problems
    return result


class EvidenceLog:
    """An open evidence log that entries are appended to."""

    def __init__(self, path: Path):
        self.path = path

    @classmethod
    def create(cls, path, engagement=None, header: str = "") -> "EvidenceLog":
        """Start a new log at ``path`` with its ``open`` entry.

        ``engagement`` (an engagement.Engagement) is recorded with the SHA-256
        of its file, so the log names exactly what was authorized.
        """
        path = Path(path)
        private_dir(path.parent)
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as exc:
            raise EvidenceError(f"evidence log {path} already exists") from exc
        os.close(fd)
        data: dict = {"header": header}
        if engagement is not None:
            data = {
                "header": header or engagement.header(),
                "engagement": str(engagement.path.resolve()),
                "engagement_sha256": file_sha256(engagement.path),
                "in_scope": [t.raw for t in engagement.in_scope],
                "out_of_scope": [t.raw for t in engagement.out_of_scope],
            }
        log = cls(path)
        log._append("open", data, ())
        return log

    @classmethod
    def open(cls, path) -> "EvidenceLog":
        """Open an existing log for appending; refuse one whose chain is broken."""
        path = Path(path)
        result = _check_chain(_read_lines(path))
        if not result.ok:
            raise EvidenceError(f"evidence log {path} does not verify: {result.error}")
        return cls(path)

    def append(self, kind: str, data: dict, artefacts=()) -> dict:
        """Append an entry; ``artefacts`` are paths whose SHA-256 is recorded."""
        if kind not in KINDS or kind == "open":
            raise EvidenceError(f"unknown entry kind {kind!r}")
        return self._append(kind, data, artefacts)

    def close(self, data: dict | None = None) -> dict:
        """End the log; nothing can be appended after this."""
        return self._append("close", data or {}, ())

    def _append(self, kind: str, data: dict, artefacts) -> dict:
        records = [_artefact(a) for a in artefacts]
        with open(self.path, "a+") as f:
            fcntl.flock(f, fcntl.LOCK_EX)  # released when f is closed
            f.seek(0)
            lines = f.read().splitlines()
            if lines:
                last = json.loads(lines[-1])
                if last.get("kind") == "close":
                    raise EvidenceError(f"evidence log {self.path} is closed")
                seq, prev = last["seq"] + 1, last["hash"]
            else:
                seq, prev = 0, GENESIS
            entry = {
                "seq": seq,
                "time": datetime.now(timezone.utc).isoformat(timespec="microseconds"),
                "kind": kind,
                "data": data,
                "artefacts": records,
                "prev": prev,
            }
            entry["hash"] = entry_hash(entry)
            f.write(json.dumps(entry, sort_keys=True, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())
        return entry
