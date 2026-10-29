"""
codebud/tools/patcher.py

This module defines the PatchTool, which applies unified diff patches to files.

Tool name: "patch"

Args:
{
    "patch": "unified diff text"
}

The tool:
- Parses the diff
- Applies it line-by-line
- Writes the updated file
- Returns stdout/stderr/returncode

This is a simplified patcher suitable for coding agents.
"""

from typing import Any

from codebud import confine


def patch_target(patch_text: str) -> str | None:
    """The file a unified diff patches (its +++ line, a/ or b/ stripped)."""
    target = None
    for line in patch_text.split("\n"):
        if line.startswith("+++ "):
            target = line[4:].strip()
    if not target:
        return None
    return target[2:] if target.startswith(("a/", "b/")) else target


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

    # ------------------------------------------------------------------
    # Internal patch logic
    # ------------------------------------------------------------------

    def _apply_patch(self, patch_text: str) -> str:
        """
        Apply a unified diff patch.

        This is a minimal patcher:
        - Supports single-file patches
        - Supports @@ hunk headers
        - Applies line additions/removals
        """

        lines = patch_text.split("\n")

        name = patch_target(patch_text)
        if not name:
            raise ValueError("Could not determine target file from patch")
        target_path = confine.check_write(name)  # inside the project only

        if not target_path.exists():
            raise FileNotFoundError(f"Target file not found: {name}")

        # Load original file
        with confine.open_for_read(target_path) as f:
            original_lines = f.readlines()

        patched_lines = []
        j = 0

        # Apply hunks
        for line in lines:
            # Skip diff file headers and hunk markers
            if line.startswith("---") or line.startswith("+++") or line.startswith("@@"):
                continue

            if line.startswith("-"):
                # Remove line — advance source pointer, do not emit
                j += 1
                continue

            if line.startswith("+"):
                # Add line — emit without the leading '+'
                patched_lines.append(line[1:] + "\n")
                continue

            if line.startswith(" "):
                # Context line — copy from original
                patched_lines.append(original_lines[j])
                j += 1
                continue

        # Write patched file
        with confine.open_for_write(target_path) as f:
            f.writelines(patched_lines)

        return f"Patch applied to {target_path}"
