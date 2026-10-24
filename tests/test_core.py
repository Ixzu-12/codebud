"""
tests/test_core.py

Unit tests for AgentCore.set_model / get_model (model selection & loading).
"""

from agent import memory as memory_mod


def _make_agent(tmp_path, monkeypatch):
    from agent.core import AgentCore

    monkeypatch.setattr(memory_mod, "MEMORY_PATH", str(tmp_path / "entries.jsonl"))
    monkeypatch.setattr("agent.core.prewarm_model", lambda: None)
    monkeypatch.setattr("agent.core.ModelHeartbeat.start", lambda self: None)
    monkeypatch.setattr("agent.core.ModelHeartbeat.stop", lambda self: None)
    return AgentCore()


def test_get_model_reflects_env(tmp_path, monkeypatch):
    monkeypatch.setenv("OLLAMA_MODEL", "env-model")
    agent = _make_agent(tmp_path, monkeypatch)
    assert agent.get_model() == "env-model"


def test_get_model_empty_when_unset(tmp_path, monkeypatch):
    monkeypatch.delenv("OLLAMA_MODEL", raising=False)
    agent = _make_agent(tmp_path, monkeypatch)
    assert agent.get_model() == ""


def test_set_model_success_updates_env_and_warms(tmp_path, monkeypatch):
    monkeypatch.setenv("OLLAMA_MODEL", "old-model")
    agent = _make_agent(tmp_path, monkeypatch)

    monkeypatch.setattr("agent.core.list_models", lambda: ["old-model", "new-model"])
    chunks = []
    warmed = {}

    def fake_warm(model, on_chunk=None):
        warmed["model"] = model
        if on_chunk:
            on_chunk("x")
        return True

    monkeypatch.setattr("agent.core.warm_model_sync", fake_warm)

    ok, msg = agent.set_model("new-model", on_chunk=chunks.append)
    assert ok is True
    assert "new-model" in msg
    assert agent.get_model() == "new-model"
    assert warmed["model"] == "new-model"
    assert chunks == ["x"]


def test_set_model_rejects_not_downloaded(tmp_path, monkeypatch):
    monkeypatch.setenv("OLLAMA_MODEL", "old-model")
    agent = _make_agent(tmp_path, monkeypatch)

    monkeypatch.setattr("agent.core.list_models", lambda: ["old-model"])

    ok, msg = agent.set_model("missing-model")
    assert ok is False
    assert "not downloaded" in msg
    assert "ollama pull missing-model" in msg
    assert agent.get_model() == "old-model"


def test_set_model_never_auto_pulls(tmp_path, monkeypatch):
    """Rejecting an undownloaded model must not attempt to fetch it."""
    monkeypatch.setenv("OLLAMA_MODEL", "old-model")
    agent = _make_agent(tmp_path, monkeypatch)

    monkeypatch.setattr("agent.core.list_models", lambda: ["old-model"])

    def fail_warm(*a, **kw):
        raise AssertionError("warm_model_sync should not be called for an undownloaded model")

    monkeypatch.setattr("agent.core.warm_model_sync", fail_warm)

    ok, _ = agent.set_model("missing-model")
    assert ok is False


def test_set_model_reverts_on_warmup_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("OLLAMA_MODEL", "old-model")
    agent = _make_agent(tmp_path, monkeypatch)

    monkeypatch.setattr("agent.core.list_models", lambda: ["old-model", "new-model"])
    monkeypatch.setattr("agent.core.warm_model_sync", lambda model, on_chunk=None: False)

    ok, msg = agent.set_model("new-model")
    assert ok is False
    assert "Failed to warm up" in msg
    assert agent.get_model() == "old-model"


def test_set_model_reports_unreachable_ollama(tmp_path, monkeypatch):
    monkeypatch.setenv("OLLAMA_MODEL", "old-model")
    agent = _make_agent(tmp_path, monkeypatch)

    monkeypatch.setattr("agent.core.list_models", lambda: [])

    ok, msg = agent.set_model("new-model")
    assert ok is False
    assert "reach Ollama" in msg
    assert agent.get_model() == "old-model"
