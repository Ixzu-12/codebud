"""
codebud/rag/store.py

The project index store (phase 9, B3; decision D13b).

One SQLite database per project, in
$XDG_DATA_HOME/codebud/index/<id>/index.sqlite3, where <id> is derived
from the project's absolute path. The directory is created 0700 and the
database 0600: the index holds copies of the project's text.

Tables:

- meta:   key/value -- project root, embedding model and dimension,
          schema version, whether keyword search is available, last update
- files:  path -> content hash, to re-index only what changed
- chunks: path, line range, symbol, kind, text, and the embedding as a
          unit-length float32 BLOB (so cosine similarity is a dot product)
- chunks_fts: FTS5 keyword index over path, symbol and text, keyed by
          chunk id; left out when SQLite was built without FTS5

An index belongs to one embedding model (A3): vectors from different
models are not comparable, so a different model or dimension is refused
until the index is rebuilt.
"""

import hashlib
import math
import os
import re
import sqlite3
from array import array
from collections.abc import Iterator, Sequence
from datetime import datetime, timezone
from pathlib import Path

from codebud import paths
from codebud.rag.chunker import Chunk

SCHEMA_VERSION = "1"
DB_NAME = "index.sqlite3"


class ModelMismatch(RuntimeError):
    """The index was built with a different embedding model or dimension."""


def index_dir(root: Path | str) -> Path:
    """Directory holding the index of the project at ``root``."""
    key = hashlib.sha256(str(Path(root).resolve()).encode()).hexdigest()[:16]
    return paths.data_dir() / "index" / key


def _private_dir(path: Path) -> None:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path, 0o700)


