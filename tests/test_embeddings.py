"""
tests/test_embeddings.py

The offline stub embedding is for tests only and must be deterministic;
at runtime an unreachable embedding model is an error, never a silent
switch to fake vectors (phase 9, step A1).
"""

import importlib.util
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
        embeddings.embed_query("text")


def test_stub_is_used_only_when_asked_for(monkeypatch):
    monkeypatch.setenv("CODEBUD_EMBED_STUB", "1")

    def no_network(*_args, **_kwargs):
        raise AssertionError("the stub must not call Ollama")

    monkeypatch.setattr(embeddings.requests, "post", no_network)
    assert embeddings.embed_query("text") == embeddings.stub_embedding("text")
    assert embeddings.embed_documents(["a", "b"]) == [
        embeddings.stub_embedding("a"),
        embeddings.stub_embedding("b"),
    ]


# --- the search_embeddings tool (phase 9, C2) --------------------------------

needs_numpy = pytest.mark.skipif(
    importlib.util.find_spec("numpy") is None, reason="numpy (codebud[rag]) not installed"
)


@pytest.fixture
def indexed_project(tmp_path, monkeypatch):
    from codebud.rag import indexer

    root = tmp_path / "proj"
    root.mkdir()
    for i in range(4):
        (root / f"m{i}.py").write_text(f"def func{i}():\n    return {i}\n")
    indexer.index_project(root)
    monkeypatch.chdir(root)
    return root


@needs_numpy
def test_search_tool_returns_located_chunks(indexed_project):
    result = embeddings.EmbeddingSearchTool().run({"query": "func2", "top_k": 2})
    assert result["returncode"] == 0, result["stderr"]
    hits = json.loads(result["stdout"])
    assert len(hits) == 2
    assert hits[0] == {
        "path": "m2.py",
        "lines": "1-2",
        "symbol": "func2",
        "score": hits[0]["score"],
        "text": "def func2():\n    return 2",
    }
    assert (
        len(json.loads(embeddings.EmbeddingSearchTool().run({"query": "x", "k": 3})["stdout"])) == 3
    )


@needs_numpy
def test_search_tool_without_an_index_says_how_to_build_one(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = embeddings.EmbeddingSearchTool().run({"query": "x"})
    assert result["returncode"] == 1
    assert "codebud index" in result["stderr"]


@needs_numpy
def test_search_reports_an_unavailable_model_as_an_error(tmp_path, monkeypatch):
    from codebud.rag.store import IndexStore

    monkeypatch.delenv("CODEBUD_EMBED_STUB", raising=False)
    monkeypatch.delenv("EMBED_MODEL", raising=False)
    with IndexStore.open(tmp_path, create=True) as store:
        store.ensure_model("all-minilm", 16)
        store.replace_file("a.py", "h", [(_any_chunk(), [1.0] * 16)])
    monkeypatch.chdir(tmp_path)
    _ollama_down(monkeypatch)

    result = embeddings.EmbeddingSearchTool().run({"query": "x"})
    assert result["returncode"] == 1
    assert "Embedding model unavailable" in result["stderr"]


def _any_chunk():
    from codebud.rag.chunker import Chunk

    return Chunk("a.py", 1, 1, None, "python", "x = 1")


def test_planner_is_not_offered_the_embed_tool():
    """embed returned a vector the planner could not use (D13f): removed."""
    from codebud.tools.tool_registry import ToolRegistry

    registry = ToolRegistry(sandbox=None)
    assert not registry.has("embed")
    assert registry.has("search_embeddings")
    assert "- embed:" not in registry.describe_tools()


# --- embedding model selection (phase 9, A2) --------------------------------


def test_default_embed_model_is_a_dedicated_embedding_model(monkeypatch):
    """It used to fall back to OLLAMA_MODEL, a text-generation model."""
    monkeypatch.delenv("EMBED_MODEL", raising=False)
    monkeypatch.setenv("OLLAMA_MODEL", "qwen2.5-coder:3b-instruct-q4_K_M")
    assert embeddings.embed_model() == "all-minilm"


def test_embed_model_env_is_read_at_call_time(monkeypatch):
    monkeypatch.setenv("EMBED_MODEL", "qwen3-embedding:0.6b")
    assert embeddings.embed_model() == "qwen3-embedding:0.6b"


def test_unavailable_message_names_the_configured_model(monkeypatch):
    monkeypatch.delenv("CODEBUD_EMBED_STUB", raising=False)
    monkeypatch.setenv("EMBED_MODEL", "my-embedder")
    _ollama_down(monkeypatch)
    with pytest.raises(embeddings.EmbeddingUnavailable, match="ollama pull my-embedder"):
        embeddings.embed_query("text")


def _tags(monkeypatch, names):
    from codebud.cli import doctor

    class Reply:
        status_code = 200

        def json(self):
            return {"models": [{"name": n} for n in names]}

    monkeypatch.setattr(doctor.requests, "get", lambda *a, **k: Reply())
    return doctor


@pytest.mark.parametrize("pulled", ["all-minilm", "all-minilm:latest"])
def test_doctor_passes_when_embed_model_is_pulled(monkeypatch, pulled):
    monkeypatch.delenv("EMBED_MODEL", raising=False)
    doctor = _tags(monkeypatch, [pulled])
    passed, label, _hint = doctor.check_embed_model_pulled()
    assert passed is True
    assert "all-minilm" in label


def test_doctor_only_warns_when_embed_model_is_missing(monkeypatch, capsys):
    """Project search is optional, so a missing embedding model is no failure."""
    monkeypatch.delenv("EMBED_MODEL", raising=False)
    monkeypatch.setenv("OLLAMA_MODEL", "qwen2.5-coder:3b")
    doctor = _tags(monkeypatch, ["qwen2.5-coder:3b"])
    passed, _label, hint = doctor.check_embed_model_pulled()
    assert passed is None
    assert hint == "ollama pull all-minilm"

    assert doctor.run_doctor() == 0
    out = capsys.readouterr().out
    assert "[WARN]" in out
    assert "ollama pull all-minilm" in out


def test_config_shows_the_resolved_embed_model(monkeypatch, capsys):
    from codebud.cli.main import cmd_config

    monkeypatch.delenv("EMBED_MODEL", raising=False)
    monkeypatch.setenv("OLLAMA_MODEL", "qwen2.5-coder:3b")
    cmd_config(None)
    line = next(ln for ln in capsys.readouterr().out.splitlines() if "EMBED_MODEL" in ln)
    assert line.endswith("= all-minilm")
