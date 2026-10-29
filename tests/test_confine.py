"""
tests/test_confine.py

Where the file tools may go (security review C1, 2026-10-29). They run
inside codebud with the user's rights, so: writes only inside the project,
never into .git/ or .codebud/; reads may leave the project but never reach
the user's secret files or codebud's own; symlinks are resolved first. The
safety policy refuses such steps before they run, and the tools check
again. The plan display shows the real target, not just the model's
description.
"""

import os

import pytest

from codebud import confine
from codebud.safety import SafetyEngine
from codebud.sandbox import Sandbox
from codebud.tools.tool_registry import ToolRegistry


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    (root / ".git").mkdir(parents=True)
    (root / "src").mkdir()
    (root / "src" / "a.py").write_text("x = 1\n")
    (tmp_path / "outside").mkdir()
    (tmp_path / "outside" / "target.txt").write_text("ORIGINAL\n")
    monkeypatch.chdir(root)
    monkeypatch.setattr(confine, "project_root", lambda: root.resolve())
    return root


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    (h / ".ssh").mkdir(parents=True)
    (h / ".ssh" / "id_ed25519").write_text("PRIVATE")
    (h / ".netrc").write_text("machine x password y")
    monkeypatch.setenv("HOME", str(h))
    return h


def _run(tool, args):
    return ToolRegistry(Sandbox()).get(tool).run(args)


def _refusal(tool, args):
    return SafetyEngine().refusal_reason({"id": "1", "tool": tool, "args": args})


# --- writes -----------------------------------------------------------------


def test_writing_inside_the_project(project):
    assert _refusal("file_write", {"path": "src/b.py", "content": "y"}) is None
    result = _run("file_write", {"path": "src/new/b.py", "content": "y = 2\n"})
    assert result["returncode"] == 0
    assert (project / "src" / "new" / "b.py").read_text() == "y = 2\n"


@pytest.mark.parametrize(
    "path",
    ["../outside/escape.txt", "/etc/codebud-escape", "src/../../outside/escape.txt"],
)
def test_writing_outside_the_project_is_refused(project, path):
    assert "outside the project" in _refusal("file_write", {"path": path, "content": "x"})
    result = _run("file_write", {"path": path, "content": "x"})
    assert result["returncode"] == 1 and "outside the project" in result["stderr"]
    assert not (project.parent / "outside" / "escape.txt").exists()


def test_writing_through_a_symlink_that_leaves_the_project(project):
    os.symlink(project.parent / "outside" / "target.txt", project / "link.txt")
    assert "resolves to" in _refusal("file_write", {"path": "link.txt", "content": "x"})
    assert _run("file_write", {"path": "link.txt", "content": "x"})["returncode"] == 1
    assert (project.parent / "outside" / "target.txt").read_text() == "ORIGINAL\n"


def test_writing_into_a_symlinked_directory_that_leaves_the_project(project):
    os.symlink(project.parent / "outside", project / "out")
    assert _run("file_write", {"path": "out/new.txt", "content": "x"})["returncode"] == 1
    assert not (project.parent / "outside" / "new.txt").exists()


@pytest.mark.parametrize(
    "path",
    [".git/hooks/pre-commit", ".git/config", "sub/.git/config", ".codebud/harmless_commands.json"],
)
def test_git_and_codebud_dirs_are_protected(project, path):
    reason = _refusal("file_write", {"path": path, "content": "x"})
    assert reason and "refused" in reason
    assert _run("file_write", {"path": path, "content": "x"})["returncode"] == 1


def test_codebud_own_dirs_are_protected_even_inside_the_project(project, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(project / "cfg"))
    reason = _refusal("file_write", {"path": "cfg/codebud/config.toml", "content": "x"})
    assert reason and "codebud's own files" in reason


def test_patch_target_is_confined(project):
    diff = "--- a/../outside/target.txt\n+++ b/../outside/target.txt\n@@ -1 +1 @@\n-ORIGINAL\n+X\n"
    assert "outside the project" in _refusal("patch", {"patch": diff})
    assert _run("patch", {"patch": diff})["returncode"] == 1
    assert (project.parent / "outside" / "target.txt").read_text() == "ORIGINAL\n"


# --- reads ------------------------------------------------------------------


def test_reading_outside_the_project_is_allowed(project):
    assert _refusal("file_read", {"path": "../outside/target.txt"}) is None
    assert _run("file_read", {"path": "../outside/target.txt"})["stdout"] == "ORIGINAL\n"


@pytest.mark.parametrize("path", ["~/.ssh/id_ed25519", "~/.netrc"])
def test_reading_secret_files_is_refused(project, home, path):
    reason = _refusal("file_read", {"path": path})
    assert reason and "secrets" in reason
    result = _run("file_read", {"path": path})
    assert result["returncode"] == 1 and "PRIVATE" not in result["stdout"]


def test_reading_codebud_keys_is_refused(project, tmp_path, monkeypatch):
    keys = tmp_path / "cfg" / "codebud" / "keys"
    keys.mkdir(parents=True)
    (keys / "identity.age").write_text("SEALED")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    reason = _refusal("file_read", {"path": str(keys / "identity.age")})
    assert reason and "codebud's own files" in reason


def test_reading_a_secret_through_a_symlink_is_refused(project, home):
    os.symlink(home / ".ssh" / "id_ed25519", project / "innocent.txt")
    assert "secrets" in _refusal("file_read", {"path": "innocent.txt"})


# --- what the user sees -----------------------------------------------------


def test_the_display_shows_the_real_target(project, capsys):
    from codebud.cli.display import print_plan, print_step_header

    os.symlink(project.parent / "outside" / "target.txt", project / "link.txt")
    plan = {
        "plan": [
            {
                "id": "1",
                "tool": "file_write",
                "description": "fix typo",
                "args": {"path": "link.txt", "content": "abc"},
            },
            {"id": "2", "tool": "command", "description": "list", "args": {"cmd": "ls -la"}},
            {
                "id": "3",
                "tool": "patch",
                "description": "tidy",
                "args": {"patch": "--- a/src/a.py\n+++ b/src/a.py\n@@ -1 +1 @@\n-x = 1\n+x = 2\n"},
            },
        ]
    }
    print_plan(plan)
    err = capsys.readouterr().err
    assert f"link.txt -> {project.parent.resolve() / 'outside' / 'target.txt'}" in err
    assert "(3 bytes)" in err and "ls -la" in err and str(project.resolve() / "src" / "a.py") in err
    print_step_header(3, plan["plan"][2])
    assert "+x = 2" in capsys.readouterr().err  # the whole diff, before it runs


def test_control_characters_in_descriptions_are_shown_not_obeyed(project, capsys):
    from codebud.cli.display import print_plan

    print_plan(
        {
            "plan": [
                {
                    "id": "1",
                    "tool": "command",
                    "description": "ok\x1b[2K\rsafe",
                    "args": {"cmd": "ls"},
                }
            ]
        }
    )
    err = capsys.readouterr().err
    assert "\x1b[2K" not in err and "\\x1b[2K" in err and "\\r" in err