class IndexStore:
    def __init__(self, root: Path, db_path: Path):
        self.root = root
        self.db_path = db_path
        self._db = sqlite3.connect(db_path)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA busy_timeout=5000")
        self._create_schema()

    # --- opening ------------------------------------------------------------

    @classmethod
    def open(cls, root: Path | str, create: bool = False) -> "IndexStore | None":
        """Open the index of ``root``; None if it has none and not ``create``."""
        root = Path(root).resolve()
        directory = index_dir(root)
        db_path = directory / DB_NAME
        if not db_path.exists():
            if not create:
                return None
            _private_dir(directory.parent)
            _private_dir(directory)
            # Create the file 0600 before SQLite opens it (it would use the umask).
            os.close(os.open(db_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
        return cls(root, db_path)

    def close(self) -> None:
        self._db.close()

    def __enter__(self) -> "IndexStore":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def _create_schema(self) -> None:
        with self._db:
            self._db.executescript(
                """
                CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
                CREATE TABLE IF NOT EXISTS files (path TEXT PRIMARY KEY, sha256 TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS chunks (
                    id INTEGER PRIMARY KEY,
                    path TEXT NOT NULL,
                    start_line INTEGER NOT NULL,
                    end_line INTEGER NOT NULL,
                    symbol TEXT,
                    kind TEXT NOT NULL,
                    text TEXT NOT NULL,
                    vector BLOB NOT NULL
                );
                CREATE INDEX IF NOT EXISTS chunks_path ON chunks (path);
                """
            )
            if self._meta("schema") is None:
                self._set_meta("schema", SCHEMA_VERSION)
                self._set_meta("root", str(self.root))
                self._set_meta("fts", "1" if self._create_fts() else "0")

    def _create_fts(self) -> bool:
        try:
            self._db.execute("CREATE VIRTUAL TABLE chunks_fts USING fts5(path, symbol, text)")
        except sqlite3.OperationalError:  # SQLite built without FTS5
            return False
        return True

    # --- metadata -----------------------------------------------------------

    def _meta(self, key: str) -> str | None:
        row = self._db.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def _set_meta(self, key: str, value: str) -> None:
        self._db.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value))

    @property
    def has_fts(self) -> bool:
        return self._meta("fts") == "1"

    def check_model(self, model: str) -> None:
        """Refuse ``model`` if the index was built with another one."""
        stored = self._meta("model")
        if stored is not None and stored != model:
            raise ModelMismatch(
                f"The index of {self.root} was built with {stored}; this run uses "
                f"{model}. Run: codebud index --rebuild"
            )

    def ensure_model(self, model: str, dim: int) -> None:
        """Bind the index to ``model``/``dim``, or refuse a different one (A3)."""
        self.check_model(model)
        stored_dim = self._meta("dim")
        if stored_dim is None:
            with self._db:
                self._set_meta("model", model)
                self._set_meta("dim", str(dim))
        elif int(stored_dim) != dim:
            raise ModelMismatch(
                f"The index of {self.root} holds {stored_dim}-dim vectors; {model} "
                f"now returns {dim}-dim ones. Run: codebud index --rebuild"
            )

    def _dim(self) -> int:
        dim = self._meta("dim")
        if dim is None:
            raise RuntimeError("ensure_model() must be called before storing vectors")
        return int(dim)

    # --- writing ------------------------------------------------------------

    def replace_file(
        self, path: str, sha256: str, items: Sequence[tuple[Chunk, Sequence[float]]]
    ) -> None:
        """Replace everything stored for ``path`` with ``items`` (chunk, vector)."""
        dim = self._dim()
        blobs = []
        for _chunk, vector in items:
            if len(vector) != dim:
                raise ValueError(f"Expected {dim}-dim vectors, got {len(vector)}")
            blobs.append(_unit_blob(vector))
        with self._db:
            self._delete_chunks([path])
            self._db.execute(
                "INSERT OR REPLACE INTO files (path, sha256) VALUES (?, ?)", (path, sha256)
            )
            for (chunk, _vector), blob in zip(items, blobs, strict=True):
                cursor = self._db.execute(
                    "INSERT INTO chunks (path, start_line, end_line, symbol, kind, text, vector)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        path,
                        chunk.start_line,
                        chunk.end_line,
                        chunk.symbol,
                        chunk.kind,
                        chunk.text,
                        blob,
                    ),
                )
                if self.has_fts:
                    self._db.execute(
                        "INSERT INTO chunks_fts (rowid, path, symbol, text) VALUES (?, ?, ?, ?)",
                        (cursor.lastrowid, path, chunk.symbol or "", chunk.text),
                    )
            self._touch()

    def remove_files(self, paths_: Sequence[str]) -> None:
        """Forget ``paths_`` and their chunks (files deleted from the project)."""
        with self._db:
            self._delete_chunks(paths_)
            self._db.executemany("DELETE FROM files WHERE path = ?", [(p,) for p in paths_])
            self._touch()

    def _delete_chunks(self, paths_: Sequence[str]) -> None:
        for path in paths_:
            if self.has_fts:
                self._db.execute(
                    "DELETE FROM chunks_fts WHERE rowid IN (SELECT id FROM chunks WHERE path = ?)",
                    (path,),
                )
            self._db.execute("DELETE FROM chunks WHERE path = ?", (path,))

    def _touch(self) -> None:
        self._set_meta("updated", datetime.now(timezone.utc).isoformat(timespec="seconds"))

    # --- reading ------------------------------------------------------------

    def file_hashes(self) -> dict[str, str]:
        return dict(self._db.execute("SELECT path, sha256 FROM files"))

    def vectors(self) -> Iterator[tuple[int, bytes]]:
        """(chunk id, float32 unit-vector BLOB) for every chunk, by id."""
        yield from self._db.execute("SELECT id, vector FROM chunks ORDER BY id")

    def chunk(self, chunk_id: int) -> Chunk:
        row = self._db.execute(
            "SELECT path, start_line, end_line, symbol, kind, text FROM chunks WHERE id = ?",
            (chunk_id,),
        ).fetchone()
        if row is None:
            raise KeyError(chunk_id)
        return Chunk(*row)

    def keyword_search(self, query: str, limit: int = 20) -> list[int]:
        """Chunk ids matching any word of ``query``, best (BM25) first."""
        words = re.findall(r"\w+", query)
        if not self.has_fts or not words:
            return []
        match = " OR ".join('"{}"'.format(w.replace('"', '""')) for w in words)
        rows = self._db.execute(
            "SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH ? ORDER BY bm25(chunks_fts)"
            " LIMIT ?",
            (match, limit),
        )
        return [r[0] for r in rows]

    def stats(self) -> dict:
        count = self._db.execute
        return {
            "root": self._meta("root"),
            "model": self._meta("model"),
            "dim": int(self._meta("dim") or 0),
            "files": count("SELECT COUNT(*) FROM files").fetchone()[0],
            "chunks": count("SELECT COUNT(*) FROM chunks").fetchone()[0],
            "keyword_search": self.has_fts,
            "updated": self._meta("updated"),
            "path": str(self.db_path),
        }


def _unit_blob(vector: Sequence[float]) -> bytes:
    """``vector`` scaled to length 1 (left as-is if zero), as float32 bytes."""
    norm = math.sqrt(sum(x * x for x in vector))
    if norm:
        vector = [x / norm for x in vector]
    return array("f", vector).tobytes()
