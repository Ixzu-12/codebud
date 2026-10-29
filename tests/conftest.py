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


@pytest.fixture
def fast_passphrase(monkeypatch):
    """Replace age's scrypt passphrase sealing (~1 s per call) with a fast
    stand-in with the same contract: a wrong passphrase raises DecryptError.
    tests/test_crypto.py keeps the real one."""
    pyrage = pytest.importorskip("pyrage")

    def encrypt(data, passphrase):
        return b"SEALED:" + passphrase.encode().hex().encode() + b":" + data

    def decrypt(sealed, passphrase):
        _, secret, data = sealed.split(b":", 2)
        if secret != passphrase.encode().hex().encode():
            raise pyrage.DecryptError("Decryption failed")
        return data

    monkeypatch.setattr(pyrage.passphrase, "encrypt", encrypt)
    monkeypatch.setattr(pyrage.passphrase, "decrypt", decrypt)


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
    # The developer's own backend choice must not change test results.
    monkeypatch.delenv("CODEBUD_BACKEND", raising=False)
    # Nor their privacy level or profile; and main() activating a profile
    # in one test must not leak into the next.
    from codebud import profiles, settings

    monkeypatch.delenv("CODEBUD_PRIVACY", raising=False)
    monkeypatch.delenv("CODEBUD_PROFILE", raising=False)
    monkeypatch.setattr(profiles, "_active", profiles.BUILTIN[profiles.DEFAULT])
    monkeypatch.setattr(paths, "PROFILE_POLICY_DIR", None)
    monkeypatch.setattr(settings, "_applied", {})
    # Nor their passphrase; and no test starts with a key unlocked by another.
    from codebud import crypto

    monkeypatch.delenv("CODEBUD_PASSPHRASE", raising=False)
    monkeypatch.delenv("CODEBUD_ENCRYPT", raising=False)
    monkeypatch.setattr(crypto, "_identity", None)
    monkeypatch.setattr(crypto, "_env_passphrase", None)
    # Tests type passphrases through a patched getpass; pretend there is a
    # terminal (tests of the no-terminal case set this back to False).
    monkeypatch.setattr(crypto, "_terminal_available", lambda: True)

    # doctor's RAM check reads /proc/meminfo; with a model loaded the real
    # machine can be "low on RAM" and break unrelated doctor tests. Tests
    # of the RAM check itself point MEMINFO_PATH at their own file.
    from codebud.cli import doctor

    # Not in tmp_path: tests use that as a project directory and list it.
    meminfo = tmp_path_factory.mktemp("meminfo") / "meminfo"
    meminfo.write_text("MemTotal: 33554432 kB\nMemAvailable: 16777216 kB\n")
    monkeypatch.setattr(doctor, "MEMINFO_PATH", str(meminfo))
