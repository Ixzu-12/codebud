"""
codebud/fsutil.py

Small filesystem helpers shared by the persistence modules (memory,
sessions, index).

Codebud's data -- execution history, conversations, copies of project
text -- is readable only by its owner (phase 8, B1): directories are
created 0700 and files 0600, whatever the umask.
"""

import os
import tempfile
from pathlib import Path


def private_dir(path) -> None:
    """Create ``path`` and any missing parents, each one 0700.

    os.makedirs applies its mode only to the last directory, so the
    missing levels are created one by one. Existing directories are left
    as they are.
    """
    missing = []
    current = Path(path)
    while not current.exists():
        missing.append(current)
        current = current.parent
    for directory in reversed(missing):
        try:
            directory.mkdir(mode=0o700)
        except FileExistsError:  # created meanwhile by another process
            pass


def create_private(path) -> None:
    """Create the file ``path`` as 0600 if it does not exist yet."""
    os.close(os.open(path, os.O_CREAT | os.O_WRONLY, 0o600))


def atomic_write_text(path: str, text: str) -> None:
    """Replace the contents of ``path`` with ``text``, all or nothing.

    Writes to a temporary file in the same directory, fsyncs it, renames
    it over ``path`` (os.replace is atomic on POSIX) and fsyncs the
    directory so the rename survives a power cut. If anything fails, the
    temporary file is removed and ``path`` keeps its previous contents.
    """
    _atomic_write(path, text, "w")


def atomic_write_bytes(path, data: bytes) -> None:
    """atomic_write_text() for bytes; the new file is 0600."""
    _atomic_write(path, data, "wb")


def _atomic_write(path, content, mode: str) -> None:
    path = os.fspath(path)
    fd, tmp_path = tempfile.mkstemp(  # mkstemp creates the file 0600
        dir=os.path.dirname(path) or ".",
        prefix=f".{os.path.basename(path)}-",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, mode) as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        os.unlink(tmp_path)
        raise
    _fsync_dir(os.path.dirname(path) or ".")


def _fsync_dir(directory: str) -> None:
    """Make a rename in ``directory`` durable (it lives in the directory).

    Without this a power cut right after, say, creating the encryption key
    could lose the new file while data was already encrypted to it. Best
    effort: some platforms cannot open or fsync a directory.
    """
    try:
        fd = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)
