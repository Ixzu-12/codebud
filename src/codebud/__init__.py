"""
Codebud agent package.

This package contains the core building blocks of the
local-first coding agent:

- AgentCore: central orchestrator that wires planner, executor,
  tools, sandbox, safety, and memory.
- Planner: LLM‑driven plan generator (see planner.py).
- Executor: runs validated plans step‑by‑step (see executor.py).
- SafetyEngine: validates tool usage and commands (see safety.py).
- Sandbox: executes shell commands safely (see sandbox.py).
- MemoryStore: persists execution snapshots (see memory.py).
"""

import importlib.metadata
import re
from pathlib import Path

from .core import AgentCore


def _find_version() -> str:
    """Return codebud's version; pyproject.toml is the single source of truth.

    Installed (pip or `make install`), it comes from the package metadata.
    Run from an uninstalled source tree (e.g. `make check`), it is read from
    the pyproject.toml beside src/.
    """
    try:
        return importlib.metadata.version("codebud")
    except importlib.metadata.PackageNotFoundError:
        pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
        try:
            match = re.search(r'^version = "(.+)"$', pyproject.read_text(), re.M)
        except OSError:
            match = None
        return match.group(1) if match else "0+unknown"


__version__ = _find_version()

__all__ = ["AgentCore", "__version__"]
