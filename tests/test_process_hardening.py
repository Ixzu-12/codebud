"""
tests/test_process_hardening.py

Audit finding M1 (2026-10-29): before the secret key or a passphrase is
held in memory, codebud turns core dumps off and (on Linux) makes itself
non-dumpable, so a crash cannot leave the key and decrypted data in a core
file, and other processes of the same user cannot read its memory.

Hardening lasts for the life of the process, so it is checked in a
subprocess; conftest.py stubs it out for every other test.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from codebud import crypto

CHECK = """
import resource, sys
from codebud import crypto
crypto._harden_process()
print(resource.getrlimit(resource.RLIMIT_CORE))
if sys.platform.startswith("linux"):
    import ctypes
    print(ctypes.CDLL(None).prctl(3, 0, 0, 0, 0))  # PR_GET_DUMPABLE
"""


def test_hardening_disables_core_dumps_and_dumping():
    # The codebud under test, not whichever one is installed.
    env = {**os.environ, "PYTHONPATH": str(Path(crypto.__file__).resolve().parents[1])}
    out = subprocess.run(
        [sys.executable, "-c", CHECK], capture_output=True, text=True, check=True, env=env
    ).stdout.split()
    assert out[0:2] == ["(0,", "0)"]
    if sys.platform.startswith("linux"):
        assert out[2] == "0"


@pytest.mark.parametrize("how", ["unlock", "create", "environment"])
def test_every_way_of_holding_a_secret_hardens(how, monkeypatch, fast_passphrase):
    pytest.importorskip("pyrage")
    calls = []
    if how != "create":
        crypto.create_key("pw")
        crypto.lock()
    monkeypatch.setattr(crypto, "_harden_process", lambda: calls.append(how))
    if how == "unlock":
        crypto.unlock("pw")
    elif how == "create":
        crypto.create_key("pw")
    else:
        monkeypatch.setenv("CODEBUD_PASSPHRASE", "pw")
        crypto.take_env_passphrase()
    assert calls, f"{how} did not harden the process"
