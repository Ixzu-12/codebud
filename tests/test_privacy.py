"""
tests/test_privacy.py

The privacy dial (phase 8, Part E; design settled 2026-10-28): locked /
private (default) / trusted says what codebud may reach, independently of
the mode (how much it asks). Trust never lifts the allow-list or the
refused arguments.
"""

import json
import logging

import pytest

from codebud import llm_stub, privacy, settings
from codebud.cli import doctor
from codebud.safety import SafetyEngine
from codebud.tools import embeddings, web_search


def _engine():
    engine = SafetyEngine.__new__(SafetyEngine)
    engine.harmless_commands = ["ls"]
    engine.powerful_commands = ["pytest"]
    engine.trusted_sequences = []
    return engine


SEARCH = {"tool": "web_search", "args": {"query": "codebud"}}


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    for name in ("CODEBUD_PRIVACY", "CODEBUD_BACKEND", "OLLAMA_BASE_URL", "WEB_SEARCH_ENABLED"):
        monkeypatch.delenv(name, raising=False)


def test_default_is_private():
    assert privacy.level() == "private"


def test_level_from_the_environment(monkeypatch):
    monkeypatch.setenv("CODEBUD_PRIVACY", "Locked")
    assert privacy.level() == "locked"


def test_unknown_level_falls_back_to_private_with_a_warning(monkeypatch, caplog):
    monkeypatch.setenv("CODEBUD_PRIVACY", "trustd")
    with caplog.at_level(logging.WARNING):
        assert privacy.level() == "private"
    assert "trustd" in caplog.text


def test_privacy_is_a_protective_setting():
    setting = settings.BY_KEY["privacy"]
    assert (setting.env, setting.default, setting.protective) == (
        "CODEBUD_PRIVACY",
        "private",
        True,
    )


# --- locked -----------------------------------------------------------------


@pytest.mark.parametrize(
    "env",
    [
        {"CODEBUD_BACKEND": "openai", "OPENAI_API_KEY": "k"},
        {"OLLAMA_BASE_URL": "https://gpu-box.example:11434"},
    ],
)
def test_locked_refuses_any_backend_off_this_machine(monkeypatch, env):
    monkeypatch.setenv("CODEBUD_PRIVACY", "locked")
    monkeypatch.setenv("OLLAMA_MODEL", "m")
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(llm_stub, "_ollama_available", lambda: True)
    monkeypatch.setattr(llm_stub, "_call_ollama", lambda *a, **k: pytest.fail("sent"))
    with pytest.raises(llm_stub.LLMUnavailable, match="privacy is locked"):
        llm_stub.call_llm("secret")


def test_locked_allows_a_local_ollama(monkeypatch):
    monkeypatch.setenv("CODEBUD_PRIVACY", "locked")
    monkeypatch.setenv("OLLAMA_MODEL", "m")
    monkeypatch.setattr(llm_stub, "_ollama_available", lambda: True)
    monkeypatch.setattr(llm_stub, "_call_ollama", lambda *a, **k: "ok")
    assert llm_stub.call_llm("prompt") == "ok"


def test_locked_refuses_remote_embeddings(monkeypatch):
    monkeypatch.setenv("CODEBUD_PRIVACY", "locked")
    monkeypatch.delenv("CODEBUD_EMBED_STUB", raising=False)
    monkeypatch.setenv("OLLAMA_BASE_URL", "https://gpu-box.example:11434")
    monkeypatch.setattr(embeddings.requests, "post", lambda *a, **k: pytest.fail("sent"))
    with pytest.raises(embeddings.EmbeddingUnavailable, match="privacy is locked"):
        embeddings.embed_query("where is it?")


def test_locked_turns_web_search_off(monkeypatch):
    monkeypatch.setenv("WEB_SEARCH_ENABLED", "1")
    assert web_search.enabled() is True
    monkeypatch.setenv("CODEBUD_PRIVACY", "locked")
    assert web_search.enabled() is False
    result = web_search.WebSearchTool().run({"query": "x"})
    assert result["returncode"] == 1 and "locked" in result["stderr"]
    assert "Outbound: none" in doctor.check_outbound()[1]


# --- trusted ----------------------------------------------------------------


def test_trusted_searches_without_confirmation_but_keeps_safety(monkeypatch):
    engine = _engine()
    assert engine.confirmation_reason(SEARCH) is not None
    monkeypatch.setenv("CODEBUD_PRIVACY", "trusted")
    assert engine.confirmation_reason(SEARCH) is None
    # Safety is not privacy: powerful commands still ask, unknown ones are refused.
    assert engine.confirmation_reason({"tool": "command", "args": {"cmd": "pytest"}})
    assert engine.validate_step({"tool": "command", "args": {"cmd": "rm -rf x"}}) is False
    assert engine.validate_step({"tool": "command", "args": {"cmd": "find . -delete"}}) is False


# --- visibility -------------------------------------------------------------


def test_cli_flag_sets_the_level(monkeypatch):
    import os
    import sys
    from unittest.mock import patch

    from codebud.cli import main as cli

    monkeypatch.setenv("CODEBUD_PRIVACY", "private")
    with patch.object(sys, "argv", ["codebud", "--privacy", "locked", "version"]):
        try:
            cli.main()
        except SystemExit:
            pass
    assert os.environ["CODEBUD_PRIVACY"] == "locked"


def test_cli_flag_rejects_unknown_levels(capsys):
    from codebud.cli.main import _build_parser

    with pytest.raises(SystemExit):
        _build_parser().parse_args(["--privacy", "open", "version"])


@pytest.mark.parametrize(
    "level, stream, text",
    [("trusted", "err", "not isolated"), ("locked", "err", "nothing leaves this machine")],
)
def test_start_announces_trusted_and_locked(monkeypatch, capsys, level, stream, text):
    from codebud.cli import main as cli

    monkeypatch.setenv("CODEBUD_PRIVACY", level)
    cli._announce_privacy()
    assert text in getattr(capsys.readouterr(), stream)


def test_private_is_not_announced(capsys):
    from codebud.cli import main as cli

    cli._announce_privacy()
    assert capsys.readouterr().err == ""


def test_toolbar_shows_the_level():
    from codebud.cli.chat_input import toolbar_text

    assert "privacy: trusted" in toolbar_text("ask", "m", 1, 10, privacy="trusted")


def test_doctor_reports_the_level(monkeypatch):
    passed, label, _hint = doctor.check_privacy()
    assert passed is True and label.startswith("Privacy: private")
    monkeypatch.setenv("CODEBUD_PRIVACY", "locked")
    monkeypatch.setenv("CODEBUD_BACKEND", "openai")
    passed, label, hint = doctor.check_privacy()
    assert passed is False and "locked" in label and "CODEBUD_BACKEND" in hint


def test_session_file_records_the_level(monkeypatch):
    from codebud import paths, sessions

    monkeypatch.setenv("CODEBUD_PRIVACY", "trusted")
    s = sessions.Session.create()
    s.save([], model="m", mode="full-auto", privacy=privacy.level())
    data = json.loads((paths.data_dir() / "sessions" / f"{s.id}.json").read_text())
    s.close()
    assert (data["mode"], data["privacy"]) == ("full-auto", "trusted")


def test_locked_neither_announces_nor_lists_a_refused_backend(monkeypatch, capsys):
    from codebud.cli import main as cli

    monkeypatch.setenv("CODEBUD_PRIVACY", "locked")
    monkeypatch.setenv("CODEBUD_BACKEND", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    cli._announce_backend()
    assert capsys.readouterr().err == ""
    assert "Outbound: none" in doctor.check_outbound()[1]
