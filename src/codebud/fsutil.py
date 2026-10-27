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

    Writes to a temporary file in the same directory, fsyncs it and renames
    it over ``path`` (os.replace is atomic on POSIX). If anything fails, the
    temporary file is removed and ``path`` keeps its previous contents.
    """
    fd, tmp_path = tempfile.mkstemp(
        dir=os.path.dirname(path) or ".",
        prefix=f".{os.path.basename(path)}-",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        os.unlink(tmp_path)
        raise
