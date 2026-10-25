"""
tests/test_modes.py

Agent modes (backlog item 6, decision D1): ask (default), auto, plan, edit.
Restrictions are enforced in code, not only described to the model:

- edit: only file_read / file_write / patch steps pass the SafetyEngine,
  both when a plan is validated and again right before execution;
- plan: plans may be made, but the Executor refuses to run any step.
"""

import pytest

from agent import modes
from agent.executor import Executor
from agent.safety import SafetyEngine

_CMD = {"id": "step_0", "description": "list", "tool": "command", "args": {"cmd": "ls"}}
_WRITE = {
    "id": "step_0",
    "description": "write",
    "tool": "file_write",
    "args": {"path": "a.txt", "content": "x"},
}


def _safety(mode=modes.DEFAULT_MODE):
    engine = SafetyEngine.__new__(SafetyEngine)
    engine.harmless_commands = ["ls"]
    engine.powerful_commands = []
    engine.trusted_sequences = []
    engine.mode = mode
    return engine


class _Tool:
    def __init__(self):
        self.ran = False

    def run(self, args):
        self.ran = True
        return {"stdout": "ok", "stderr": "", "returncode": 0}


def _executor(safety, tool):
    class Registry:
        def get(self, name):
            return tool

    class Memory:
        def add_snapshot(self, step, result):
            pass

    return Executor(Registry(), object(), Memory(), safety=safety)


def test_modes_list_and_default():
    assert modes.MODES == ("ask", "auto", "plan", "edit")
    assert modes.DEFAULT_MODE == "ask"


@pytest.mark.parametrize("mode", ["ask", "auto", "plan"])
def test_commands_pass_validation_outside_edit_mode(mode):
    assert _safety(mode).validate_step(_CMD) is True


def test_edit_mode_rejects_commands_but_allows_file_tools():
    engine = _safety("edit")
    assert engine.validate_step(_CMD) is False
    assert engine.validate_step(_WRITE) is True


def test_plan_mode_executor_refuses_to_run_anything():
    tool = _Tool()
    result = _executor(_safety("plan"), tool).execute_plan({"plan": [_WRITE]})
    assert result["status"] == "step_refused"
    assert "plan mode" in result["error"]
    assert tool.ran is False


def test_edit_mode_executor_refuses_command_planned_earlier():
    """A plan validated in ask mode must not run after switching to edit."""
    tool = _Tool()
    result = _executor(_safety("edit"), tool).execute_plan({"plan": [_CMD]})
    assert result["status"] == "step_refused"
    assert tool.ran is False


def test_ask_mode_executor_runs_steps():
    tool = _Tool()
    result = _executor(_safety("ask"), tool).execute_plan({"plan": [_CMD]})
    assert result["status"] == "ok"
    assert tool.ran is True


def test_planner_prompt_mentions_edit_mode_only():
    from unittest.mock import MagicMock

    from agent.planner import LLMPlanner

    registry = MagicMock()
    registry.describe_tools.return_value = "- command\n- file_write"
    ask_prompt = LLMPlanner(registry, _safety("ask"))._build_prompt("do it")
    plain_prompt = LLMPlanner(registry, _safety("auto"))._build_prompt("do it")
    edit_prompt = LLMPlanner(registry, _safety("edit"))._build_prompt("do it")

    assert ask_prompt == plain_prompt  # cache-friendly: no mode text unless needed
    assert "MODE: edit" not in ask_prompt
    assert "MODE: edit" in edit_prompt
    assert "file_read, file_write, patch" in edit_prompt


def test_agent_core_set_mode(tmp_path, monkeypatch):
    from agent import memory as memory_mod
    from agent.core import AgentCore

    monkeypatch.setattr(memory_mod, "MEMORY_PATH", str(tmp_path / "entries.jsonl"))
    monkeypatch.setattr("agent.core.prewarm_model", lambda: None)
    monkeypatch.setattr("agent.core.ModelHeartbeat.start", lambda self: None)
    monkeypatch.setattr("agent.core.ModelHeartbeat.stop", lambda self: None)
    agent = AgentCore()

    assert agent.get_mode() == "ask"
    assert agent.set_mode("edit") == (True, "Mode: edit")
    assert agent.get_mode() == "edit"
    assert agent.safety.mode == "edit"
    ok, msg = agent.set_mode("yolo")
    assert ok is False
    assert "ask, auto, plan, edit" in msg
    assert agent.get_mode() == "edit"


def test_edit_mode_refusal_is_reported_as_such(monkeypatch):
    import json
    from unittest.mock import MagicMock

    from agent.planner import LLMPlanner

    registry = MagicMock()
    registry.has.return_value = True
    registry.describe_tools.return_value = "- command"
    plan = {"status": "ok", "plan": [_CMD]}
    monkeypatch.setattr("agent.planner.call_llm", lambda *a, **kw: json.dumps(plan))

    result = LLMPlanner(registry, _safety("edit")).generate_plan("run the tests")

    assert result["status"] == "plan_refused"
    assert "edit mode" in result["error"]
    assert "command" in result["error"]


def test_core_shows_mode_refusal_instead_of_chatting(tmp_path, monkeypatch):
    from agent import memory as memory_mod
    from agent.core import AgentCore

    monkeypatch.setattr(memory_mod, "MEMORY_PATH", str(tmp_path / "entries.jsonl"))
    monkeypatch.setattr("agent.core.prewarm_model", lambda: None)
    monkeypatch.setattr("agent.core.ModelHeartbeat.start", lambda self: None)
    monkeypatch.setattr("agent.core.ModelHeartbeat.stop", lambda self: None)

    def no_chat(*a, **kw):
        raise AssertionError("a mode refusal must not fall back to a chat reply")

    monkeypatch.setattr("agent.core.call_llm_plain", no_chat)
    agent = AgentCore()
    refused = {"status": "plan_refused", "error": "not allowed in edit mode: command"}
    monkeypatch.setattr(agent.planner, "generate_plan", lambda *a, **kw: refused)

    assert agent.handle_user_message("run the tests") == refused
