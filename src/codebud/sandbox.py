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
hidden. At every privacy level, a step's environment leaves out the
encryption passphrase and codebud's secret settings (step_environment()).

The sandbox does NOT:
- validate plans
- validate tool usage
- interact with the LLM
"""

import os
import shlex
import subprocess
from typing import Any

from codebud import isolation


def step_environment() -> dict[str, str]:
    """The environment a command step runs with: codebud's, minus its secrets.

    Isolation hides secret *files*, but a child process inherits the
    environment; a step must never see the encryption passphrase or the
    secret settings (an API key), whatever it runs (`printenv`, `env`).
    """
    from codebud import crypto, settings

    hidden = {crypto.PASSPHRASE_ENV} | {s.env for s in settings.SETTINGS if s.secret}
    return {k: v for k, v in os.environ.items() if k not in hidden}


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
            process = subprocess.run(
                argv, capture_output=True, text=True, timeout=self.timeout, env=step_environment()
            )

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
