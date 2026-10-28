"""
codebud/privacy.py

The privacy dial (phase 8, Part E; design settled 2026-10-28).

Two independent dials decide how codebud behaves: the mode (modes.py) says
how much it asks, the privacy level says what it may reach:

- locked   nothing leaves this machine: the LLM and embeddings must be a
           local Ollama (the openai backend and remote servers are
           refused), web search is off
- private  the default: data leaves only where you chose (backend, remote
           Ollama, web search -- each announced, shown and, for web search,
           confirmed outside full-auto)
- trusted  for trusted, unattended environments: web searches run without
           confirmation (and, with isolation, steps run unisolated)

Trust never lifts safety: the allow-list and the refused arguments apply
at every level. Set with --privacy, `privacy =` in your config.toml (a
project's file cannot set it) or CODEBUD_PRIVACY.
"""

import logging
import os

logger = logging.getLogger(__name__)

LEVELS = ("locked", "private", "trusted")
DEFAULT_LEVEL = "private"

_warned: set[str] = set()


def level() -> str:
    """The current privacy level (unknown values warn and mean "private")."""
    raw = os.environ.get("CODEBUD_PRIVACY", "").strip().lower()
    if not raw:
        return DEFAULT_LEVEL
    if raw not in LEVELS:
        if raw not in _warned:
            _warned.add(raw)
            logger.warning(
                "Unknown privacy level '%s' (choose %s); using %s",
                raw,
                ", ".join(LEVELS),
                DEFAULT_LEVEL,
            )
        return DEFAULT_LEVEL
    return raw


def locked_refusal(what: str) -> str:
    """The message for something ``locked`` does not allow."""
    return (
        f"privacy is locked: {what} would leave this machine. Use a local Ollama, "
        "or choose another privacy level (--privacy private)."
    )


def egress_needs_confirmation() -> bool:
    """Whether steps sending data off the machine (web search) need a "y"."""
    return level() != "trusted"


def web_search_allowed() -> bool:
    return level() != "locked"
