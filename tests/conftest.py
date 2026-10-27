"""
tests/conftest.py

Every test runs with its own XDG data/config directories and no legacy data
directory, so nothing can read or write the user's real
~/.local/share/codebud, ~/.config/codebud or the checkout's old data/ --
including codebud processes that tests start (they inherit the environment).
Embeddings use the deterministic offline stub (CODEBUD_EMBED_STUB=1), and
`codebud doctor` sees a fake /proc/meminfo with plenty of free RAM, so no
test depends on what the machine is running at the time.
"""

import pytest


@pytest.fixture(autouse=True)
def _isolated_codebud_dirs(tmp_path, tmp_path_factory, monkeypatch):
    from codebud import paths

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
    # Set as environment variables too, so `codebud` run as a subprocess
    # (e.g. tests/test_paths.py runs `python -m codebud`) is isolated as well.
    monkeypatch.setenv("CODEBUD_LEGACY_DATA_DIR", str(tmp_path / "no-legacy-data"))
    monkeypatch.setattr(paths, "LEGACY_DATA_DIR", None)
    # Embeddings use the deterministic offline stub, never a live Ollama.
    monkeypatch.setenv("CODEBUD_EMBED_STUB", "1")

    # doctor's RAM check reads /proc/meminfo; with a model loaded the real
    # machine can be "low on RAM" and break unrelated doctor tests. Tests
    # of the RAM check itself point MEMINFO_PATH at their own file.
    from codebud.cli import doctor

    # Not in tmp_path: tests use that as a project directory and list it.
    meminfo = tmp_path_factory.mktemp("meminfo") / "meminfo"
    meminfo.write_text("MemTotal: 33554432 kB\nMemAvailable: 16777216 kB\n")
    monkeypatch.setattr(doctor, "MEMINFO_PATH", str(meminfo))
