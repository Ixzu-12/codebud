"""
tests/test_plan_args.py

Plans whose steps miss a tool's required arguments (open product issue 5):
the planner asks the model once more, saying what was wrong, and reports
the plan as invalid if it is still wrong -- instead of accepting it and
failing at execution.
"""

import json

import pytest

from codebud import planner as planner_mod
from codebud.cli.errors import explain
from codebud.planner import LLMPlanner
from codebud.safety import SafetyEngine
from codebud.tools.tool_registry import ToolRegistry


def _step(tool, args, n=0):
    return {"id": f"step_{n}", "description": "d", "tool": tool, "args": args}


GOOD = {"status": "ok", "plan": [_step("file_read", {"path": "src/a.py"})]}
BAD = {
    "status": "ok",
    "plan": [
        _step("command", {"cmd": "ls"}),
        _step("file_read", {"file_path": "path/to/your/file"}, 1),
    ],
}


@pytest.fixture
def planner():
    safety = SafetyEngine.__new__(SafetyEngine)
    safety.harmless_commands = ["ls"]
    safety.powerful_commands = []
    safety.trusted_sequences = []
    return LLMPlanner(ToolRegistry(sandbox=None), safety)


@pytest.fixture
def answers(monkeypatch):
    """Queue model answers; record the prompts sent."""
    state = {"queue": [], "prompts": []}

    def fake_call(prompt, on_chunk=None):
        state["prompts"].append(prompt)
        return json.dumps(state["queue"].pop(0))

    monkeypatch.setattr(planner_mod, "call_llm", fake_call)
    return state


@pytest.mark.parametrize(
    "tool, args",
    [
        ("file_read", {"path": "a"}),
        ("file_write", {"path": "a"}),  # content defaults to ""
        ("command", {"cmd": "ls"}),
        ("patch", {"patch": "--- a/x\n+++ b/x\n"}),
        ("web_search", {"query": "q"}),
        ("search_embeddings", {"query": "q", "top_k": 3}),
    ],
)
def test_steps_with_their_required_arguments_pass(planner, tool, args):
    assert planner._arg_problems({"plan": [_step(tool, args)]}) == []


def test_missing_arguments_are_named_with_what_was_given(planner):
    assert planner._arg_problems(BAD) == ['step 2 (file_read) needs "path"; it gave file_path']
    assert planner._arg_problems({"plan": [_step("patch", {})]}) == [
        'step 1 (patch) needs "patch"; it gave no arguments'
    ]


def test_extra_arguments_alone_are_tolerated(planner):
    step = _step("file_read", {"path": "a", "encoding": "utf-8"})
    assert planner._arg_problems({"plan": [step]}) == []


def test_invalid_plan_gets_one_retry_with_feedback(planner, answers):
    answers["queue"] = [BAD, GOOD]
    assert planner.generate_plan("read the file") == GOOD
    first, second = answers["prompts"]
    assert second.startswith(first)  # same prefix: the prompt cache still applies
    assert 'step 2 (file_read) needs "path"; it gave file_path' in second[len(first) :]


def test_still_invalid_after_the_retry_is_reported(planner, answers):
    answers["queue"] = [BAD, BAD]
    result = planner.generate_plan("read the file")
    assert result["status"] == "plan_invalid"
    assert 'step 2 (file_read) needs "path"; it gave file_path' in result["error"]
    assert len(answers["prompts"]) == 2


def test_core_reports_an_invalid_plan_instead_of_chatting(tmp_path, monkeypatch):
    from codebud import memory as memory_mod
    from codebud.core import AgentCore

    monkeypatch.setattr(memory_mod, "MEMORY_PATH", str(tmp_path / "entries.jsonl"))
    monkeypatch.setattr("codebud.core.prewarm_model", lambda: None)
    monkeypatch.setattr("codebud.core.ModelHeartbeat.start", lambda self: None)
    monkeypatch.setattr("codebud.core.ModelHeartbeat.stop", lambda self: None)
    monkeypatch.setattr("codebud.core.call_llm_plain", lambda *a, **k: pytest.fail("must not chat"))
    agent = AgentCore()
    invalid = {"status": "plan_invalid", "error": "The model's plan is invalid: ..."}
    monkeypatch.setattr(agent.planner, "generate_plan", lambda *a, **k: invalid)
    assert agent.handle_user_message("read it") == invalid
    agent.close()


def test_error_hint_suggests_a_larger_model():
    _explanation, fix = explain('The model\'s plan is invalid: step 1 (file_read) needs "path"')
    assert "7b" in fix or "larger model" in fix
