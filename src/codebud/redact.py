"""
codebud/redact.py

Mask secrets before codebud stores anything (phase 8, Part B3; decision
D12d: on by default, CODEBUD_MASK_SECRETS=0 turns it off).

redact(text) replaces, with "[redacted]":

- well-known token formats (OpenAI/Anthropic "sk-...", GitHub "ghp_..." and
  "github_pat_...", AWS "AKIA...", Slack "xox...", Google "AIza...", JWTs);
- PEM private-key blocks, whole;
- the token after "Bearer" and the password in "scheme://user:password@";
- the value of a quoted literal assigned to a secret-looking name
  (api_key = "...", password: '...'), and of UPPER_CASE environment-style
  assignments (DB_PASSWORD=..., export GITHUB_TOKEN=...).

Names stay, so the text still reads. Ordinary code such as
`token = get_token()` is left alone: a secret-looking name alone is not
enough, its value must be a literal. Only what is written to disk is
masked (sessions, step history); prompts sent to the model are not.
"""

import os
import re
from typing import Any

REDACTED = "[redacted]"

_NAME = r"(?:pass(?:word|wd)?|secret|token|api[_-]?key|access[_-]?key|private[_-]?key|credentials?)"

# (pattern, replacement); applied in order, whole tokens first.
_RULES: list[tuple[re.Pattern, str]] = [
    (
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL
        ),
        REDACTED,
    ),
    (re.compile(r"\bsk-(?:[A-Za-z0-9]+-)*[A-Za-z0-9_-]{20,}"), REDACTED),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"), REDACTED),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{22,}\b"), REDACTED),
    (re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), REDACTED),
    (re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b"), REDACTED),
    (re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"), REDACTED),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"), REDACTED),
    (re.compile(r"(?i)(\bbearer\s+)[A-Za-z0-9._~+/=-]{20,}"), rf"\g<1>{REDACTED}"),
    (re.compile(r"(\b[a-z][a-z0-9+.-]*://[^\s:/@]+:)[^\s@/]+(@)"), rf"\g<1>{REDACTED}\g<2>"),
    # name = "literal" / name: 'literal' (code, YAML, JSON); 8+ characters.
    (
        re.compile(rf"""(?i)(\b\w*{_NAME}\w*["']?\s*[:=]\s*)(["'])(?!https?://)[^"'\s]{{8,}}\2"""),
        rf"\g<1>\g<2>{REDACTED}\g<2>",
    ),
    # NAME=value, environment style (UPPER_CASE names only).
    (
        re.compile(
            r"(\b[A-Z0-9_]*(?:PASSWORD|PASSWD|SECRET|TOKEN|API_KEY|ACCESS_KEY|PRIVATE_KEY)"
            r"[A-Z0-9_]*=)(?![\"'$])[^\s\"']{8,}"
        ),
        rf"\g<1>{REDACTED}",
    ),
]


def enabled() -> bool:
    """Whether masking is on: CODEBUD_MASK_SECRETS, default on."""
    return os.environ.get("CODEBUD_MASK_SECRETS", "1").strip().lower() not in ("0", "false", "no")


def redact(text: str) -> str:
    """``text`` with every secret it contains replaced by [redacted]."""
    for pattern, replacement in _RULES:
        text = pattern.sub(replacement, text)
    return text


def redact_data(data: Any) -> Any:
    """A copy of ``data`` (dicts, lists, strings) with every string redacted."""
    if isinstance(data, str):
        return redact(data)
    if isinstance(data, dict):
        return {key: redact_data(value) for key, value in data.items()}
    if isinstance(data, list):
        return [redact_data(item) for item in data]
    if isinstance(data, tuple):
        return tuple(redact_data(item) for item in data)
    return data


def for_storage(data: Any) -> Any:
    """``data`` as it should be written to disk: redacted unless masking is off."""
    return redact_data(data) if enabled() else data
