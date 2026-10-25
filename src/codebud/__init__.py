"""
Codebud agent package.

This package contains the core building blocks of the local
Cursor‑style coding agent:

- AgentCore: central orchestrator that wires planner, executor,
  tools, sandbox, safety, and memory.
- Planner: LLM‑driven plan generator (see planner.py).
- Executor: runs validated plans step‑by‑step (see executor.py).
- SafetyEngine: validates tool usage and commands (see safety.py).
- Sandbox: executes shell commands safely (see sandbox.py).
- MemoryStore: persists execution snapshots (see memory.py).
"""

from importlib.metadata import PackageNotFoundError, version

from .core import AgentCore

try:
    # Single source of truth: the version in pyproject.toml.
    __version__ = version("codebud")
except PackageNotFoundError:  # running from a source tree that isn't installed
    __version__ = "0+unknown"

__all__ = ["AgentCore", "__version__"]
