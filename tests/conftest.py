"""
tests/conftest.py

Every test runs with its own XDG data/config directories and no legacy data
directory, so nothing can read or write the user's real
~/.local/share/codebud, ~/.config/codebud or the checkout's old data/ --
including codebud processes that tests start (they inherit the environment).
Embeddings use the deterministic offline stub (CODEBUD_EMBED_STUB=1).
"""

import pytest


@pytest.fixture(autouse=True)
def _isolated_codebud_dirs(tmp_path, monkeypatch):
    from codebud import paths

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
    # Set as environment variables too, so `codebud` run as a subprocess
    # (e.g. tests/test_paths.py runs `python -m codebud`) is isolated as well.
    monkeypatch.setenv("CODEBUD_LEGACY_DATA_DIR", str(tmp_path / "no-legacy-data"))
    monkeypatch.setattr(paths, "LEGACY_DATA_DIR", None)
    # Embeddings use the deterministic offline stub, never a live Ollama.
    monkeypatch.setenv("CODEBUD_EMBED_STUB", "1")
