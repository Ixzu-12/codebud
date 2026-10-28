"""
codebud/sandbox.py

This module defines the Sandbox class, which provides a safe environment
for executing shell commands. It ensures:

- Restricted command execution
- Timeout enforcement
- Safe working directory
- Structured stdout/stderr/returncode output

Each command is isolated according to the privacy level (isolation.py):
no network unless the step declared it, project-only writes, secrets
hidden.

The sandbox does NOT:
- validate plans
- validate tool usage
- interact with the LLM
"""

import shlex
import subprocess
from typing import Any

from codebud import isolation


class Sandbox:
    """
    Safe execution environment for shell commands.

    Responsibilities:
    - Execute commands with subprocess
    - Enforce timeouts
    - Block obviously dangerous commands
    - Return structured results
    """

    def __init__(self, timeout: int = 10):
        self.timeout = timeout

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run_command(self, cmd: str, network: bool = False) -> dict[str, Any]:
        """
        Execute a shell command safely, isolated (isolation.prepare());
        ``network`` is the step's declaration that it needs the internet.

        Returns:
        {
            "stdout": "...",
            "stderr": "...",
            "returncode": 0
        }
        """

        # Basic safety guard
        if self._is_dangerous(cmd):
            return {"stdout": "", "stderr": f"Blocked dangerous command: {cmd}", "returncode": 1}

        argv, refusal = isolation.prepare(shlex.split(cmd), network=network)
        if refusal:
            return {"stdout": "", "stderr": refusal, "returncode": 1}

        try:
            process = subprocess.run(argv, capture_output=True, text=True, timeout=self.timeout)

            return {
                "stdout": process.stdout,
                "stderr": process.stderr,
                "returncode": process.returncode,
            }

        except subprocess.TimeoutExpired:
            return {
                "stdout": "",
                "stderr": f"Command timed out after {self.timeout} seconds",
                "returncode": 1,
            }

        except Exception as e:
            return {"stdout": "", "stderr": f"Sandbox error: {e}", "returncode": 1}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _is_dangerous(self, cmd: str) -> bool:
        """
        Block obviously dangerous commands.
        This is NOT a full security system — just a basic guard.
        """

        dangerous_patterns = [
            "rm -rf /",
            "rm -rf *",
            "shutdown",
            "reboot",
            ":(){:|:&};:",  # fork bomb
        ]

        for pattern in dangerous_patterns:
            if pattern in cmd:
                return True

        return False
