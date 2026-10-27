"""
codebud/llm_stub.py

Stub layer that hides the details of talking to an LLM.  The rest of the
codebase calls `call_llm(prompt)` and expects a string containing the raw
model output (usually JSON).  No parsing or error handling is performed by
this module.

The implementation is small and flexible:

* If `OLLAMA_MODEL` is set and the Ollama server (`OLLAMA_BASE_URL`) answers,
  we call its REST API (`/api/generate`, streamed), sending the same
  `num_ctx` on every request and bypassing HTTP proxies for a local server.
* Otherwise, if `OPENAI_API_KEY` is set we fall back to the OpenAI Python
  client (the optional extra `codebud[openai]`) with `OPENAI_MODEL` or
  `gpt-3.5-turbo` by default.
* If neither backend is configured, `call_llm` returns an empty string and
  logs a warning.

It also holds the helpers around the model: warm-up and heartbeat, model
listing, conversation summaries for /compact. No network operations are
performed unless a real model is requested.
"""

import logging
import os
import threading
from collections.abc import Callable
from urllib.parse import urlsplit

import requests

logger = logging.getLogger(__name__)

# Ollama default base URL; override with OLLAMA_BASE_URL if needed.
_OLLAMA_BASE = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")

# Context window requested from Ollama on every call. Must be identical for
# all requests: a different num_ctx makes Ollama reload the model and drop
# the cached prompt prefix. Override with CODEBUD_NUM_CTX.
_DEFAULT_NUM_CTX = 8192


def num_ctx() -> int:
    """Return the context window (tokens) to request from Ollama.

    Ollama's own default (4096 on 0.40) is small enough that long planner
    prompts get their beginning, i.e. the instructions, silently cut off.
    Invalid CODEBUD_NUM_CTX values fall back to the default with a warning.
    """
    raw = os.environ.get("CODEBUD_NUM_CTX", "")
    if not raw:
        return _DEFAULT_NUM_CTX
    try:
        value = int(raw)
    except ValueError:
        value = 0
    if value <= 0:
        logger.warning("Ignoring invalid CODEBUD_NUM_CTX=%r; using %d", raw, _DEFAULT_NUM_CTX)
        return _DEFAULT_NUM_CTX
    return value


_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}


def ollama_proxies(base: str) -> dict[str, None] | None:
    """Return the ``proxies`` argument for a request to the Ollama at ``base``.

    requests honours HTTP_PROXY even for localhost, so with a system-wide
    proxy (e.g. Privoxy) and no NO_PROXY, every call to a local Ollama would
    be sent to the proxy and fail. Loopback addresses therefore bypass any
    proxy; a remote Ollama keeps the environment's proxy settings (None).
    """
    if urlsplit(base).hostname in _LOOPBACK_HOSTS:
        return {"http": None, "https": None, "all": None}
    return None


def call_llm(
    prompt: str,
    timeout: float = 600.0,
    on_chunk: Callable[[str], None] | None = None,
) -> str:
    """Send ``prompt`` to the configured LLM and return the raw text output.

    Priority order:

    1. Ollama REST API if ``OLLAMA_MODEL`` is set and the server is reachable.
    2. OpenAI API if ``OPENAI_API_KEY`` is present.
    3. Otherwise return an empty string and log a warning.

    ``timeout`` is the maximum total wall-clock seconds to wait.  On CPU-only
    hardware prefill alone can take several minutes for a 300-token prompt, so
    the default is generous.  The Ollama call uses streaming internally so the
    HTTP read timeout never fires mid-generation.

    ``on_chunk`` is an optional callback that receives each text fragment as
    it arrives from the model.  The CLI uses this to drive the progress
    indicator without coupling the display code to this module.
    """

    ollama_model = os.environ.get("OLLAMA_MODEL")
    if ollama_model and _ollama_available():
        return _call_ollama(ollama_model, prompt, timeout, on_chunk=on_chunk)

    if os.environ.get("OPENAI_API_KEY"):
        try:
            from openai import OpenAI
        except ImportError:
            logger.warning(
                "OPENAI_API_KEY is set but the OpenAI client is not installed; "
                "run: pip install 'codebud[openai]'"
            )
            return ""
        try:
            client = OpenAI()
            model = os.environ.get("OPENAI_MODEL", "gpt-3.5-turbo")
            resp = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
            )
            return resp.choices[0].message.content or ""
        except Exception as exc:  # pragma: no cover
            logger.warning("OpenAI call failed: %s", exc)
            return ""

    logger.warning("No LLM backend configured (set OLLAMA_MODEL or OPENAI_API_KEY)")
    return ""


