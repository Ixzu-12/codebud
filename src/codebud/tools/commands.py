"""
codebud/tools/commands.py

This module defines the CommandTool, which executes shell commands inside
the sandbox environment.

Tool name: "command"

Args:
{
    "cmd": "shell command string"
}

The tool:
- Delegates execution to sandbox.run_command(cmd, network=...); a step
  that needs the internet declares "network": true, every other command
  runs without network (isolation.py)
- Returns stdout/stderr/returncode
"""

from typing import Any


class CommandTool:
    """
    Execute a shell command inside the sandbox.

    Args:
    {
        "cmd": "shell command"
    }
    """

    description = "Execute a shell command inside the sandbox."
    usage_hint = (
        'Required: "cmd" (str). Use for ls, pytest, grep, git, etc. '
        'Add "network": true only if the command needs the internet '
        "(pip install, git fetch); otherwise it runs without network."
    )
    required_args = ("cmd",)

    def __init__(self, sandbox):
        self.sandbox = sandbox

    def run(self, args: dict[str, Any]) -> dict[str, Any]:
        cmd = args.get("cmd")

        if not cmd:
            return {"stdout": "", "stderr": "Missing required argument: cmd", "returncode": 1}

        # Delegate to sandbox
        return self.sandbox.run_command(cmd, network=bool(args.get("network")))
