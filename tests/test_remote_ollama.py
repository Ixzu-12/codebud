"""
tests/test_remote_ollama.py

Remote Ollama (phase 8, Part A2; decision D12a): prompts may go to another
machine over https with a notice, but over plain http only when explicitly
allowed, since they would cross the network unencrypted.
"""

import pytest

from codebud import llm_stub, settings
from codebud.cli import doctor
from codebud.tools import embeddings

REMOTE_HTTP = "http://192.168.1.50:11434"
REMOTE_HTTPS = "https://gpu-box.example:11434"


@pytest.fixture
def ollama_up(monkeypatch):
    monkeypatch.setenv("OLLAMA_MODEL", "m")
    monkeypatch.delenv("CODEBUD_ALLOW_REMOTE_HTTP", raising=False)
    monkeypatch.setattr(llm_stub, "_ollama_available", lambda: True)
    sent = []
    monkeypatch.setattr(
        llm_stub,
        "_call_ollama",
        lambda model, prompt, timeout, on_chunk=None: sent.append(prompt) or "ok",
    )
    return sent


@pytest.mark.parametrize(
    "url, remote",
    [
        ("http://localhost:11434", False),
        ("http://127.0.0.1:11434", False),
        ("http://127.1.2.3:11434", False),
        ("http://[::1]:11434", False),
        (REMOTE_HTTP, True),
        (REMOTE_HTTPS, True),
    ],
)
def test_which_urls_are_remote(url, remote):
    assert llm_stub.is_remote(url) is remote


def test_plain_http_to_another_machine_is_refused(monkeypatch, ollama_up):
    monkeypatch.setenv("OLLAMA_BASE_URL", REMOTE_HTTP)
    with pytest.raises(llm_stub.LLMUnavailable, match="unencrypted") as info:
        llm_stub.call_llm("secret prompt")
    assert "CODEBUD_ALLOW_REMOTE_HTTP=1" in str(info.value)
    assert ollama_up == []


def test_plain_http_can_be_allowed(monkeypatch, ollama_up):
    monkeypatch.setenv("OLLAMA_BASE_URL", REMOTE_HTTP)
    monkeypatch.setenv("CODEBUD_ALLOW_REMOTE_HTTP", "1")
    assert llm_stub.call_llm("prompt") == "ok"


@pytest.mark.parametrize("url", [REMOTE_HTTPS, "http://localhost:11434"])
def test_https_and_local_need_no_opt_in(monkeypatch, ollama_up, url):
    monkeypatch.setenv("OLLAMA_BASE_URL", url)
    assert llm_stub.call_llm("prompt") == "ok"


def test_embeddings_follow_the_same_rule(monkeypatch):
    monkeypatch.delenv("CODEBUD_EMBED_STUB", raising=False)
    monkeypatch.delenv("CODEBUD_ALLOW_REMOTE_HTTP", raising=False)
    monkeypatch.setenv("OLLAMA_BASE_URL", REMOTE_HTTP)
    monkeypatch.setattr(embeddings.requests, "post", lambda *a, **k: pytest.fail("sent"))
    with pytest.raises(embeddings.EmbeddingUnavailable, match="unencrypted"):
        embeddings.embed_query("which file has the secret?")


def test_remote_server_is_announced(monkeypatch, capsys):
    from codebud.cli import main as cli

    monkeypatch.delenv("CODEBUD_BACKEND", raising=False)
    monkeypatch.setenv("OLLAMA_BASE_URL", REMOTE_HTTPS)
    cli._announce_backend()
    err = capsys.readouterr().err
    assert "remote" in err and "gpu-box.example" in err
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:11434")
    cli._announce_backend()
    assert capsys.readouterr().err == ""


def test_doctor_fails_on_refused_plain_http(monkeypatch):
    monkeypatch.delenv("CODEBUD_BACKEND", raising=False)
    monkeypatch.delenv("CODEBUD_ALLOW_REMOTE_HTTP", raising=False)
    monkeypatch.setenv("OLLAMA_BASE_URL", REMOTE_HTTP)
    passed, label, hint = doctor.check_backend()
    assert passed is False
    assert "remote" in label
    assert "CODEBUD_ALLOW_REMOTE_HTTP" in hint and "https" in hint


def test_doctor_passes_remote_https(monkeypatch):
    monkeypatch.delenv("CODEBUD_BACKEND", raising=False)
    monkeypatch.setenv("OLLAMA_BASE_URL", REMOTE_HTTPS)
    passed, label, _hint = doctor.check_backend()
    assert passed is True
    assert label == f"LLM backend: ollama at {REMOTE_HTTPS} (remote)"


def test_toolbar_marks_a_remote_server(monkeypatch):
    from codebud.cli.chat_input import ChatInput

    class Agent:
        def get_mode(self):
            return "ask"

        def get_model(self):
            return "qwen"

    monkeypatch.delenv("CODEBUD_BACKEND", raising=False)
    monkeypatch.setenv("OLLAMA_BASE_URL", REMOTE_HTTPS)
    assert "qwen @ gpu-box.example" in ChatInput(Agent(), interactive=False)._toolbar()


def test_allow_remote_http_is_an_egress_setting():
    setting = settings.BY_KEY["allow_remote_http"]
    assert (setting.env, setting.default, setting.kind, setting.egress) == (
        "CODEBUD_ALLOW_REMOTE_HTTP",
        False,
        bool,
        True,
    )


def test_error_hint_offers_https_or_a_tunnel():
    from codebud.cli.errors import explain

    _explanation, fix = explain(
        "OLLAMA_BASE_URL http://192.168.1.50:11434 is another machine over plain http: ..."
    )
    assert "https" in fix and "ssh -L" in fix


def test_no_notice_for_a_refused_server(monkeypatch, capsys):
    from codebud.cli import main as cli

    monkeypatch.delenv("CODEBUD_BACKEND", raising=False)
    monkeypatch.delenv("CODEBUD_ALLOW_REMOTE_HTTP", raising=False)
    monkeypatch.setenv("OLLAMA_BASE_URL", REMOTE_HTTP)
    cli._announce_backend()
    assert capsys.readouterr().err == ""
