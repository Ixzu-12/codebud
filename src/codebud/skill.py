"""
codebud/skill.py

codebud's skill: a SKILL.md (shipped as package data) that teaches other
agents when and how to call the codebud CLI. The format, Markdown with a
name/description frontmatter, is read by Claude Code, OpenClaw and others.
"""

from pathlib import Path

SKILL_FILE = Path(__file__).resolve().parent / "data" / "skill" / "SKILL.md"

# Where each supported agent looks for personal skills (relative to $HOME).
AGENT_SKILL_DIRS = {
    "claude": Path(".claude") / "skills" / "codebud",
    "openclaw": Path(".openclaw") / "skills" / "codebud",
}


class SkillError(Exception):
    """The skill could not be installed."""


def skill_text() -> str:
    """Return the contents of codebud's SKILL.md."""
    return SKILL_FILE.read_text()


def target_dir(agent: str) -> Path:
    """Return the skill directory for ``agent`` in the user's home."""
    if agent not in AGENT_SKILL_DIRS:
        raise SkillError(f"Unknown agent '{agent}'; choose one of: {', '.join(AGENT_SKILL_DIRS)}.")
    return Path.home() / AGENT_SKILL_DIRS[agent]


def install(directory: Path | str, force: bool = False) -> Path:
    """Write SKILL.md into ``directory`` and return its path.

    An existing identical copy is left as is. A copy that differs (perhaps
    edited by the user) is only replaced with ``force``.
    """
    path = Path(directory) / "SKILL.md"
    text = skill_text()
    if path.exists() and path.read_text() != text and not force:
        raise SkillError(
            f"{path} exists and differs from codebud's skill; use --force to replace it."
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path
