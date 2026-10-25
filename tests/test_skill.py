"""
tests/test_skill.py

codebud ships a SKILL.md (package data) that teaches other agents (Claude
Code, OpenClaw, ...) when and how to call the codebud CLI. `codebud skill`
prints it or installs it where an agent looks for skills.
"""

import sys

import pytest

from codebud import skill


def _cli(argv, monkeypatch):
    from codebud.cli import main as run_agent

    monkeypatch.setattr(sys, "argv", ["codebud", *argv])
    try:
        run_agent.main()
    except SystemExit as exc:
        return exc.code
    return 0


# --- content ---


def test_skill_has_frontmatter_name_and_description():
    text = skill.skill_text()
    assert text.startswith("---\n")
    front = text.split("---")[1]
    assert "name: codebud" in front
    assert "description:" in front


@pytest.mark.parametrize(
    "needle",
    [
        "codebud run",
        "codebud plan",
        "codebud chat",
        "codebud doctor",
        "codebud sessions",
        "--resume",
        "--mode",
        "--yes",
        "--no-progress",
        "OLLAMA_MODEL",
        "CODEBUD_NUM_CTX",
    ],
)
def test_skill_documents_current_cli(needle):
    assert needle in skill.skill_text()


def test_skill_has_no_stale_source_paths():
    text = skill.skill_text()
    assert "agent/" not in text
    assert "run_agent" not in text


# --- install ---


def test_target_dirs_for_known_agents(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert skill.target_dir("claude") == tmp_path / ".claude" / "skills" / "codebud"
    assert skill.target_dir("openclaw") == tmp_path / ".openclaw" / "skills" / "codebud"
    with pytest.raises(skill.SkillError, match="claude, openclaw"):
        skill.target_dir("vim")


def test_install_writes_the_skill_and_is_idempotent(tmp_path):
    path = skill.install(tmp_path / "skills" / "codebud")
    assert path == tmp_path / "skills" / "codebud" / "SKILL.md"
    assert path.read_text() == skill.skill_text()
    assert skill.install(tmp_path / "skills" / "codebud") == path  # same content: fine


def test_install_never_overwrites_a_changed_copy_without_force(tmp_path):
    target = tmp_path / "codebud"
    target.mkdir()
    (target / "SKILL.md").write_text("my own edits\n")

    with pytest.raises(skill.SkillError, match="--force"):
        skill.install(target)
    assert (target / "SKILL.md").read_text() == "my own edits\n"

    skill.install(target, force=True)
    assert (target / "SKILL.md").read_text() == skill.skill_text()


# --- CLI ---


def test_cli_skill_show_prints_it(monkeypatch, capsys):
    assert _cli(["skill", "show"], monkeypatch) in (0, None)
    assert capsys.readouterr().out == skill.skill_text()


def test_cli_skill_install_for_agent(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert _cli(["skill", "install", "--for", "claude"], monkeypatch) in (0, None)
    installed = tmp_path / ".claude" / "skills" / "codebud" / "SKILL.md"
    assert installed.read_text() == skill.skill_text()
    assert str(installed) in capsys.readouterr().out


def test_cli_skill_install_to_dir(tmp_path, monkeypatch):
    assert _cli(["skill", "install", "--dir", str(tmp_path / "x")], monkeypatch) in (0, None)
    assert (tmp_path / "x" / "SKILL.md").exists()


def test_cli_skill_install_refuses_changed_copy(tmp_path, monkeypatch, capsys):
    (tmp_path / "x").mkdir()
    (tmp_path / "x" / "SKILL.md").write_text("mine\n")
    assert _cli(["skill", "install", "--dir", str(tmp_path / "x")], monkeypatch) == 1
    assert "--force" in capsys.readouterr().err
    assert (tmp_path / "x" / "SKILL.md").read_text() == "mine\n"
