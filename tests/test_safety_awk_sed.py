"""
tests/test_safety_awk_sed.py

awk and sed are powerful commands, but like `python -c` they can run any
program (security review H3, 2026-10-29): awk's system()/getline-from-
command/output pipe, sed's `e` command, and sed's file writes (w, W,
s///w). Those forms are refused; ordinary text processing keeps working,
which matters because a loose check would wrongly refuse a `w` inside a
substitution or an address like /warn/.
"""

import pytest

from codebud.safety import SafetyEngine


@pytest.fixture
def engine():
    return SafetyEngine()


def _reason(engine, cmd):
    return engine.refusal_reason({"id": "1", "tool": "command", "args": {"cmd": cmd}})


REFUSED = [
    # awk
    "awk 'BEGIN{system(\"id\")}'",
    "gawk 'BEGIN{system(\"rm -rf x\")}'",
    "awk '{print $0 | \"sh\"}' f",
    "awk 'BEGIN{while ((\"date\"|getline d)>0) print d}'",
    "awk -f program.awk data",
    "awk --file=program.awk data",
    # sed
    "sed -n '1e id' f",
    "sed -e '2e cat /etc/passwd' f",
    "sed 's/a/b/w out.txt' f",
    "sed 's/x/y/gw log' f",
    "sed '/foo/w captured' f",
    "sed 'W firstlines' f",
    "sed -f script.sed f",
    "sed --file=script.sed f",
]

ALLOWED = [
    # awk
    "awk '{print $1}' data.txt",
    "awk -F, '{sum+=$2} END{print sum}' data.csv",
    "awk 'END{print NR}' f",
    "awk '/error/{print}' log",
    "awk -v n=3 '{print $n}' f",
    # sed — the w and e letters appear, but not as commands
    "sed 's/widget/gadget/g' f",
    "sed 's/a/write/' f",
    "sed -n '/warn/p' f",
    "sed 'y/abce/xyzw/' f",
    "sed -n '1,5p' f",
    "sed 's|/usr/bin|/bin|' f",
    "sed '/^#/d' f",
    "sed 's/foo/bar/;s/baz/qux/' f",
    "sed -e 's/a/b/' -e 's/c/d/' f",
]


@pytest.mark.parametrize("cmd", REFUSED)
def test_code_running_forms_are_refused(engine, cmd):
    reason = _reason(engine, cmd)
    assert reason, f"should have refused: {cmd}"


@pytest.mark.parametrize("cmd", ALLOWED)
def test_ordinary_text_processing_is_allowed(engine, cmd):
    assert _reason(engine, cmd) is None, f"should have allowed: {cmd}"


def test_awk_and_sed_stay_powerful(engine):
    # So even an allowed awk/sed is confirmed in auto mode.
    assert engine.is_powerful({"tool": "command", "args": {"cmd": "awk '{print}' f"}})
    assert engine.is_powerful({"tool": "command", "args": {"cmd": "sed 's/a/b/' f"}})


def test_refusal_mentions_the_reason(engine):
    assert "shell command" in _reason(engine, "sed '1e id' f")
    assert "writes to a file" in _reason(engine, "sed 's/a/b/w x' f")
    assert "system/getline/pipe" in _reason(engine, "awk 'BEGIN{system(\"id\")}'")
