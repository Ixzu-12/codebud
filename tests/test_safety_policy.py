"""
tests/test_safety_policy.py

Command policy tightening (2026-10-25). Commands run without a shell
(shlex.split + subprocess), so the policy is judged on the parsed words:
a list entry must match whole leading words, never a string prefix.
"""

import pytest

from codebud.safety import SafetyEngine


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


def _dev_engine():
    return _engine(
        harmless=["find", "python", "python3", "git log", "git diff", "git show", "ls"],
        powerful=[],
    )


@pytest.mark.parametrize(
    "cmd",
    [
        "find . -name '*.pyc' -delete",
        "find . -exec rm {} ;",
        "find . -execdir sh -c 'x' ;",
        "find . -ok rm {} ;",
        "find . -okdir rm {} ;",
        "find . -fprint out.txt",
        "find . -fls listing.txt",
        "python -c 'import os'",
        "python3 -c print(1)",
        "python3 -cprint(1)",
        "python3 -Ic 'import os'",
        "python3 -W ignore -c 'import os'",
        "git log --output=/tmp/x",
        "git diff --output /tmp/x",
        "git show --ext-diff HEAD",
    ],
)
def test_arguments_that_run_code_or_destroy_are_refused(cmd):
    assert _ok(_dev_engine(), cmd) is False


@pytest.mark.parametrize(
    "cmd",
    [
        "find . -name '*.py'",
        "find src -type f -newer setup.py",
        "python script.py",
        "python3 -m pytest -q",
        "python3 script.py -c config.toml",  # -c belongs to the script, not python
        "python3 -W ignore script.py",
        "git log -n 3 --oneline",
        "git diff HEAD~1",
    ],
)
def test_ordinary_uses_stay_allowed(cmd):
    assert _ok(_dev_engine(), cmd) is True


# --- powerful commands (need an explicit y in auto mode) ---


def test_is_powerful_for_listed_commands_only():
    engine = _engine(harmless=["ls"], powerful=["pytest", "cp"])
    assert engine.is_powerful({"tool": "command", "args": {"cmd": "pytest -q"}}) is True
    assert engine.is_powerful({"tool": "command", "args": {"cmd": "cp a b"}}) is True
    assert engine.is_powerful({"tool": "command", "args": {"cmd": "ls -la"}}) is False
    assert engine.is_powerful({"tool": "file_write", "args": {"path": "a"}}) is False


def test_default_policy_treats_code_runners_as_powerful():
    engine = SafetyEngine()
    for cmd in ["python script.py", "python3 -m pytest", "pytest -q", "ruff check --fix ."]:
        step = {"tool": "command", "args": {"cmd": cmd}}
        assert engine.validate_step(step) is True, cmd
        assert engine.is_powerful(step) is True, cmd
    for cmd in ["ls -la", "cat README.md", "git status"]:
        assert engine.is_powerful({"tool": "command", "args": {"cmd": cmd}}) is False, cmd


def test_malformed_policy_file_is_reported(tmp_path, monkeypatch, caplog):
    """A broken JSON file silently became an empty list; now it warns."""
    (tmp_path / ".codebud").mkdir()
    (tmp_path / ".codebud" / "harmless_commands.json").write_text("[not json")
    monkeypatch.chdir(tmp_path)
    caplog.set_level("WARNING")

    engine = SafetyEngine()

    assert engine.harmless_commands == []
    assert "harmless_commands.json" in caplog.text
