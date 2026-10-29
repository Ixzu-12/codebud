"""
codebud/gitsafe.py

Running git without letting the repository run programs (security review
H2, 2026-10-29).

A repository's own configuration (.git/config, and files it includes)
can name programs git runs as a side effect of read-only commands:
core.fsmonitor (git status, git ls-files), clean/smudge filters (git
diff and git status on working files), diff drivers and textconv (git
diff, log -p, show), gpg.program (git log with log.showSignature),
pagers, editors, SSH commands and credential helpers. A repository that
arrives with its .git/ directory -- an archive, a shared disk -- or a
step that wrote .git/config could use any of them.

git_argv() returns a git command line with all of them neutralised:
fixed overrides for the well-known keys, and an override for every
program-running key the repository's own config sets (its names are
the repository's choice, so they are read first; `git config --list`
runs nothing). diff, log and show also get --no-ext-diff --no-textconv.
git_environment() is the environment codebud's own git calls use: the
command-step allow-list (sandbox.step_environment()), so a GIT_* or
token variable cannot steer them either.
"""

import re
import subprocess
from pathlib import Path

# Always: the keys most read-only commands consult.
FIXED = (
    ("core.fsmonitor", "false"),
    ("core.hooksPath", "/dev/null"),
    ("core.pager", "cat"),
    ("core.editor", "false"),
    ("sequence.editor", "false"),
    ("core.sshCommand", "false"),
    ("core.askPass", ""),
    ("diff.external", ""),
    ("log.showSignature", "false"),
    ("gpg.program", "false"),
    ("credential.helper", ""),
)

# Keys the repository may name itself; each gets a neutral value.
_RUNS_PROGRAMS = re.compile(
    r"^(filter\.[^=]+\.(clean|smudge|process|required)"
    r"|diff\.[^=]+\.(command|textconv)"
    r"|merge\.[^=]+\.driver"
    r"|pager\.[^=]+"
    r"|gpg\.[^=]+\.program"
    r"|credential\.[^=]+\.helper"
    r"|core\.(fsmonitor|hookspath|pager|editor|sshcommand|askpass|gitproxy|alternaterefscommand)"
    r"|uploadpack\.packobjectshook)$",
    re.IGNORECASE,
)

_CONTENT_COMMANDS = {"diff", "log", "show"}


def _neutral(key: str) -> str:
    lowered = key.lower()
    if lowered.endswith((".clean", ".smudge")) or lowered.startswith("pager."):
        return "cat"  # pass content through unchanged
    if lowered.endswith(".required"):
        return "false"
    if lowered == "core.fsmonitor":
        return "false"
    return ""


def repository_overrides(cwd: str | Path | None = None) -> list[tuple[str, str]]:
    """(key, neutral value) for each program-running key the repository sets."""
    try:
        result = subprocess.run(
            ["git", *_fixed_args(), "config", "--list", "--show-scope", "--name-only"],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
            env=git_environment(),
        )
    except OSError:
        return []
    found = []
    for line in result.stdout.splitlines():
        scope, _, key = line.partition("\t")
        if scope in ("local", "worktree") and _RUNS_PROGRAMS.match(key):
            found.append((key, _neutral(key)))
    return list(dict.fromkeys(found))


def _fixed_args() -> list[str]:
    return [arg for key, value in FIXED for arg in ("-c", f"{key}={value}")]


def git_argv(args: list[str], cwd: str | Path | None = None) -> list[str]:
    """``git *args`` with the repository unable to run programs.

    ``args`` is what follows "git" (global options such as -C included);
    ``cwd`` is where it will run, for reading the repository's config.
    """
    overrides = _fixed_args() + [
        arg for key, value in repository_overrides(cwd) for arg in ("-c", f"{key}={value}")
    ]
    words = list(args)
    # Find the subcommand: the first word that is not a global option.
    i = 0
    while i < len(words) and words[i].startswith("-"):
        i += 2 if words[i] in ("-C", "-c", "--git-dir", "--work-tree") else 1
    if i < len(words) and words[i] in _CONTENT_COMMANDS:
        words[i + 1 : i + 1] = ["--no-ext-diff", "--no-textconv"]
    return ["git", *overrides, *words]


def git_environment() -> dict[str, str]:
    from codebud.sandbox import step_environment

    return step_environment()
