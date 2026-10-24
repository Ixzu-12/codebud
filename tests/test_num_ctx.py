"""
tests/test_num_ctx.py

Every Ollama request must carry the same explicit num_ctx. Without it
Ollama loads the model with a small default context (4096 on 0.40) and
silently drops the *start* of longer prompts, which is where the
planner's instructions are. A request with a different num_ctx would also
make Ollama reload the model and lose the cached prompt prefix, so the
heartbeat and warmup calls must match the planner's value exactly.
"""

import json

import pytest

from agent import llm_stub


class _FakeResponse:
    def raise_for_status(self):
        pass

    def iter_lines(self):
        yield json.dumps({"response": "{}", "done": True}).encode()


@pytest.fixture
def sent(monkeypatch):
    payloads = []

    def fake_post(url, json=None, **kwargs):
        payloads.append(json)
        return _FakeResponse()

    class _InlineThread:
        def __init__(self, target, **kwargs):
            self._target = target

        def start(self):
            self._target()

    monkeypatch.setenv("OLLAMA_MODEL", "m")
    monkeypatch.delenv("CODEBUD_NUM_CTX", raising=False)
    monkeypatch.setattr(llm_stub, "_ollama_available", lambda: True)
    monkeypatch.setattr(llm_stub.requests, "post", fake_post)
    monkeypatch.setattr(llm_stub.threading, "Thread", _InlineThread)
    return payloads


def _send_every_kind_of_request():
    llm_stub.call_llm("plan this")
    llm_stub.call_llm_plain("hello")
    llm_stub.warm_model_sync("m")
    llm_stub.prewarm_model()
    llm_stub.ModelHeartbeat()._ping()


def test_every_request_sends_the_default_num_ctx(sent):
    _send_every_kind_of_request()
    assert len(sent) == 5
    assert {p["options"]["num_ctx"] for p in sent} == {8192}


def test_num_ctx_can_be_overridden_by_environment(sent, monkeypatch):
    monkeypatch.setenv("CODEBUD_NUM_CTX", "16384")
    _send_every_kind_of_request()
    assert {p["options"]["num_ctx"] for p in sent} == {16384}


@pytest.mark.parametrize("bad", ["abc", "0", "-5", ""])
def test_invalid_num_ctx_falls_back_to_default(bad, monkeypatch, caplog):
    monkeypatch.setenv("CODEBUD_NUM_CTX", bad)
    caplog.set_level("WARNING")
    assert llm_stub.num_ctx() == 8192
    if bad:
        assert "CODEBUD_NUM_CTX" in caplog.text


def test_embed_requests_send_the_same_num_ctx(sent, monkeypatch):
    from agent.tools import embeddings

    class _EmbedResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"embeddings": [[0.1, 0.2]]}

    def fake_post(url, json=None, **kwargs):
        sent.append(json)
        return _EmbedResponse()

    monkeypatch.setattr(embeddings.requests, "post", fake_post)
    embeddings.ollama_embed("text", model="m")
    assert sent[-1]["options"]["num_ctx"] == 8192


def test_config_subcommand_shows_resolved_num_ctx(monkeypatch, capsys):
    import sys

    import run_agent

    monkeypatch.setenv("CODEBUD_NUM_CTX", "12288")
    monkeypatch.setattr(sys, "argv", ["codebud", "config"])
    run_agent.main()
    out = capsys.readouterr().out
    assert "CODEBUD_NUM_CTX" in out
    assert "12288" in out
