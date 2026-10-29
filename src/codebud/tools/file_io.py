"""
codebud/tools/file_io.py

This module defines two tools:

1. FileWriteTool  (tool name: "file_write")
   - Creates or overwrites a file with the given content, inside the
     project only (confine.py).

2. FileReadTool   (tool name: "file_read")
   - Reads the content of a file and returns it in stdout; never
     codebud's own files or the user's secret files (confine.py).

Both tools follow the Cursor-style tool contract:
{
    "stdout": "...",
    "stderr": "...",
    "returncode": 0
}
"""

import os
from typing import Any

from codebud import confine


class FileWriteTool:
    """
    Create or overwrite a file.

    Args:
    {
        "path": "path/to/file",
        "content": "file content"
    }
    """

    description = "Create or overwrite a file with the given content."
    usage_hint = 'Required: "path" (str), "content" (str). Use for new files or full rewrites.'
    required_args = ("path",)  # content defaults to ""

    def run(self, args: dict[str, Any]) -> dict[str, Any]:
        path = args.get("path")
        content = args.get("content", "")

        if not path:
            return {"stdout": "", "stderr": "Missing required argument: path", "returncode": 1}

        try:
            target = confine.check_write(path)  # inside the project only
        except confine.ConfinementError as e:
            return {"stdout": "", "stderr": str(e), "returncode": 1}

        try:
            os.makedirs(target.parent, exist_ok=True)
            with confine.open_for_write(target) as f:
                f.write(content)

            return {
                "stdout": f"Wrote {len(content)} bytes to {target}",
                "stderr": "",
                "returncode": 0,
            }

        except Exception as e:
            return {"stdout": "", "stderr": f"FileWriteTool error: {e}", "returncode": 1}


class FileReadTool:
    """
    Read a file and return its content in stdout.

    Args:
    {
        "path": "path/to/file"
    }
    """

    description = "Read a file and return its content."
    usage_hint = 'Required: "path" (str). Only for a specific named file, never a directory.'
    required_args = ("path",)

    def run(self, args: dict[str, Any]) -> dict[str, Any]:
        path = args.get("path")

        if not path:
            return {"stdout": "", "stderr": "Missing required argument: path", "returncode": 1}

        try:
            source = confine.check_read(path)  # never codebud's or secret files
        except confine.ConfinementError as e:
            return {"stdout": "", "stderr": str(e), "returncode": 1}

        if not source.exists():
            return {"stdout": "", "stderr": f"File not found: {path}", "returncode": 1}

        try:
            with confine.open_for_read(source) as f:
                content = f.read()

            return {"stdout": content, "stderr": "", "returncode": 0}

        except Exception as e:
            return {"stdout": "", "stderr": f"FileReadTool error: {e}", "returncode": 1}
