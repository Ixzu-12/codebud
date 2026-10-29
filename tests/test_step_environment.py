"""
tests/test_step_environment.py

What a command step's environment holds (audit finding H1 and its
follow-up, 2026-10-29). Isolation hides secret files, but a child process
inherits the environment, so a step running `printenv` could print any
token in it. Steps therefore get an allow-list: what ordinary tools need,
proxy and CA settings only when they declared the network, and whatever
the user added with step_env -- never the encryption passphrase or
codebud's secret settings. codebud also takes CODEBUD_PASSPHRASE out of
its own environment at startup, so nothing it starts can inherit it.
"""

import os
import sys
from unittest.mock import patch

import pytest

from codebud import crypto, settings
from codebud.sandbox import Sandbox, step_environment


@pytest.fixture
def env(monkeypatch):
    for name, value in {
        "CODEBUD_PASSPHRASE": "PASSPHRASE-LEAK",
        "OPENAI_API_KEY": "sk-LEAK",
        "GITHUB_TOKEN": "ghp_LEAK",
        "AWS_SECRET_ACCESS_KEY": "AWS-LEAK",
        "SSH_AUTH_SOCK": "/run/user/1000/ssh-LEAK",
        "LANG": "en_US.UTF-8",
        "LC_ALL": "C.UTF-8",
        "VIRTUAL_ENV": "/home/you/proj/.venv",
        "HTTPS_PROXY": "http://127.0.0.1:8118",
        "MY_TOOL_HOME": "/opt/tool",
    }.items():
        monkeypatch.setenv(name, value)


@pytest.mark.parametrize("privacy", ["locked", "private", "trusted"])
def test_a_step_cannot_print_tokens(env, monkeypatch, tmp_path, privacy):
    monkeypatch.setenv("CODEBUD_PRIVACY", privacy)
    monkeypatch.chdir(tmp_path)
    result = Sandbox().run_command("env")
    if result["returncode"] != 0:  # e.g. locked without isolation available
        pytest.skip(result["stderr"])
    assert "LEAK" not in result["stdout"]
    assert "LANG=en_US.UTF-8" in result["stdout"]


def test_ordinary_variables_pass(env):
    step = step_environment()
    assert step["PATH"] == os.environ["PATH"]
    assert step["HOME"] == os.environ["HOME"]
    assert step["LC_ALL"] == "C.UTF-8"  # LC_* covers every locale variable
    assert step["VIRTUAL_ENV"] == "/home/you/proj/.venv"


def test_tokens_and_agents_do_not(env):
    step = step_environment()
    for name in (
        "CODEBUD_PASSPHRASE",
        "OPENAI_API_KEY",
        "GITHUB_TOKEN",
        "AWS_SECRET_ACCESS_KEY",
        "SSH_AUTH_SOCK",
        "MY_TOOL_HOME",
    ):
        assert name not in step


def test_proxies_only_for_network_steps(env):
    assert "HTTPS_PROXY" not in step_environment(network=False)
    assert step_environment(network=True)["HTTPS_PROXY"] == "http://127.0.0.1:8118"


def test_the_user_can_add_names_and_prefixes(env, monkeypatch):
    monkeypatch.setenv("CODEBUD_STEP_ENV", "MY_TOOL_HOME, AWS_*")
    step = step_environment()
    assert step["MY_TOOL_HOME"] == "/opt/tool"
    assert step["AWS_SECRET_ACCESS_KEY"] == "AWS-LEAK"  # the user asked for it


def test_the_passphrase_and_secret_settings_are_never_passed(env, monkeypatch):
    monkeypatch.setenv("CODEBUD_STEP_ENV", "CODEBUD_PASSPHRASE OPENAI_API_KEY *")
    step = step_environment()
    assert "CODEBUD_PASSPHRASE" not in step and "OPENAI_API_KEY" not in step
    assert step["GITHUB_TOKEN"] == "ghp_LEAK"  # "*" passes the rest, as asked


def test_a_project_file_cannot_add_variables(tmp_path, monkeypatch, caplog):
    project = tmp_path / "proj" / ".codebud"
    project.mkdir(parents=True)
    (project / "config.toml").write_text('step_env = "GITHUB_TOKEN"\n')
    monkeypatch.chdir(tmp_path / "proj")
    assert "CODEBUD_STEP_ENV" not in settings._file_values()
    assert "protects your stored data" in caplog.text


def test_startup_takes_the_passphrase_out_of_the_environment(env):
    from codebud.cli import main as cli

    with patch.object(sys, "argv", ["codebud", "version"]):
        try:
            cli.main()
        except SystemExit:
            pass
    assert "CODEBUD_PASSPHRASE" not in os.environ
    assert crypto.passphrase_from_env() == "PASSPHRASE-LEAK"  # still usable in-process


def test_doctor_still_warns_after_it_was_taken(env):
    from codebud.cli import doctor

    crypto.take_env_passphrase()
    passed, label, _ = doctor.check_encryption()
    assert passed is None and "CODEBUD_PASSPHRASE" in label
