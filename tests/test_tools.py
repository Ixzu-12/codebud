"""
tests/test_tools.py

Regression tests for tool and health-check bugs found in the 2026-10-25
documentation audit.
"""

import json

from codebud.tools.file_io import FileWriteTool


def test_file_write_to_a_bare_filename(tmp_path, monkeypatch):
    """ "hello.py" has no directory part; os.makedirs("") used to fail."""
    monkeypatch.chdir(tmp_path)
    result = FileWriteTool().run({"path": "hello.py", "content": "print('hi')\n"})
    assert result["returncode"] == 0, result["stderr"]
    assert (tmp_path / "hello.py").read_text() == "print('hi')\n"
