"""
codebud/confine.py

Where the file tools (file_read, file_write, patch) may go -- security
review C1, 2026-10-29. These tools run inside codebud, with the user's
rights and no sandbox, so their paths are checked here:

- writes must resolve, symlinks followed, inside the project, and not into
  its .git/ or .codebud/ directories (git hooks and config run programs;
  .codebud/ holds the project's command policy);
- reads may leave the project, as `cat` may, but never reach the places
  isolation hides from command steps: ~/.ssh and the other secret
  directories and files, and codebud's own data, configuration (the
  encryption key) and runtime directory;
- neither may touch codebud's own directories, even inside the project.

The project is the git work tree containing the current directory, or the
current directory itself (rag.indexer.project_root()). The safety policy
refuses a step that breaks these rules before it runs, and the tools check
again when they open the file, without following a final symlink.
"""

import os
from pathlib import Path

# Never written to, at any depth inside the project.
PROTECTED_NAMES = (".git", ".codebud")


class ConfinementError(Exception):
    """A file tool path outside what the tool may touch."""


def project_root() -> Path:
    from codebud.rag.indexer import project_root as root

    return Path(root()).resolve()


def _resolve(path: str) -> Path:
    if not path or "\0" in path:
        raise ConfinementError(f"not a usable path: {path!r}")
    return Path(os.path.realpath(os.path.expanduser(path)))


def _inside(path: Path, directory: Path) -> bool:
    try:
        path.relative_to(directory)
    except ValueError:
        return False
    return True


def _secret_reason(resolved: Path) -> str | None:
    """Why ``resolved`` is a secret location, or None."""
    from codebud import isolation

    home = Path(os.path.realpath(Path.home()))
    for directory in isolation.codebud_dirs():
        if _inside(resolved, Path(os.path.realpath(directory))):
            return f"{resolved} is in codebud's own files ({directory})"
    for name in isolation.HIDDEN_DIRS:
        if _inside(resolved, home / name):
            return f"{resolved} is in ~/{name}, which holds secrets"
    for name in isolation.HIDDEN_FILES:
        if resolved == home / name:
            return f"{resolved} holds secrets"
    return None


def check_read(path: str) -> Path:
    """The resolved path ``path`` may be read at; ConfinementError if not."""
    resolved = _resolve(path)
    reason = _secret_reason(resolved)
    if reason:
        raise ConfinementError(f"file_read refused: {reason}")
    return resolved


def check_write(path: str) -> Path:
    """The resolved path ``path`` may be written at; ConfinementError if not."""
    resolved = _resolve(path)
    project = project_root()
    if not _inside(resolved, project):
        shown = path if str(resolved) == path else f"{path} (resolves to {resolved})"
        raise ConfinementError(f"writing outside the project ({project}) is refused: {shown}")
    inner = resolved.relative_to(project).parts
    protected = next((name for name in PROTECTED_NAMES if name in inner), None)
    if protected:
        raise ConfinementError(
            f"writing into {protected}/ is refused: {resolved} "
            f"({'git hooks and config run programs' if protected == '.git' else 'it holds the project command policy'})"
        )
    reason = _secret_reason(resolved)
    if reason:
        raise ConfinementError(f"writing is refused: {reason}")
    return resolved


def open_for_write(resolved: Path):
    """Open an already-checked path for writing, never through a final symlink."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
    return os.fdopen(os.open(resolved, flags, 0o644), "w")


def open_for_read(resolved: Path):
    """Open an already-checked path for reading, never through a final symlink."""
    return os.fdopen(os.open(resolved, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)))
