"""
codebud/fsutil.py

Small filesystem helpers shared by the persistence modules (memory,
sessions).
"""

import os
import tempfile


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
