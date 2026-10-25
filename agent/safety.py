"""
agent/safety.py

This module defines the SafetyEngine class, which validates LLM-generated
steps before execution. It enforces:

- Harmless command list
- Powerful command list
- Trusted sequences
- Tool-level safety rules

The safety engine is consulted by:
- planner.py (during plan validation)
- core.py (final plan validation)

It does NOT:
- execute commands
- modify plans
- interact with the LLM
"""

import json
import os
import shlex
from typing import Any

from .modes import DEFAULT_MODE, step_allowed

CONFIG_DIR = "config"
HARMLESS_PATH = os.path.join(CONFIG_DIR, "harmless_commands.json")
POWERFUL_PATH = os.path.join(CONFIG_DIR, "powerful_commands.json")
TRUSTED_SEQ_PATH = os.path.join(CONFIG_DIR, "trusted_sequences.json")


def _split(cmd: str) -> list[str]:
    """Split ``cmd`` like the Sandbox does; [] if it cannot be parsed."""
    try:
        return shlex.split(cmd)
    except ValueError:
        return []


def _starts_with_words(words: list[str], entry: str) -> bool:
    """Return whether ``words`` begins with all the words of ``entry``."""
    entry_words = entry.split()
    return bool(entry_words) and words[: len(entry_words)] == entry_words


# Arguments that turn an allowed program into "run anything" or "delete or
# overwrite something", refused regardless of the allow-lists.
_FIND_FORBIDDEN = {
    "-exec",
    "-execdir",
    "-ok",
    "-okdir",
    "-delete",
    "-fprint",
    "-fprint0",
    "-fprintf",
    "-fls",
}
_GIT_FORBIDDEN_PREFIXES = ("--output", "--ext-diff")
_PYTHONS = {"python", "python3"}
# Python options that take a separate value (the value must not be mistaken
# for the script name when scanning for -c).
_PYTHON_VALUE_OPTIONS = {"-W", "-X"}


def _python_runs_inline_code(args: list[str]) -> bool:
    """Return whether the interpreter options in ``args`` include -c.

    Only the interpreter's own options are checked: they end at the first
    word that is not an option (the script) or after -m, so "-c" passed to
    a script is not refused.
    """
    i = 0
    while i < len(args):
        word = args[i]
        if not word.startswith("-") or word == "-" or word.startswith("--"):
            if word.startswith("--"):
                i += 1
                continue
            return False
        flags = word[1:]
        for pos, flag in enumerate(flags):
            if flag == "c":
                return True
            if flag == "m":
                return False
            if f"-{flag}" in _PYTHON_VALUE_OPTIONS:
                if pos == len(flags) - 1:
                    i += 1  # value is the next word
                break  # rest of this word is the option's value
        i += 1
    return False


def _forbidden_argument(words: list[str]) -> bool:
    """Return whether ``words`` use an argument this policy always refuses."""
    program, args = words[0], words[1:]
    if program == "find":
        return any(arg in _FIND_FORBIDDEN for arg in args)
    if program in _PYTHONS:
        return _python_runs_inline_code(args)
    if program == "git":
        return any(arg.startswith(_GIT_FORBIDDEN_PREFIXES) for arg in args)
    return False


class SafetyEngine:
    """
    Validates steps produced by the LLM planner.

    Responsibilities:
    - Load safety policies
    - Validate each step
    - Provide safety descriptions for planner prompt
    """

    # Active agent mode (see agent/modes.py); AgentCore.set_mode() updates it.
    mode = DEFAULT_MODE

    def __init__(self):
        self.harmless_commands = self._load_json(HARMLESS_PATH)
        self.powerful_commands = self._load_json(POWERFUL_PATH)
        self.trusted_sequences = self._load_json(TRUSTED_SEQ_PATH)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def validate_step(self, step: dict[str, Any]) -> bool:
        """
        Validate a single step.

        Checks:
        - Tool exists (handled by tool registry)
        - If tool is 'command', validate command safety
        - Trusted sequence validation (optional)
        """

        if not step_allowed(self.mode, step):
            return False

        tool = step.get("tool")
        args = step.get("args", {})

        # Command safety
        if tool == "command":
            cmd = args.get("cmd", "")
            if not self._validate_command(cmd):
                return False

        # Additional tool-specific safety rules can be added here

        return True

    def may_execute(self, step: dict[str, Any]) -> tuple[bool, str]:
        """Check, right before execution, that ``step`` may run in this mode.

        Re-validating here means a plan made in one mode cannot run after
        switching to a stricter one. Returns (allowed, reason).
        """
        if self.mode == "plan":
            return False, "plan mode: steps are never executed (switch with /mode ask)"
        if not self.validate_step(step):
            return False, f"step refused in {self.mode} mode by the safety policy"
        return True, ""

    def describe_rules(self) -> str:
        """
        Return a human-readable description of safety rules for the planner prompt.
        """
        return f"""
- Harmless commands: {self.harmless_commands}
- Powerful commands: {self.powerful_commands}
- Trusted sequences: {self.trusted_sequences}
- Dangerous commands are forbidden.
- Tools must be used responsibly.
"""

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _validate_command(self, cmd: str) -> bool:
        """
        Validate a shell command against the harmless/powerful lists.

        Commands run without a shell (Sandbox uses shlex.split), so they
        are judged on the same parsed words: a list entry such as "cat" or
        "git log" must match the command's leading words exactly. A mere
        string prefix is not enough, so "cat" does not admit "catastrophe".
        Some arguments are refused even for listed programs (see
        _forbidden_argument), e.g. find -exec and python -c.
        """
        words = _split(cmd)
        if not words or _forbidden_argument(words):
            return False
        return any(
            _starts_with_words(words, entry)
            for entry in (*self.harmless_commands, *self.powerful_commands)
        )

    def _load_json(self, path: str) -> list[str]:
        """Load a JSON list from disk."""
        try:
            with open(path) as f:
                return json.load(f)
        except Exception:
            return []
