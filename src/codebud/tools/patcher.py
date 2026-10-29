"""
codebud/tools/patcher.py

This module defines the PatchTool, which applies unified diff patches to files.

Tool name: "patch"

Args:
{
    "patch": "unified diff text"
}

The patch must change one file, named by its +++ line, inside the project
(confine.py). It is applied strictly (security review H1, 2026-10-29; the
old applier rebuilt the file from the diff's own lines and so dropped
every line the diff did not mention):

- each hunk is located by its context and removed lines -- at the line
  its @@ header names if they match there, otherwise at the nearest place
  they do, since a model's line numbers are often off;
- a hunk whose old lines are found nowhere, or found at several places
  equally near, makes the whole patch fail and the file is left as it was;
- a hunk with no old lines (a pure insertion) goes where its header says;
- `--- /dev/null` creates a new file from added lines only.

Returns stdout/stderr/returncode like every tool.
"""

import re
from dataclasses import dataclass, field
from typing import Any

from codebud import confine

_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


class PatchError(Exception):
    """A patch that does not apply; the file is left unchanged."""


def patch_target(patch_text: str) -> str | None:
    """The file a unified diff patches (its +++ line, a/ or b/ stripped)."""
    target = None
    for line in patch_text.split("\n"):
        if line.startswith("+++ "):
            target = line[4:].strip()
    if not target:
        return None
    return target[2:] if target.startswith(("a/", "b/")) else target


@dataclass
class Hunk:
    old_start: int  # the @@ header's -N
    old_count: int  # the @@ header's ,M (1 when left out)
    old: list[str] = field(default_factory=list)  # context and removed lines
    new: list[str] = field(default_factory=list)  # context and added lines


def parse(patch_text: str) -> tuple[str, bool, list[Hunk]]:
    """(target path, creates a new file, hunks) of a one-file unified diff."""
    lines = patch_text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()  # the newline ending the diff, not an empty context line
    targets = [line for line in lines if line.startswith("+++ ")]
    if not targets:
        raise PatchError("no +++ line naming the file to patch")
    if len(targets) > 1:
        raise PatchError("the patch changes more than one file; send one patch per file")
    target = patch_target(patch_text)
    creates = any(line.startswith("--- /dev/null") for line in lines)

    hunks: list[Hunk] = []
    current: Hunk | None = None
    for line in lines:
        if line.startswith(("--- ", "+++ ")) and current is None:
            continue
        match = _HUNK.match(line)
        if match:
            count = match.group(2)
            current = Hunk(int(match.group(1)), 1 if count is None else int(count))
            hunks.append(current)
            continue
        if current is None or line.startswith("\\"):  # "\ No newline at end of file"
            continue
        if line.startswith("+"):
            current.new.append(line[1:])
        elif line.startswith("-"):
            current.old.append(line[1:])
        else:
            # Context: " text", or an empty line where the leading space was lost.
            text = line[1:] if line.startswith(" ") else line
            current.old.append(text)
            current.new.append(text)
    if not hunks:
        raise PatchError("the patch has no @@ hunks")
    return target, creates, hunks


def _find(lines: list[str], old: list[str], want: int) -> int:
    """Index where ``old`` occurs in ``lines``: nearest to ``want``, unambiguous."""
    n = len(old)
    places = [i for i in range(len(lines) - n + 1) if lines[i : i + n] == old]
    if not places:
        raise PatchError(
            "the lines the patch expects were not found in the file "
            "(it may have changed, or the patch is wrong); nothing was changed"
        )
    places.sort(key=lambda i: abs(i - want))
    if len(places) > 1 and abs(places[0] - want) == abs(places[1] - want):
        raise PatchError(
            "the lines the patch expects occur at more than one place; "
            "add more context lines; nothing was changed"
        )
    return places[0]


def apply(lines: list[str], hunks: list[Hunk]) -> list[str]:
    """``lines`` (without line ends) with ``hunks`` applied, all or nothing."""
    result = list(lines)
    shift = 0  # how far earlier hunks moved later lines
    for hunk in hunks:
        want = max(hunk.old_start - 1, 0) + shift
        if hunk.old:
            at = _find(result, hunk.old, want)
        else:
            # A pure insertion: "-N,0" inserts after line N (GNU diff;
            # "-0,0" is the top of the file).
            after = hunk.old_start if hunk.old_count == 0 else hunk.old_start - 1
            at = min(max(after, 0) + shift, len(result))
        result[at : at + len(hunk.old)] = hunk.new
        shift += len(hunk.new) - len(hunk.old)
    return result


class PatchTool:
    """
    Apply a unified diff patch to a file.

    Args:
    {
        "patch": "unified diff text"
    }
    """

    description = "Apply a unified diff patch to a file."
    usage_hint = 'Required: "patch" (str, unified diff with --- +++ headers). Prefer over file_write for small edits.'
    required_args = ("patch",)

    def run(self, args: dict[str, Any]) -> dict[str, Any]:
        patch_text = args.get("patch")

        if not patch_text:
            return {"stdout": "", "stderr": "Missing required argument: patch", "returncode": 1}

        try:
            result = self._apply_patch(patch_text)
            return {"stdout": result, "stderr": "", "returncode": 0}

        except Exception as e:
            return {"stdout": "", "stderr": f"PatchTool error: {e}", "returncode": 1}

    def _apply_patch(self, patch_text: str) -> str:
        name, creates, hunks = parse(patch_text)
        target_path = confine.check_write(name)  # inside the project only

        if target_path.exists():
            with confine.open_for_read(target_path) as f:
                text = f.read()
        elif creates and all(not h.old for h in hunks):
            text = ""
        else:
            raise FileNotFoundError(f"Target file not found: {name}")

        target_path.parent.mkdir(parents=True, exist_ok=True)
        ends_with_newline = text.endswith("\n") or not text
        patched = apply(text.splitlines(), hunks)

        with confine.open_for_write(target_path) as f:
            f.write("\n".join(patched) + ("\n" if patched and ends_with_newline else ""))

        return f"Patch applied to {target_path}"
