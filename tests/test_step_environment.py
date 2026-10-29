"""
tests/test_step_environment.py

Command steps never see the encryption passphrase or codebud's secret
settings (audit finding H1, 2026-10-29): isolation hides secret files, but
a child process inherits the environment, so a step running `printenv`
could otherwise print them. codebud also takes CODEBUD_PASSPHRASE out of
its own environment at startup, so nothing it starts can inherit it.
"""

import os
import sys
from unittest.mock import patch

import pytest

from codebud import crypto
from codebud.sandbox import Sandbox, step_environment


@pytest.fixture
def secrets_in_env(monkeypatch):
    monkeypatch.setenv("CODEBUD_PASSPHRASE", "PASSPHRASE-LEAK")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-LEAK")
    monkeypatch.setenv("CODEBUD_HARMLESS_MARKER", "visible")


@pytest.mark.parametrize("privacy", ["locked", "private", "trusted"])
def test_a_step_cannot_print_the_secrets(secrets_in_env, monkeypatch, tmp_path, privacy):
    monkeypatch.setenv("CODEBUD_PRIVACY", privacy)
    monkeypatch.chdir(tmp_path)
    result = Sandbox().run_command("env")
    if result["returncode"] != 0:  # e.g. locked without isolation available
        pytest.skip(result["stderr"])
    assert "LEAK" not in result["stdout"]
    assert "CODEBUD_HARMLESS_MARKER=visible" in result["stdout"]


def test_step_environment_leaves_out_secret_settings(secrets_in_env):
    env = step_environment()
    assert "CODEBUD_PASSPHRASE" not in env and "OPENAI_API_KEY" not in env
    assert env["CODEBUD_HARMLESS_MARKER"] == "visible"


def test_startup_takes_the_passphrase_out_of_the_environment(secrets_in_env):
    from codebud.cli import main as cli

    with patch.object(sys, "argv", ["codebud", "version"]):
        try:
            cli.main()
        except SystemExit:
            pass
    assert "CODEBUD_PASSPHRASE" not in os.environ
    assert crypto.passphrase_from_env() == "PASSPHRASE-LEAK"  # still usable in-process


def test_doctor_still_warns_after_it_was_taken(secrets_in_env):
    from codebud.cli import doctor

    crypto.take_env_passphrase()
    passed, label, _ = doctor.check_encryption()
    assert passed is None and "CODEBUD_PASSPHRASE" in label
