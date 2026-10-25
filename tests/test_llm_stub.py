import sys
import types

from codebud import llm_stub


def test_no_backend(monkeypatch, caplog):
    monkeypatch.delenv("OLLAMA_MODEL", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    caplog.set_level("WARNING")
    output = llm_stub.call_llm("hello")
    assert output == ""
    assert "No LLM backend configured" in caplog.text


def test_ollama_path(monkeypatch):
    monkeypatch.setenv("OLLAMA_MODEL", "foo-model")
    monkeypatch.setattr(llm_stub, "_ollama_available", lambda: True)
    called = {}

    def fake_call(model, prompt, timeout, on_chunk=None):
        called["model"] = model
        called["prompt"] = prompt
        return "ollama-output"

    monkeypatch.setattr(llm_stub, "_call_ollama", fake_call)

    result = llm_stub.call_llm("my prompt")
    assert result == "ollama-output"
    assert called["model"] == "foo-model"
    assert called["prompt"] == "my prompt"


def test_openai_fallback(monkeypatch):
    monkeypatch.delenv("OLLAMA_MODEL", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "testkey")

    # Build a minimal mock that matches the openai v1 client interface.
    choice = types.SimpleNamespace(message=types.SimpleNamespace(content="resp-text"))
    completion = types.SimpleNamespace(choices=[choice])

    class FakeCompletions:
        def create(self, **kwargs):
            return completion

    class FakeChat:
        completions = FakeCompletions()

    class FakeClient:
        chat = FakeChat()

    fake_openai = types.ModuleType("openai")
    fake_openai.OpenAI = lambda: FakeClient()

    monkeypatch.setitem(sys.modules, "openai", fake_openai)

    result = llm_stub.call_llm("prompt for openai")
    assert result == "resp-text"


def test_ollama_unavailable_falls_through(monkeypatch, caplog):
    """If OLLAMA_MODEL is set but server is down, fall through to warning."""
    monkeypatch.setenv("OLLAMA_MODEL", "some-model")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(llm_stub, "_ollama_available", lambda: False)

    caplog.set_level("WARNING")
    result = llm_stub.call_llm("hello")
    assert result == ""
    assert "No LLM backend configured" in caplog.text


def test_list_models_returns_names(monkeypatch):
    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"models": [{"name": "a"}, {"name": "b"}]}

    monkeypatch.setattr(llm_stub.requests, "get", lambda *a, **kw: FakeResponse())
    assert llm_stub.list_models() == ["a", "b"]


def test_list_models_empty_on_failure(monkeypatch):
    def boom(*a, **kw):
        raise ConnectionError("no server")

    monkeypatch.setattr(llm_stub.requests, "get", boom)
    assert llm_stub.list_models() == []


def test_warm_model_sync_success(monkeypatch):
    monkeypatch.setattr(llm_stub, "_ollama_available", lambda: True)

    class FakeResponse:
        def raise_for_status(self):
            pass

        def iter_lines(self):
            import json as _json

            yield _json.dumps({"response": "h", "done": False}).encode()
            yield _json.dumps({"response": "i", "done": True}).encode()

    monkeypatch.setattr(llm_stub.requests, "post", lambda *a, **kw: FakeResponse())

    chunks = []
    ok = llm_stub.warm_model_sync("some-model", on_chunk=chunks.append)
    assert ok is True
    assert chunks == ["h", "i"]


def test_warm_model_sync_unreachable_server(monkeypatch):
    monkeypatch.setattr(llm_stub, "_ollama_available", lambda: False)
    assert llm_stub.warm_model_sync("some-model") is False


def test_warm_model_sync_request_failure(monkeypatch):
    monkeypatch.setattr(llm_stub, "_ollama_available", lambda: True)

    def boom(*a, **kw):
        raise ConnectionError("dropped")

    monkeypatch.setattr(llm_stub.requests, "post", boom)
    assert llm_stub.warm_model_sync("some-model") is False


def test_summarize_conversation_sends_turns_and_strips_reply(monkeypatch):
    captured = {}

    def fake_call_llm(prompt, timeout=600.0, on_chunk=None):
        captured["prompt"] = prompt
        return "  A short summary.  \n"

    monkeypatch.setattr(llm_stub, "call_llm", fake_call_llm)
    turns = [
        {"role": "user", "content": "list the python files"},
        {"role": "assistant", "content": "planned: command(list python files)"},
    ]
    assert llm_stub.summarize_conversation(turns) == "A short summary."
    assert "User: list the python files" in captured["prompt"]
    assert "Assistant: planned: command(list python files)" in captured["prompt"]
