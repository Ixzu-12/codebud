"""
tests/test_tools.py

Regression tests for tool and health-check bugs found in the 2026-10-25
documentation audit.
"""

from codebud.cli import doctor
from codebud.tools.file_io import FileWriteTool


def test_file_write_to_a_bare_filename(tmp_path, monkeypatch):
    """ "hello.py" has no directory part; os.makedirs("") used to fail."""
    monkeypatch.chdir(tmp_path)
    result = FileWriteTool().run({"path": "hello.py", "content": "print('hi')\n"})
    assert result["returncode"] == 0, result["stderr"]
    assert (tmp_path / "hello.py").read_text() == "print('hi')\n"


def _meminfo(tmp_path, monkeypatch, available_kb):
    meminfo = tmp_path / "meminfo"
    meminfo.write_text(
        f"MemTotal:       16000000 kB\nMemAvailable:   {available_kb} kB\nCached: 1 kB\n"
    )
    monkeypatch.setattr(doctor, "MEMINFO_PATH", str(meminfo))


def test_ram_check_reads_meminfo(tmp_path, monkeypatch):
    """The parser used to crash on every line, so the check was always skipped."""
    _meminfo(tmp_path, monkeypatch, 8 * 1024 * 1024)
    monkeypatch.setenv("OLLAMA_MODEL", "qwen2.5-coder:3b-instruct-q4_K_M")
    ok, label, _hint = doctor.check_ram()
    assert ok is True
    assert label == "Available RAM: 8192 MB (need ~4096 MB)"


def test_low_ram_hint_shows_the_numbers(tmp_path, monkeypatch):
    _meminfo(tmp_path, monkeypatch, 1024 * 1024)
    monkeypatch.setenv("OLLAMA_MODEL", "qwen2.5-coder:3b-instruct-q4_K_M")
    ok, _label, hint = doctor.check_ram()
    assert ok is False
    assert "only 1024 MB is available" in hint
    assert "{" not in hint
