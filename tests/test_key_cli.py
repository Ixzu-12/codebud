"""
tests/test_key_cli.py

`codebud key init|passwd|status` and the passphrase sources (phase 10,
D14g): typed on the terminal (twice when new), or CODEBUD_PASSPHRASE for
unattended runs, which `codebud doctor` warns about.

age's scrypt makes each real passphrase operation take about a second;
test_crypto.py covers those. Here the passphrase sealing is replaced by a
fast stand-in with the same contract (wrong passphrase -> DecryptError).
"""

import sys
from unittest.mock import patch

import pytest

pytest.importorskip("pyrage")

from codebud import crypto  # noqa: E402
from codebud.cli import doctor  # noqa: E402

pytestmark = pytest.mark.usefixtures("fast_passphrase")  # see conftest.py


@pytest.fixture
def typed(monkeypatch):
    """Queue what is typed at getpass prompts."""
    answers = []
    monkeypatch.setattr("getpass.getpass", lambda _prompt="": answers.pop(0))
    return answers


def _codebud(*argv):
    from codebud.cli import main as cli

    with patch.object(sys, "argv", ["codebud", *argv]):
        try:
            cli.main()
        except SystemExit as exc:
            return exc.code or 0
    return 0


def test_init_asks_twice_and_creates_the_key(typed, capsys):
    typed += ["correct horse battery", "correct horse battery"]
    assert _codebud("key", "init") == 0
    assert crypto.has_key()
    out = capsys.readouterr().out
    assert crypto.recipient() in out
    assert "cannot be recovered" in out
    crypto.lock()
    crypto.unlock("correct horse battery")


def test_init_refuses_mismatched_passphrases(typed, capsys):
    typed += ["first long passphrase", "second long passphrase"]
    assert _codebud("key", "init") == 1
    assert not crypto.has_key()
    assert "do not match" in capsys.readouterr().err


def test_init_uses_the_environment_passphrase(monkeypatch, capsys):
    monkeypatch.setenv("CODEBUD_PASSPHRASE", "unattended run passphrase")
    assert _codebud("key", "init") == 0
    crypto.lock()
    crypto.unlock("unattended run passphrase")


def test_init_refuses_to_replace_a_key(typed, capsys):
    crypto.create_key("first")
    assert _codebud("key", "init") == 1
    assert "already exists" in capsys.readouterr().err


def test_passwd_changes_the_passphrase(typed, capsys):
    crypto.create_key("old")
    sealed = crypto.encrypt(b"data")
    crypto.lock()
    typed += ["old", "a new long passphrase", "a new long passphrase"]
    assert _codebud("key", "passwd") == 0
    crypto.unlock("a new long passphrase")
    assert crypto.decrypt(sealed) == b"data"


def test_passwd_refuses_a_wrong_current_passphrase(typed, capsys):
    crypto.create_key("old")
    typed += ["wrong"]
    assert _codebud("key", "passwd") == 1
    assert "wrong passphrase" in capsys.readouterr().err


def test_passwd_without_a_key(capsys):
    assert _codebud("key", "passwd") == 1
    assert "codebud key init" in capsys.readouterr().err


def test_status(capsys):
    assert _codebud("key", "status") == 0
    assert "no key yet" in capsys.readouterr().out
    crypto.create_key("pw")
    assert _codebud("key", "status") == 0
    out = capsys.readouterr().out
    assert crypto.recipient() in out and "asked on the terminal" in out


# --- unlocking --------------------------------------------------------------


def test_ensure_unlocked_prompts_and_retries():
    crypto.create_key("right")
    crypto.lock()
    answers = ["wrong", "right"]
    crypto.ensure_unlocked(ask=lambda _p: answers.pop(0))
    assert crypto.is_unlocked() and answers == []


def test_ensure_unlocked_gives_up(monkeypatch):
    crypto.create_key("right")
    crypto.lock()
    with pytest.raises(crypto.CryptoError, match="wrong passphrase"):
        crypto.ensure_unlocked(ask=lambda _p: "wrong", tries=2)


def test_ensure_unlocked_from_the_environment(monkeypatch):
    crypto.create_key("right")
    crypto.lock()
    monkeypatch.setenv("CODEBUD_PASSPHRASE", "right")
    crypto.ensure_unlocked(ask=lambda _p: pytest.fail("must not prompt"))
    assert crypto.is_unlocked()


def test_ensure_unlocked_bad_environment_passphrase_is_one_try(monkeypatch):
    crypto.create_key("right")
    crypto.lock()
    monkeypatch.setenv("CODEBUD_PASSPHRASE", "wrong")
    with pytest.raises(crypto.CryptoError, match="CODEBUD_PASSPHRASE"):
        crypto.ensure_unlocked(ask=lambda _p: pytest.fail("must not prompt"))


# --- doctor -----------------------------------------------------------------


def test_doctor_warns_about_the_environment_passphrase(monkeypatch):
    monkeypatch.setenv("CODEBUD_PASSPHRASE", "x")
    passed, label, hint = doctor.check_encryption()
    assert passed is None
    assert "CODEBUD_PASSPHRASE" in label and "unattended" in hint


def test_doctor_reports_the_key():
    assert "codebud key init" in doctor.check_encryption()[1]
    crypto.create_key("pw")
    passed, label, _ = doctor.check_encryption()
    assert passed is True and "encrypt = true" in label


# --- passphrase strength (audit L5) -----------------------------------------


@pytest.mark.parametrize("short", ["", "s3cret", "elevenchars"])
def test_init_refuses_a_short_passphrase(typed, capsys, short):
    typed += [short, short]
    assert _codebud("key", "init") == 1
    assert "at least 12 characters" in capsys.readouterr().err
    assert not crypto.has_key()


def test_init_refuses_a_short_environment_passphrase(monkeypatch, capsys):
    monkeypatch.setenv("CODEBUD_PASSPHRASE", "short")
    assert _codebud("key", "init") == 1
    assert not crypto.has_key()


def test_passwd_refuses_a_short_new_passphrase(typed, capsys):
    crypto.create_key("old")
    crypto.lock()
    typed += ["old", "short"]
    assert _codebud("key", "passwd") == 1
    crypto.unlock("old")  # unchanged
