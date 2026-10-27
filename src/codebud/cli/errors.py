"""
codebud/cli/errors.py

Maps error strings returned by the planner/executor to beginner-friendly
explanations and actionable fix hints. Mirrors the Troubleshooting chapter
so the CLI surfaces what the docs already document.
"""

import sys

_IS_TTY = sys.stderr.isatty()
_BOLD = "\033[1m" if _IS_TTY else ""
_DIM = "\033[2m" if _IS_TTY else ""
_RED = "\033[31m" if _IS_TTY else ""
_RESET = "\033[0m" if _IS_TTY else ""


_RULES: list[tuple[str, str, str]] = [
    # (substring to match, explanation, fix command)
    (
        "Connection refused",
        "The Ollama server is not running.",
        "sudo systemctl start ollama  (or: ollama serve)",
    ),
    (
        "could not connect",
        "Codebud cannot reach the Ollama server.",
        "sudo systemctl start ollama  (or: ollama serve)",
    ),
    (
        "model not found",
        "The model named in OLLAMA_MODEL is not downloaded.",
        "ollama pull $OLLAMA_MODEL",
    ),
    (
        "LLM returned empty output",
        "The model produced no response. This usually means the server "
        "is not running or OLLAMA_MODEL is not set.",
        "Run `codebud doctor` to diagnose.",
    ),
    (
        "LLM returned invalid JSON",
        "The model's output could not be parsed as JSON. "
        "Try `codebud plan` to inspect the raw output.",
        "Switch to a larger model (e.g. 3b instead of 1.5b) for more reliable JSON.",
    ),
    (
        "LLM returned malformed plan",
        "The model returned JSON but its structure was wrong "
        "(missing required fields or invalid tool name).",
        "Try rephrasing the request, or use `codebud plan` to inspect.",
    ),
    (
        "HTTP 500",
        "Ollama returned an internal server error. "
        "This is often caused by the OS out-of-memory killer terminating the runner.",
        "Run: dmesg | grep -i oom   Then switch to a smaller model.",
    ),
    (
        "out of memory",
        "The model weights did not fit in available RAM.",
        "Close other applications, or switch to a smaller model variant.",
    ),
    (
        "timed out",
        "The request took too long (the limit is 600 s). On CPU-only hardware "
        "the first request of a session takes about 30 s with the 3B model.",
        "Check that Ollama is still running (`codebud doctor`), or try a smaller model.",
    ),
    # Mode before policy: a policy refusal can name the mode as its reason.
    (
        "not allowed in",
        "The active agent mode does not allow this tool.",
        "Switch the mode: /mode ask in chat, or --mode ask.",
    ),
    (
        "safety policy",
        "A plan step uses a command or argument the safety policy does not allow.",
        "If the command is safe, add it to harmless_commands.json (reads only) or "
        "powerful_commands.json (runs code or changes files) in ~/.config/codebud/ or "
        "the project's .codebud/ (`codebud config` shows the files in effect); "
        "or rephrase the request.",
    ),
]


def explain(error: str) -> tuple[str, str] | None:
    """Return (explanation, fix_hint) for a known error, or None if unknown."""
    lower = error.lower()
    for fragment, explanation, fix in _RULES:
        if fragment.lower() in lower:
            return explanation, fix
    return None


def print_error(error: str, *, label: str = "Error") -> None:
    """Print a formatted error with an explanation and fix hint if known."""
    print(f"\n{_RED}{_BOLD}{label}:{_RESET} {error}", file=sys.stderr)
    result = explain(error)
    if result:
        explanation, fix = result
        print(f"  {_DIM}What happened:{_RESET} {explanation}", file=sys.stderr)
        print(f"  {_DIM}How to fix:{_RESET}    {fix}", file=sys.stderr)
    else:
        print(f"  {_DIM}Run `codebud doctor` to check your environment.{_RESET}", file=sys.stderr)
    print(file=sys.stderr)
