"""
tests/test_safety_policy.py

Command policy tightening (2026-10-25). Commands run without a shell
(shlex.split + subprocess), so the policy is judged on the parsed words:
a list entry must match whole leading words, never a string prefix.
"""

import pytest

from agent.safety import SafetyEngine


def _engine(harmless=None, powerful=None):
    engine = SafetyEngine.__new__(SafetyEngine)
    engine.harmless_commands = (
        harmless if harmless is not None else ["cat", "ls", "echo", "git log"]
    )
    engine.powerful_commands = powerful if powerful is not None else ["cp"]
    engine.trusted_sequences = []
    return engine


def _ok(engine, cmd):
    return engine.validate_step({"tool": "command", "args": {"cmd": cmd}})


@pytest.mark.parametrize(
    "cmd",
    ["cat README.md", "ls", "ls -la src", "git log -n 3", "git log", "cp a b"],
)
def test_whole_command_words_are_allowed(cmd):
    assert _ok(_engine(), cmd) is True


@pytest.mark.parametrize(
    "cmd",
    [
        "catastrophe",  # was allowed: starts with "cat"
        "lsblk",  # was allowed: starts with "ls"
        "git logfoo",  # was allowed: starts with "git log"
        "git push",
        "git",
        "cpio -o",  # was allowed: starts with "cp"
        "/bin/ls",
        "echo 'unterminated",  # cannot be parsed
    ],
)
def test_prefix_lookalikes_and_unparsable_commands_are_rejected(cmd):
    assert _ok(_engine(), cmd) is False
