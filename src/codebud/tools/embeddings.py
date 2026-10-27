"""
codebud/tools/embeddings.py

Embeds text with Ollama's /api/embed endpoint, and provides the
EmbeddingSearchTool ("search_embeddings"): load stored embeddings from the
embedding index in the data directory, embed the query, compute cosine
similarity, and return top-k matches.  If the embedding model is
unreachable, an error is reported; fake vectors are never substituted.

Environment variables:
  OLLAMA_BASE_URL   Base URL of the Ollama server (default: http://localhost:11434)
  EMBED_MODEL       Embedding model (default: nomic-embed-text).  A dedicated
                    embedding model, not the text-generation OLLAMA_MODEL.
  CODEBUD_EMBED_STUB  Tests only: "1" uses a deterministic offline stub
                    instead of Ollama.
"""

import hashlib
import json
import logging
import math
import os
from typing import Any

import requests

from codebud import paths
from codebud.llm_stub import num_ctx, ollama_proxies

logger = logging.getLogger(__name__)

# Overridable for tests; None means the default location in the data dir.
EMBEDDING_INDEX_PATH: str | None = None


def _index_path() -> str:
    """Return the embedding index file (EMBEDDING_INDEX_PATH, else the data dir's)."""
    return EMBEDDING_INDEX_PATH or str(paths.data_dir() / "embeddings" / "index.json")


_OLLAMA_BASE = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
DEFAULT_EMBED_MODEL = "nomic-embed-text"


def embed_model() -> str:
    """Return the embedding model: EMBED_MODEL, else DEFAULT_EMBED_MODEL."""
    return os.environ.get("EMBED_MODEL") or DEFAULT_EMBED_MODEL


# ---------------------------------------------------------------------------
# Embedding backends
# ---------------------------------------------------------------------------


def ollama_embed(text: str, model: str | None = None) -> list[float]:
    """Return a real embedding vector from the Ollama /api/embed endpoint.

    Raises RuntimeError if the server is unreachable or returns an error,
    so callers can fall back to the stub if needed.
    """
    m = model or embed_model()
    r = requests.post(
        f"{_OLLAMA_BASE}/api/embed",
        json={"model": m, "input": text, "options": {"num_ctx": num_ctx()}},
        timeout=30,
        proxies=ollama_proxies(_OLLAMA_BASE),
    )
    r.raise_for_status()
    embeddings = r.json().get("embeddings", [[]])
    if not embeddings or not embeddings[0]:
        raise RuntimeError("Ollama returned empty embedding")
    return embeddings[0]


class EmbeddingUnavailable(RuntimeError):
    """The embedding model could not be reached or returned nothing."""


def stub_embedding(text: str) -> list[float]:
    """Deterministic 16-float unit vector from SHA-256 (tests only).

    Unlike hash(), SHA-256 is not salted per process, so the same text
    always gets the same vector.
    """
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    raw = [b - 127.5 for b in digest[:16]]
    norm = math.sqrt(sum(x * x for x in raw)) or 1.0
    return [x / norm for x in raw]


def _stub_enabled() -> bool:
    return os.environ.get("CODEBUD_EMBED_STUB") == "1"


def get_embedding(text: str) -> list[float]:
    """Embed text with Ollama (or the stub when CODEBUD_EMBED_STUB=1).

    Raises EmbeddingUnavailable when the model cannot be used; callers must
    not invent vectors, since mixing them with real ones corrupts an index.
    """
    if _stub_enabled():
        return stub_embedding(text)
    try:
        return ollama_embed(text)
    except Exception as exc:
        raise EmbeddingUnavailable(
            f"Embedding model unavailable ({embed_model()} at {_OLLAMA_BASE}): {exc}. "
            f"Start Ollama and run: ollama pull {embed_model()}"
        ) from exc


# ---------------------------------------------------------------------------
# Similarity
# ---------------------------------------------------------------------------


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        # Dimension mismatch (e.g. vectors from different models in one index)
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


class EmbeddingSearchTool:
    """Search stored embeddings using cosine similarity."""

    description = "Search stored embeddings and return top-k matches."
    usage_hint = 'Required: "query" (str). Optional: "top_k" (int). Returns closest stored entries.'

    def __init__(self):
        self._ensure_storage()
        self.index = self._load_index()

    def run(self, args: dict[str, Any]) -> dict[str, Any]:
        query = args.get("query")
        # "top_k" is the name in usage_hint; "k" is accepted for compatibility.
        k = args.get("top_k", args.get("k", 5))
        if not query:
            return {"stdout": "", "stderr": "Missing required argument: query", "returncode": 1}

        try:
            query_vec = get_embedding(query)
        except EmbeddingUnavailable as exc:
            return {"stdout": "", "stderr": str(exc), "returncode": 1}

        scored = []
        for entry in self.index:
            sim = cosine_similarity(query_vec, entry.get("vector", []))
            scored.append((sim, entry))

        scored.sort(key=lambda x: x[0], reverse=True)
        top_k = [entry for _, entry in scored[:k]]

        return {"stdout": json.dumps(top_k, indent=2), "stderr": "", "returncode": 0}

    def _ensure_storage(self):
        os.makedirs(os.path.dirname(_index_path()), exist_ok=True)
        if not os.path.exists(_index_path()):
            with open(_index_path(), "w") as f:
                json.dump([], f, indent=2)

    def _load_index(self):
        try:
            with open(_index_path()) as f:
                return json.load(f)
        except Exception:
            return []
