"""
tests/test_embeddings.py

The offline stub embedding is for tests only and must be deterministic;
at runtime an unreachable embedding model is an error, never a silent
switch to fake vectors (phase 9, step A1).
"""

import json
import os
import subprocess
import sys

import pytest

from codebud.tools import embeddings


def _stub_in_fresh_process(hashseed: str) -> list[float]:
    code = "import json; from codebud.tools import embeddings; print(json.dumps(embeddings.stub_embedding('hello')))"
    env = {**os.environ, "PYTHONHASHSEED": hashseed}
    out = subprocess.run(
        [sys.executable, "-c", code], env=env, capture_output=True, text=True, check=True
    )
    return json.loads(out.stdout)


def test_stub_is_the_same_in_every_process():
    """hash() is salted per process; the stub used to differ between runs."""
    assert _stub_in_fresh_process("1") == _stub_in_fresh_process("2")


def test_stub_vectors_are_unit_length_and_text_dependent():
    a = embeddings.stub_embedding("hello")
    b = embeddings.stub_embedding("world")
    assert a != b
    assert sum(x * x for x in a) == pytest.approx(1.0)


def _ollama_down(monkeypatch):
    def fail(*_args, **_kwargs):
        raise embeddings.requests.ConnectionError("refused")

    monkeypatch.setattr(embeddings.requests, "post", fail)


def test_runtime_failure_raises_instead_of_using_the_stub(monkeypatch):
    monkeypatch.delenv("CODEBUD_EMBED_STUB", raising=False)
    _ollama_down(monkeypatch)
    with pytest.raises(embeddings.EmbeddingUnavailable, match="ollama pull"):
        embeddings.get_embedding("text")


def test_stub_is_used_only_when_asked_for(monkeypatch):
    monkeypatch.setenv("CODEBUD_EMBED_STUB", "1")

    def no_network(*_args, **_kwargs):
        raise AssertionError("the stub must not call Ollama")

    monkeypatch.setattr(embeddings.requests, "post", no_network)
    assert embeddings.get_embedding("text") == embeddings.stub_embedding("text")


def test_tools_report_an_unavailable_model_as_an_error(tmp_path, monkeypatch):
    monkeypatch.delenv("CODEBUD_EMBED_STUB", raising=False)
    monkeypatch.setattr(embeddings, "EMBEDDING_INDEX_PATH", str(tmp_path / "index.json"))
    _ollama_down(monkeypatch)

    embed = embeddings.EmbedTool().run({"text": "x"})
    search = embeddings.EmbeddingSearchTool().run({"query": "x"})
    for result in (embed, search):
        assert result["returncode"] == 1
        assert "Embedding model unavailable" in result["stderr"]