def call_llm_plain(
    user_message: str,
    history: list[dict[str, str]] | None = None,
    on_chunk: Callable[[str], None] | None = None,
    context_block: str = "",
) -> str:
    """Send a conversational (non-JSON) prompt to the LLM and return plain text.

    Used as a fallback when the planner cannot fulfil the user's request so the
    agent can still give a helpful natural-language reply.  ``context_block``
    is retrieved project context (codebud.rag.context.format_context); the
    model is asked to cite the files it uses from it.
    """
    history_lines = ""
    if history:
        history_lines = "\n".join(f"{t['role'].capitalize()}: {t['content']}" for t in history[-6:])
        history_lines = f"Conversation so far:\n{history_lines}\n\n"
    if context_block:
        context_block += (
            "When your answer relies on the project context, cite each file you use "
            "as path:start-end.\n\n"
        )

    prompt = (
        f"You are a helpful coding assistant.\n\n{history_lines}{context_block}"
        f"User: {user_message}\nAssistant:"
    )
    return call_llm(prompt, on_chunk=on_chunk)


def summarize_conversation(
    turns: list[dict[str, str]],
    on_chunk: Callable[[str], None] | None = None,
) -> str:
    """Ask the LLM for a short summary of ``turns`` (used by /compact in chat).

    Returns the summary text, or '' if the model gave no answer.
    """
    transcript = "\n".join(f"{t['role'].capitalize()}: {t['content']}" for t in turns)
    prompt = (
        "Summarize the following conversation between a user and a coding agent "
        "in a few sentences. Keep file names, commands, decisions and anything "
        "the user may refer back to. Output only the summary.\n\n"
        f"{transcript}\n\nSummary:"
    )
    return call_llm(prompt, on_chunk=on_chunk).strip()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class ModelHeartbeat:
    """Keeps the Ollama model loaded by pinging it every ``interval`` seconds.

    Ollama unloads a model after it has been idle for its keep_alive window
    (default 5 minutes).  While a session is active we don't want that to
    happen between requests, so this class runs a daemon thread that sends a
    zero-token generate call before each idle-timeout window closes.

    Usage::

        hb = ModelHeartbeat()
        hb.start()          # begin pinging
        ...                 # session work
        hb.stop()           # stop pinging; Ollama will unload on its own schedule
    """

    def __init__(self, interval: float = 240.0):
        self._interval = interval  # ping every 4 min (< 5 min Ollama default)
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        model = os.environ.get("OLLAMA_MODEL")
        if not model or not _ollama_available():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def _loop(self) -> None:
        while not self._stop_event.wait(timeout=self._interval):
            self._ping()

    def _ping(self) -> None:
        model = os.environ.get("OLLAMA_MODEL")
        if not model:
            return
        try:
            requests.post(
                f"{_OLLAMA_BASE}/api/generate",
                proxies=ollama_proxies(_OLLAMA_BASE),
                json={
                    "model": model,
                    "prompt": "hi",
                    "stream": False,
                    "options": {"num_predict": 2, "num_ctx": num_ctx()},
                },
                timeout=(5, 60),
            )
        except Exception:
            pass  # server went away; AgentCore will surface the error on next real call


