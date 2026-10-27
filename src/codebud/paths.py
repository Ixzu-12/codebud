"""
codebud/paths.py

Where codebud keeps its files (decision D5b). Nothing here depends on the
directory codebud is started from.

- Runtime data (memory, sessions, project indexes):
      $XDG_DATA_HOME/codebud            (default ~/.local/share/codebud)
- Policy files (harmless/powerful commands, trusted sequences), first found:
      <project>/.codebud/               nearest one at or above the cwd
      $XDG_CONFIG_HOME/codebud          (default ~/.config/codebud)
      the defaults shipped with codebud
- Data written by older versions to the checkout's data/ directory is copied
  to the data directory once (migrate_legacy_data); the originals are kept.
"""

import os
import shutil
from pathlib import Path

APP = "codebud"
PROJECT_DIR_NAME = ".codebud"

_PACKAGE_DIR = Path(__file__).resolve().parent

# Default policy files shipped inside the package (package data).
PACKAGED_POLICY_DIR = _PACKAGE_DIR / "data" / "policy"

# The source checkout, when running from one (src/codebud/ -> repo root).
_SOURCE_ROOT = _PACKAGE_DIR.parent.parent

# Override for where versions before 2026-10-25 kept runtime data; see
# legacy_data_dir(). Tests set it (and CODEBUD_LEGACY_DATA_DIR) to an empty
# temporary directory.
LEGACY_DATA_DIR: Path | None = None


def _xdg(var: str, fallback: str) -> Path:
    base = os.environ.get(var) or os.path.join(os.path.expanduser("~"), fallback)
    return Path(base) / APP


def data_dir() -> Path:
    """Directory for runtime data (memory, sessions, embeddings)."""
    return _xdg("XDG_DATA_HOME", os.path.join(".local", "share"))


def config_dir() -> Path:
    """Directory for the user's own policy files."""
    return _xdg("XDG_CONFIG_HOME", ".config")


def project_config_dir(start: Path | str | None = None) -> Path | None:
    """Return the nearest .codebud/ at or above ``start`` (default: cwd)."""
    here = Path(start or os.getcwd()).resolve()
    for directory in (here, *here.parents):
        candidate = directory / PROJECT_DIR_NAME
        if candidate.is_dir():
            return candidate
    return None


def policy_file(name: str, start: Path | str | None = None) -> Path:
    """Return the policy file ``name`` that applies, by precedence."""
    project = project_config_dir(start)
    for directory in (project, config_dir()):
        if directory is not None and (directory / name).is_file():
            return directory / name
    return PACKAGED_POLICY_DIR / name


def legacy_data_dir() -> Path:
    """Where older versions kept runtime data: the checkout's data/.

    Overridable with LEGACY_DATA_DIR or the CODEBUD_LEGACY_DATA_DIR
    environment variable (which child processes inherit).
    """
    if LEGACY_DATA_DIR is not None:
        return Path(LEGACY_DATA_DIR)
    if os.environ.get("CODEBUD_LEGACY_DATA_DIR"):
        return Path(os.environ["CODEBUD_LEGACY_DATA_DIR"])
    return _SOURCE_ROOT / "data"


def migrate_legacy_data() -> list[str]:
    """Copy files from the old data/ directory into data_dir(), once.

    The originals are never deleted: user data must survive even if the
    copy lands somewhere unexpected. A file is copied only if nothing exists
    at its new location, so newer data is never overwritten. Returns the
    new paths of copied files.
    """
    legacy = legacy_data_dir()
    if not legacy.is_dir() or legacy.resolve() == data_dir().resolve():
        return []
    copied = []
    for source in sorted(legacy.rglob("*")):
        if not source.is_file() or source.suffix in (".lock", ".tmp"):
            continue
        target = data_dir() / source.relative_to(legacy)
        if target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        copied.append(str(target))
    return copied
