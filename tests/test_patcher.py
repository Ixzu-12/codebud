"""
tests/test_patcher.py

The patch tool applies unified diffs strictly (security review H1,
2026-10-29): the old applier rebuilt the file from the diff's own lines,
so a one-line change to line 4 of a 5-line file left only that line.
Hunks are now located by their context and removed lines, a mismatch
fails without touching the file, and insertions follow GNU diff's -N,0.
"""

import pytest

from codebud import confine
from codebud.tools.patcher import PatchError, PatchTool, apply, parse

FIVE = "line1\nline2\nline3\nline4\nline5\n"


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(confine, "project_root", lambda: tmp_path.resolve())
    (tmp_path / "f.py").write_text(FIVE)
    return tmp_path


def _patch(project, diff):
    return PatchTool().run({"patch": diff})


def test_a_one_line_change_keeps_every_other_line(project):
    # The reviewer's reproduction: this left only "LINE4".
    result = _patch(project, "--- a/f.py\n+++ b/f.py\n@@ -4,1 +4,1 @@\n-line4\n+LINE4\n")
    assert result["returncode"] == 0
    assert (project / "f.py").read_text() == "line1\nline2\nline3\nLINE4\nline5\n"


def test_insertion_at_the_top(project):
    # The planner's own example shape (now -0,0, GNU diff's top of file).
    result = _patch(project, '--- a/f.py\n+++ b/f.py\n@@ -0,0 +1,2 @@\n+"""doc"""\n+\n')
    assert result["returncode"] == 0
    assert (project / "f.py").read_text() == '"""doc"""\n\n' + FIVE


def test_insertion_after_a_line(project):
    _patch(project, "--- a/f.py\n+++ b/f.py\n@@ -2,0 +3 @@\n+new\n")
    assert (project / "f.py").read_text() == "line1\nline2\nnew\nline3\nline4\nline5\n"


def test_wrong_line_numbers_with_matching_context(project):
    result = _patch(
        project, "--- a/f.py\n+++ b/f.py\n@@ -1,3 +1,3 @@\n line3\n-line4\n+LINE4\n line5\n"
    )
    assert result["returncode"] == 0
    assert (project / "f.py").read_text() == "line1\nline2\nline3\nLINE4\nline5\n"


def test_lines_that_do_not_match_fail_and_change_nothing(project):
    result = _patch(project, "--- a/f.py\n+++ b/f.py\n@@ -4,1 +4,1 @@\n-line9\n+X\n")
    assert result["returncode"] == 1 and "not found" in result["stderr"]
    assert (project / "f.py").read_text() == FIVE


def test_ambiguous_context_fails(project):
    (project / "f.py").write_text("a\nx\nb\nx\nc\n")
    result = _patch(project, "--- a/f.py\n+++ b/f.py\n@@ -3,1 +3,1 @@\n-x\n+y\n")
    assert result["returncode"] == 1 and "more than one place" in result["stderr"]


def test_the_nearest_match_wins_when_not_ambiguous(project):
    (project / "f.py").write_text("x\na\nb\nc\nx\n")
    _patch(project, "--- a/f.py\n+++ b/f.py\n@@ -5,1 +5,1 @@\n-x\n+y\n")
    assert (project / "f.py").read_text() == "x\na\nb\nc\ny\n"


def test_several_hunks_shift_correctly(project):
    diff = (
        "--- a/f.py\n+++ b/f.py\n"
        "@@ -1,2 +1,3 @@\n line1\n+inserted\n line2\n"
        "@@ -4,2 +5,1 @@\n-line4\n line5\n"
    )
    assert _patch(project, diff)["returncode"] == 0
    assert (project / "f.py").read_text() == "line1\ninserted\nline2\nline3\nline5\n"


def test_a_blank_context_line_without_its_space(project):
    (project / "f.py").write_text("a\n\nb\n")
    _patch(project, "--- a/f.py\n+++ b/f.py\n@@ -1,3 +1,3 @@\n a\n\n-b\n+B\n")
    assert (project / "f.py").read_text() == "a\n\nB\n"


def test_a_missing_final_newline_is_kept(project):
    (project / "f.py").write_text("a\nb")
    _patch(project, "--- a/f.py\n+++ b/f.py\n@@ -2 +2 @@\n-b\n+B\n\\ No newline at end of file\n")
    assert (project / "f.py").read_text() == "a\nB"


def test_a_new_file(project):
    result = _patch(project, "--- /dev/null\n+++ b/pkg/new.py\n@@ -0,0 +1,2 @@\n+a = 1\n+b = 2\n")
    assert result["returncode"] == 0
    assert (project / "pkg" / "new.py").read_text() == "a = 1\nb = 2\n"


def test_more_than_one_file_is_refused(project):
    diff = "--- a/f.py\n+++ b/f.py\n@@ -1 +1 @@\n-line1\n+X\n--- a/g.py\n+++ b/g.py\n@@ -1 +1 @@\n-a\n+b\n"
    result = _patch(project, diff)
    assert result["returncode"] == 1 and "more than one file" in result["stderr"]
    assert (project / "f.py").read_text() == FIVE


def test_no_hunks(project):
    with pytest.raises(PatchError, match="no @@ hunks"):
        parse("--- a/f.py\n+++ b/f.py\n")


def test_apply_is_all_or_nothing():
    lines = ["a", "b", "c"]
    _, _, hunks = parse("--- a/f\n+++ b/f\n@@ -1 +1 @@\n-a\n+A\n@@ -3 +3 @@\n-zzz\n+Z\n")
    with pytest.raises(PatchError):
        apply(lines, hunks)
    assert lines == ["a", "b", "c"]
