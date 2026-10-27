"""
codebud/safety.py

This module defines the SafetyEngine class, which validates LLM-generated
steps before execution. It enforces:

- the harmless and powerful command lists (whole-word matching)
- arguments that are always refused (find -exec/-delete, python -c, ...)
- the active agent mode (see codebud/modes.py)

It also reports which commands are powerful (they need confirmation in
auto mode and in `codebud run`). Trusted sequences are loaded and shown to
the planner, but not enforced.

The safety engine is consulted by:
- planner.py (during plan validation)
- core.py (final plan validation)
- executor.py (may_execute(), right before each step runs)

It does NOT:
- execute commands
- modify plans
- interact with the LLM
"""

import json
import logging
import shlex
from typing import Any

from .modes import DEFAULT_MODE, step_allowed
from .paths import policy_file

logger = logging.getLogger(__name__)

# Policy files, looked up by codebud/paths.policy_file(): the project's
# .codebud/, then the user's config dir, then the defaults shipped with codebud.
HARMLESS_FILE = "harmless_commands.json"
POWERFUL_FILE = "powerful_commands.json"
TRUSTED_SEQ_FILE = "trusted_sequences.json"


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


def _forbidden_argument(words: list[str]) -> str | None:
    """Return the argument of ``words`` this policy always refuses, if any."""
    program, args = words[0], words[1:]
    if program == "find":
        return next((arg for arg in args if arg in _FIND_FORBIDDEN), None)
    if program in _PYTHONS:
        return "-c" if _python_runs_inline_code(args) else None
    if program == "git":
        return next((arg for arg in args if arg.startswith(_GIT_FORBIDDEN_PREFIXES)), None)
    return None


POWERFUL_REASON = "Powerful command (runs code or changes files)."


def egress_reason(step: dict[str, Any]) -> str | None:
    """Why ``step`` sends data off the machine, or None.

    Such steps need a confirmation like powerful commands (phase 8, A3),
    except in full-auto mode, where they run unattended (user decision).
    """
    if step.get("tool") == "web_search":
        query = step.get("args", {}).get("query", "")
        return f'Web search: sends "{query}" to DuckDuckGo.'
    return None


class SafetyEngine:
    """
    Validates steps produced by the LLM planner.

    Responsibilities:
    - Load safety policies
    - Validate each step
    - Provide safety descriptions for planner prompt
    """

    # Active agent mode (see codebud/modes.py); AgentCore.set_mode() updates it.
    mode = DEFAULT_MODE

    def __init__(self):
        self.harmless_commands = self._load_json(policy_file(HARMLESS_FILE))
        self.powerful_commands = self._load_json(policy_file(POWERFUL_FILE))
        self.trusted_sequences = self._load_json(policy_file(TRUSTED_SEQ_FILE))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def validate_step(self, step: dict[str, Any]) -> bool:
        """Return whether ``step`` may run (see refusal_reason())."""
        return self.refusal_reason(step) is None

    def refusal_reason(self, step: dict[str, Any]) -> str | None:
        """Return why ``step`` is refused, or None if it may run.

        Checks the agent mode, then, for commands, that the command parses,
        uses no always-refused argument and starts with an allow-list entry.
        (Whether the tool exists is the tool registry's business.)
        """
        tool = step.get("tool")
        if not step_allowed(self.mode, step):
            return f"{tool} is not allowed in {self.mode} mode"
        if tool == "command":
            return self._command_refusal(step.get("args", {}).get("cmd", ""))
        return None

    def is_powerful(self, step: dict[str, Any]) -> bool:
        """Return whether ``step`` is a command on the powerful list.

        Powerful commands run code or change files (python, pytest, cp,
        sed, ...); in auto mode they still need an explicit confirmation.
        """
        if step.get("tool") != "command":
            return False
        words = _split(step.get("args", {}).get("cmd", ""))
        return bool(words) and any(_starts_with_words(words, e) for e in self.powerful_commands)

    def confirmation_reason(self, step: dict[str, Any]) -> str | None:
        """Why ``step`` needs an explicit "y" in auto mode and `run`, or None.

        Powerful commands run code or change files; egress steps (web
        search) send data off the machine.
        """
        if self.is_powerful(step):
            return POWERFUL_REASON
        return egress_reason(step)

    def may_execute(self, step: dict[str, Any]) -> tuple[bool, str]:
        """Check, right before execution, that ``step`` may run in this mode.

        Re-validating here means a plan made in one mode cannot run after
        switching to a stricter one. Returns (allowed, reason).
        """
        if self.mode == "plan":
            return False, "plan mode: steps are never executed (switch with /mode ask)"
        reason = self.refusal_reason(step)
        if reason:
            return False, f"refused by the safety policy: {reason}"
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

    def _command_refusal(self, cmd: str) -> str | None:
        """
        Judge a shell command against the harmless/powerful lists.

        Commands run without a shell (Sandbox uses shlex.split), so they
        are judged on the same parsed words: a list entry such as "cat" or
        "git log" must match the command's leading words exactly. A mere
        string prefix is not enough, so "cat" does not admit "catastrophe".
        Some arguments are refused even for listed programs (see
        _forbidden_argument), e.g. find -exec and python -c.
        Returns why the command is refused, or None.
        """
        if not cmd.strip():
            return "the command is empty"
        words = _split(cmd)
        if not words:
            return "the command could not be parsed"
        forbidden = _forbidden_argument(words)
        if forbidden:
            return f"{words[0]} {forbidden} is never allowed (it can run or delete anything)"
        allowed = (*self.harmless_commands, *self.powerful_commands)
        if not any(_starts_with_words(words, entry) for entry in allowed):
            return (
                f"{words[0]} is not on the allow-list "
                "(harmless_commands.json / powerful_commands.json)"
            )
        return None

    def _load_json(self, path: str) -> list[str]:
        """Load a JSON list from disk; warn and use [] if it is unusable."""
        try:
            with open(path) as f:
                return json.load(f)
        except (OSError, ValueError) as exc:
            logger.warning("Ignoring policy file %s: %s", path, exc)
            return []