def prewarm_model() -> None:
    """Fire a tiny real request in a background daemon thread.

    Call this as early as possible in the session (e.g. in AgentCore.__init__)
    so the model is loaded into GPU/CPU memory before the user's first message
    arrives.  The thread is a daemon so it never blocks interpreter exit.
    """
    model = os.environ.get("OLLAMA_MODEL")
    if not model or not _ollama_available():
        return

    def _warm() -> None:
        try:
            logger.debug("Pre-warming model %s", model)
            requests.post(
                f"{_OLLAMA_BASE}/api/generate",
                proxies=ollama_proxies(_OLLAMA_BASE),
                json={
                    "model": model,
                    "prompt": "hi",
                    "stream": False,
                    "options": {"num_predict": 2, "num_ctx": num_ctx()},
                },
                timeout=(10, 120),
            )
            logger.debug("Pre-warmup complete")
        except Exception as exc:
            logger.debug("Pre-warmup failed (non-fatal): %s", exc)

    t = threading.Thread(target=_warm, daemon=True, name="codebud-prewarm")
    t.start()


def warm_model_sync(
    model: str,
    on_chunk: Callable[[str], None] | None = None,
    timeout: float = 120.0,
) -> bool:
    """Synchronously load ``model`` into memory, streaming so on_chunk can
    drive a live progress indicator. Returns True once the model responds.

    Unlike prewarm_model() (fire-and-forget, background thread, used for
    silent session startup) this blocks and reports success/failure, for use
    when a user actively switches models and is waiting to know it worked.
    """
    if not _ollama_available():
        return False
    try:
        r = requests.post(
            f"{_OLLAMA_BASE}/api/generate",
            proxies=ollama_proxies(_OLLAMA_BASE),
            json={
                "model": model,
                "prompt": "hi",
                "stream": True,
                "options": {"num_predict": 2, "num_ctx": num_ctx()},
            },
            stream=True,
            timeout=(10, timeout),
        )
        r.raise_for_status()
        import json as _json

        for line in r.iter_lines():
            if not line:
                continue
            chunk = _json.loads(line)
            text = chunk.get("response", "")
            if on_chunk is not None and text:
                on_chunk(text)
            if chunk.get("done"):
                return True
        return True
    except Exception as exc:
        logger.warning("Model warmup failed for %s: %s", model, exc)
        return False


def _ollama_available() -> bool:
    """Return True if the Ollama server answers a lightweight health check."""
    try:
        r = requests.get(
            f"{_OLLAMA_BASE}/api/tags", timeout=2, proxies=ollama_proxies(_OLLAMA_BASE)
        )
        return r.status_code == 200
    except Exception:
        return False


def list_models() -> list[str]:
    """Return the names of models currently downloaded in Ollama, or [] on failure."""
    try:
        r = requests.get(
            f"{_OLLAMA_BASE}/api/tags", timeout=5, proxies=ollama_proxies(_OLLAMA_BASE)
        )
        r.raise_for_status()
        return [m.get("name", "") for m in r.json().get("models", [])]
    except Exception:
        return []


def _call_ollama(
    model: str,
    prompt: str,
    timeout: float,
    on_chunk: Callable[[str], None] | None = None,
) -> str:
    """Stream from the Ollama generate endpoint and return the full text.

    Using stream=True means the HTTP connection receives a token at a time,
    so the read timeout never fires during the long prefill phase on CPU-only
    hardware.  We use a generous connect timeout (10 s) and set the read
    timeout to the caller-supplied total ``timeout``.

    ``on_chunk`` is called with each response fragment so callers can drive
    a live progress indicator without blocking.
    """
    import json as _json
    import time as _time

    deadline = _time.monotonic() + timeout
    chunks = []
    try:
        r = requests.post(
            f"{_OLLAMA_BASE}/api/generate",
            proxies=ollama_proxies(_OLLAMA_BASE),
            json={
                "model": model,
                "prompt": prompt,
                "stream": True,
                "options": {"temperature": 0, "num_ctx": num_ctx()},
            },
            stream=True,
            timeout=(10, timeout),
        )
        r.raise_for_status()
        for line in r.iter_lines():
            if _time.monotonic() > deadline:
                logger.warning("Ollama call exceeded total timeout of %ss", timeout)
                break
            if not line:
                continue
            chunk = _json.loads(line)
            text = chunk.get("response", "")
            chunks.append(text)
            if on_chunk is not None and text:
                on_chunk(text)
            if chunk.get("done"):
                break
        return "".join(chunks)
    except Exception as exc:
        logger.warning("Ollama call failed: %s", exc)
        return ""
