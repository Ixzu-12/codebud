"""
tests/test_backend.py

Choosing the LLM backend explicitly (phase 8, Part A1): no silent cloud
fallback, and a missing backend is reported plainly instead of turning
into an empty chat reply.
"""

import json

import pytest

from codebud import llm_stub, settings
from codebud import planner as planner_mod
from codebud.cli import doctor
from codebud.cli.errors import explain


def _unavailable(prompt, on_chunk=None, timeout=600.0):
    raise llm_stub.LLMUnavailable("Ollama is not reachable at http://localhost:11434")


@pytest.fixture
def planner(monkeypatch):
    from unittest.mock import MagicMock

    registry = MagicMock()
    registry.tools = {}
    registry.describe_tools.return_value = "- command: run a program"
    return planner_mod.LLMPlanner(registry, MagicMock())


def test_planner_reports_an_unavailable_backend(planner, monkeypatch):
    monkeypatch.setattr(planner_mod, "call_llm", _unavailable)
    result = planner.generate_plan("list files")
    assert result == {
        "status": "llm_unavailable",
        "error": "Ollama is not reachable at http://localhost:11434",
    }


@pytest.fixture
def agent(tmp_path, monkeypatch):
    from codebud import memory as memory_mod
    from codebud.core import AgentCore

    monkeypatch.setattr(memory_mod, "MEMORY_PATH", str(tmp_path / "entries.jsonl"))
    monkeypatch.setattr("codebud.core.prewarm_model", lambda: None)
    monkeypatch.setattr("codebud.core.ModelHeartbeat.start", lambda self: None)
    monkeypatch.setattr("codebud.core.ModelHeartbeat.stop", lambda self: None)
    a = AgentCore()
    yield a
    a.close()


def test_core_reports_it_instead_of_an_empty_chat_reply(agent, monkeypatch):
    monkeypatch.setattr(planner_mod, "call_llm", _unavailable)
    monkeypatch.setattr("codebud.core.call_llm_plain", lambda *a, **k: pytest.fail("chatted"))
    result = agent.handle_user_message("list files")
    assert result["status"] == "llm_unavailable"
    assert "not reachable" in result["error"]


def test_chat_fallback_losing_the_backend_is_reported(agent, monkeypatch):
    """The plain-reply fallback can also find the backend gone."""
    monkeypatch.setattr(
        planner_mod, "call_llm", lambda prompt, on_chunk=None: json.dumps({"status": "plan_error"})
    )

    def plain(*a, **k):
        raise llm_stub.LLMUnavailable("Ollama is not reachable at http://localhost:11434")

    monkeypatch.setattr("codebud.core.call_llm_plain", plain)
    result = agent.handle_user_message("hello")
    assert result["status"] == "llm_unavailable"


def test_backend_is_an_egress_setting():
    setting = settings.BY_KEY["backend"]
    assert (setting.env, setting.default, setting.egress) == ("CODEBUD_BACKEND", "ollama", True)


def test_doctor_names_the_backend(monkeypatch):
    monkeypatch.delenv("CODEBUD_BACKEND", raising=False)
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
    passed, label, _hint = doctor.check_backend()
    assert passed is True
    assert label == "LLM backend: ollama at http://localhost:11434 (local)"


def test_doctor_on_openai_without_a_key_fails(monkeypatch):
    monkeypatch.setenv("CODEBUD_BACKEND", "openai")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    passed, label, hint = doctor.check_backend()
    assert passed is False
    assert "OPENAI_API_KEY" in hint


def test_doctor_on_openai_says_prompts_leave(monkeypatch):
    monkeypatch.setenv("CODEBUD_BACKEND", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    passed, label, _hint = doctor.check_backend()
    assert passed is True
    assert "leave this machine" in label


def test_doctor_rejects_an_unknown_backend(monkeypatch):
    monkeypatch.setenv("CODEBUD_BACKEND", "anthropic")
    passed, _label, hint = doctor.check_backend()
    assert passed is False
    assert "ollama or openai" in hint


def test_error_hint_points_at_doctor():
    _explanation, fix = explain("Ollama is not reachable at http://localhost:11434 (...)")
    assert "codebud doctor" in fix or "ollama serve" in fix


def test_run_plan_and_chat_announce_a_cloud_backend(monkeypatch, capsys):
    from codebud.cli import main as cli

    monkeypatch.setenv("CODEBUD_BACKEND", "openai")
    cli._announce_backend()
    assert "prompts leave this machine" in capsys.readouterr().err
    monkeypatch.setenv("CODEBUD_BACKEND", "ollama")
    cli._announce_backend()
    assert capsys.readouterr().err == ""


def test_toolbar_marks_the_cloud_backend(monkeypatch):
    from codebud.cli.chat_input import ChatInput

    class Agent:
        def get_mode(self):
            return "ask"

        def get_model(self):
            return "qwen"

    reader = ChatInput(Agent(), interactive=False)
    monkeypatch.setenv("CODEBUD_BACKEND", "openai")
    assert "☁ OpenAI gpt-3.5-turbo" in reader._toolbar()
    monkeypatch.setenv("CODEBUD_BACKEND", "ollama")
    assert "qwen" in reader._toolbar() and "OpenAI" not in reader._toolbar()
