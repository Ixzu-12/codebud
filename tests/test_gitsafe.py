"""
tests/test_gitsafe.py

Defensive check for security review finding H2 (2026-10-29): a
repository's own configuration can name helper programs that git runs as
a side effect of read-only commands (core.fsmonitor, clean/smudge
filters, diff textconv). codebud must neutralise these so that neither a
command step nor codebud's own git calls execute them. Each test sets a
hostile config key whose helper would create a marker file, then asserts
the marker is never created.
"""

import os
import subprocess

import pytest

from codebud import gitsafe
from codebud.sandbox import Sandbox

pytestmark = pytest.mark.skipif(
    subprocess.run(["git", "--version"], capture_output=True).returncode != 0,
    reason="git not installed",
)


def _git(repo, *args):
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t",
    }
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, env=env)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A one-commit repository with a marker-dropping helper script."""
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    (root / "a.txt").write_text("one\n")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "init")
    marker = tmp_path / "MARKER"
    helper = root / "helper.sh"
    helper.write_text(f"#!/bin/sh\ntouch {marker}\ncat\n")
    helper.chmod(0o755)
    monkeypatch.chdir(root)
    return root, marker


def _ran(marker):
    return marker.exists()


# --- gitsafe.git_argv overrides the hostile keys ----------------------------


def test_fsmonitor_is_neutralised(repo):
    root, marker = repo
    _git(root, "config", "core.fsmonitor", str(root / "helper.sh"))
    subprocess.run(
        gitsafe.git_argv(["status", "--porcelain"], cwd=root),
        cwd=root,
        capture_output=True,
        env=gitsafe.git_environment(),
    )
    assert not _ran(marker)


def test_textconv_is_neutralised(repo):
    root, marker = repo
    _git(root, "config", "diff.secret.textconv", str(root / "helper.sh"))
    (root / ".gitattributes").write_text("a.txt diff=secret\n")
    (root / "a.txt").write_text("two\n")
    subprocess.run(
        gitsafe.git_argv(["diff"], cwd=root),
        cwd=root,
        capture_output=True,
        env=gitsafe.git_environment(),
    )
    assert not _ran(marker)


def test_clean_filter_is_neutralised(repo):
    root, marker = repo
    _git(root, "config", "filter.secret.clean", str(root / "helper.sh"))
    (root / ".gitattributes").write_text("a.txt filter=secret\n")
    (root / "a.txt").write_text("three\n")
    subprocess.run(
        gitsafe.git_argv(["status"], cwd=root),
        cwd=root,
        capture_output=True,
        env=gitsafe.git_environment(),
    )
    assert not _ran(marker)


def test_repository_overrides_lists_hostile_keys(repo):
    root, _ = repo
    _git(root, "config", "core.fsmonitor", "x")
    _git(root, "config", "filter.secret.clean", "x")
    _git(root, "config", "user.name", "someone")  # harmless, not listed
    keys = {k for k, _ in gitsafe.repository_overrides(root)}
    assert "core.fsmonitor" in keys and "filter.secret.clean" in keys
    assert "user.name" not in keys


def test_content_commands_get_no_ext_diff_no_textconv():
    argv = gitsafe.git_argv(["-C", "/x", "diff", "HEAD"])
    assert "--no-ext-diff" in argv and "--no-textconv" in argv
    # inserted after the subcommand, not before it
    assert argv.index("diff") < argv.index("--no-ext-diff") < argv.index("HEAD")
    assert "--no-textconv" not in gitsafe.git_argv(["status"])


# --- a command step runs through gitsafe too --------------------------------


@pytest.mark.parametrize("privacy", ["private", "trusted"])
def test_a_git_step_does_not_run_fsmonitor(repo, monkeypatch, privacy):
    root, marker = repo
    _git(root, "config", "core.fsmonitor", str(root / "helper.sh"))
    monkeypatch.setenv("CODEBUD_PRIVACY", privacy)
    result = Sandbox().run_command("git status --porcelain")
    if result["returncode"] != 0 and "not isolated" in result["stderr"]:
        pytest.skip(result["stderr"])
    assert not _ran(marker)


def test_codebuds_own_file_selection_does_not_run_fsmonitor(repo):
    root, marker = repo
    _git(root, "config", "core.fsmonitor", str(root / "helper.sh"))
    from codebud.rag.files import select_files

    select_files(root)
    assert not _ran(marker)
