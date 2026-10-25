"""
agent/modes.py

Agent modes for chat (decision D1). A mode changes what the agent is
allowed to do, and is enforced by SafetyEngine and Executor rather than
only described to the model:

- ask  (default) review the plan, then approve each step
- auto           review the plan once; its steps then run without prompts
- plan           plans are shown but never executed
- edit           only file tools (file_read, file_write, patch); no commands
"""

from typing import Any

MODES = ("ask", "auto", "plan", "edit")
DEFAULT_MODE = "ask"

# Tools permitted in edit mode.
EDIT_TOOLS = ("file_read", "file_write", "patch")


def step_allowed(mode: str, step: dict[str, Any]) -> bool:
    """Return whether ``step`` may be planned or run in ``mode``."""
    if mode == "edit":
        return step.get("tool") in EDIT_TOOLS
    return True


def prompt_note(mode: str) -> str:
    """Text added to the planner prompt for ``mode`` ('' if none).

    Only modes that restrict the tools add text, so the prompt (and
    Ollama's cached prefix) is unchanged in ask, auto and plan modes.
    """
    if mode == "edit":
        return (
            f"MODE: edit. Only these tools may be used: {', '.join(EDIT_TOOLS)}. "
            'Shell commands ("command") will be rejected.\n\n'
        )
    return ""
