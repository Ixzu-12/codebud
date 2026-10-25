"""
codebud/cli/doctor.py

Environment health checks for `codebud doctor`.

Each check is a function that returns (passed: bool, message: str).
The message is shown to the user in both the pass and fail cases.
Failures also print a one-line "How to fix:" hint.
"""

import json
import os
import shutil
import sys
from collections.abc import Callable
from pathlib import Path

import requests

from codebud.llm_stub import ollama_proxies

_OLLAMA_BASE = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")

# Linux memory statistics; macOS and FreeBSD have none, so the RAM check is
# skipped there.
MEMINFO_PATH = "/proc/meminfo"

# ANSI color helpers — degrade to no-op when stdout isn't a TTY (piped output).
_IS_TTY = sys.stdout.isatty()
_BOLD = "\033[1m" if _IS_TTY else ""
_DIM = "\033[2m" if _IS_TTY else ""
_GREEN = "\033[32m" if _IS_TTY else ""
_RED = "\033[31m" if _IS_TTY else ""
_RESET = "\033[0m" if _IS_TTY else ""

# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------

CheckResult = tuple[bool, str, str]  # (passed, label, hint_on_failure)


def check_python_version() -> CheckResult:
    ok = sys.version_info >= (3, 10)
    ver = ".".join(str(v) for v in sys.version_info[:3])
    hint = "Install Python 3.10 or later (pacman -S python)."
    return ok, f"Python version: {ver}", hint


def check_agent_importable() -> CheckResult:
    try:
        import codebud  # noqa: F401

        return True, "codebud package is importable", ""
    except ImportError:
        hint = "Run: ./bootstrap --dev  (or: pip install -e . --group dev)"
        return False, "codebud package is NOT importable", hint


def check_ollama_model_set() -> CheckResult:
    model = os.environ.get("OLLAMA_MODEL", "")
    if model:
        return True, f"OLLAMA_MODEL={model}", ""
    hint = "Run: export OLLAMA_MODEL=qwen2.5-coder:3b-instruct-q4_K_M"
    return False, "OLLAMA_MODEL is not set", hint


def check_ollama_reachable() -> CheckResult:
    url = f"{_OLLAMA_BASE}/api/tags"
    try:
        r = requests.get(url, timeout=3, proxies=ollama_proxies(_OLLAMA_BASE))
        if r.status_code == 200:
            return True, f"Ollama server reachable at {_OLLAMA_BASE}", ""
        hint = "sudo systemctl start ollama  (or: ollama serve)"
        return False, f"Ollama returned HTTP {r.status_code}", hint
    except requests.exceptions.ConnectionError:
        hint = "Start the server: sudo systemctl start ollama  (or: ollama serve)"
        return False, f"Cannot connect to Ollama at {_OLLAMA_BASE}", hint
    except Exception as exc:
        hint = "sudo systemctl start ollama"
        return False, f"Ollama check failed: {exc}", hint


def check_model_pulled() -> CheckResult:
    model = os.environ.get("OLLAMA_MODEL", "")
    if not model:
        return False, "OLLAMA_MODEL not set (skipping model-pull check)", "Set OLLAMA_MODEL first."
    try:
        r = requests.get(
            f"{_OLLAMA_BASE}/api/tags", timeout=3, proxies=ollama_proxies(_OLLAMA_BASE)
        )
        if r.status_code != 200:
            return False, "Cannot reach Ollama to check model list", "sudo systemctl start ollama"
        names = [m.get("name", "") for m in r.json().get("models", [])]
        if model in names:
            return True, f"Model '{model}' is available", ""
        hint = f"ollama pull {model}"
        return False, f"Model '{model}' is NOT pulled", hint
    except Exception:
        return False, "Could not read model list from Ollama", "sudo systemctl start ollama"


def check_config_files() -> CheckResult:
    """Check that the policy files in effect exist and parse."""
    from codebud.paths import policy_file

    names = ["harmless_commands.json", "powerful_commands.json", "trusted_sequences.json"]
    for name in names:
        path = policy_file(name)
        if not path.exists():
            return (
                False,
                f"Missing policy file: {path}",
                "Reinstall codebud, or copy the defaults into ~/.config/codebud/.",
            )
        try:
            json.loads(path.read_text())
        except json.JSONDecodeError as exc:
            return False, f"Invalid JSON in {path}: {exc}", f"Fix the JSON in {path}."
    return True, f"Policy files OK ({policy_file(names[0]).parent})", ""


def check_disk_space() -> CheckResult:
    models_dir = Path.home() / ".ollama" / "models"
    if not models_dir.exists():
        return True, "~/.ollama/models/ not found (no models downloaded yet)", ""
    usage = shutil.disk_usage(models_dir)
    free_gb = usage.free / 1024**3
    if free_gb >= 2.0:
        return True, f"Free disk space: {free_gb:.1f} GB", ""
    hint = "Free up disk space; at least 2 GB is needed for the smallest model."
    return False, f"Low disk space: {free_gb:.1f} GB free near ~/.ollama/models/", hint


def _read_meminfo() -> dict[str, int]:
    """Parse MEMINFO_PATH ("Name:   123 kB" lines) into {name: kilobytes}."""
    values = {}
    with open(MEMINFO_PATH) as f:
        for line in f:
            name, _, rest = line.partition(":")
            fields = rest.split()
            if fields and fields[0].isdigit():
                values[name.strip()] = int(fields[0])
    return values


def check_ram() -> CheckResult:
    try:
        available_mb = _read_meminfo().get("MemAvailable", 0) // 1024
        model = os.environ.get("OLLAMA_MODEL", "")
        # Rough RAM requirements per model family
        thresholds = {
            "1.5b": 2048,
            "3b": 4096,
            "7b": 8192,
            "14b": 16384,
        }
        needed_mb = 4096  # default guess
        for key, mb in thresholds.items():
            if key in model.lower():
                needed_mb = mb
                break
        if available_mb >= needed_mb:
            return True, f"Available RAM: {available_mb} MB (need ~{needed_mb} MB)", ""
        hint = (
            f"Close other applications. The selected model needs ~{needed_mb} MB "
            f"of free RAM; only {available_mb} MB is available."
        )
        return False, f"Low RAM: {available_mb} MB available, ~{needed_mb} MB needed", hint
    except Exception:
        return True, "RAM check skipped (could not read /proc/meminfo)", ""


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

_CHECKS: list[Callable[[], CheckResult]] = [
    check_python_version,
    check_agent_importable,
    check_ollama_model_set,
    check_ollama_reachable,
    check_model_pulled,
    check_config_files,
    check_disk_space,
    check_ram,
]


def run_doctor() -> int:
    """Run all checks and print a report. Returns exit code (0=all pass)."""
    failures = 0
    print(f"{_BOLD}Codebud environment check{_RESET}\n")
    for check_fn in _CHECKS:
        passed, label, hint = check_fn()
        if passed:
            print(f"  {_GREEN}[PASS]{_RESET} {label}")
        else:
            failures += 1
            print(f"  {_RED}[FAIL]{_RESET} {label}")
            if hint:
                print(f"         {_DIM}-> {hint}{_RESET}")
    print()
    if failures == 0:
        print(f"{_GREEN}All checks passed. Codebud is ready.{_RESET}")
    else:
        print(
            f"{_RED}{failures} check(s) failed.{_RESET} Fix the issues above and re-run: codebud doctor"
        )
    return 0 if failures == 0 else 1
