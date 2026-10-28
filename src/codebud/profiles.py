"""
codebud/profiles.py

Profiles (phase 10, first part; design settled 2026-10-28). A profile is a
named bundle of the two dials and what goes with them:

- mode      how much codebud asks (modes.py): the mode chat starts in;
            "full-auto" also makes `run` and `plan --execute` unattended
- privacy   what it may reach (privacy.py)
- policy    a directory of policy files (harmless_commands.json, ...)
            that take precedence over the project's, the user's and the
            shipped ones
- prompt    extra instructions for the planner

Built in: "default" (ask, private) and "unattended" (full-auto, trusted).
Your own live in $XDG_CONFIG_HOME/codebud/profiles/<name>.toml; a project
cannot define or choose one, since a profile can lower the privacy level.

Choose with --profile, `/profile` in chat, CODEBUD_PROFILE or `profile =`
in your config.toml. An explicit --privacy, --mode, --yes or
CODEBUD_PRIVACY still wins over the profile; config.toml does not.
Whatever the profile, the allow-list and the refused arguments apply.
"""

import re
import sys
from dataclasses import dataclass
from pathlib import Path

from codebud import paths
from codebud.modes import MODES
from codebud.privacy import LEVELS

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - Python 3.10
    import tomli as tomllib

DEFAULT = "default"
PROFILE_DIR = "profiles"
_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_KEYS = ("description", "mode", "privacy", "policy", "prompt")


class ProfileError(Exception):
    """A profile that does not exist or cannot be used."""


@dataclass(frozen=True)
class Profile:
    name: str
    description: str = ""
    mode: str = "ask"
    privacy: str = "private"
    policy: Path | None = None  # directory of policy files
    prompt: str = ""
    source: str = "built in"  # or the profile file

    def summary(self) -> str:
        return f"{self.mode}, privacy {self.privacy}"


BUILTIN = {
    "default": Profile("default", "Ask before each step; data stays where you chose."),
    "unattended": Profile(
        "unattended",
        "Fully unattended: nothing is asked, steps are not isolated. The allow-list still applies.",
        mode="full-auto",
        privacy="trusted",
    ),
}

_active: Profile = BUILTIN[DEFAULT]


def profile_dir() -> Path:
    return paths.config_dir() / PROFILE_DIR


def _load_file(name: str, path: Path) -> Profile:
    try:
        data = tomllib.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise ProfileError(f"profile {name} ({path}): {exc}") from exc
    problems = [f"unknown key '{k}'" for k in data if k not in _KEYS]
    problems += [
        f"'{k}' must be a string" for k in _KEYS if k in data and not isinstance(data[k], str)
    ]
    if problems:
        raise ProfileError(f"profile {name} ({path}): {'; '.join(problems)}")
    mode = data.get("mode", "ask")
    if mode not in MODES:
        raise ProfileError(f"profile {name}: mode '{mode}' is not one of {', '.join(MODES)}")
    level = data.get("privacy", "private")
    if level not in LEVELS:
        raise ProfileError(f"profile {name}: privacy '{level}' is not one of {', '.join(LEVELS)}")
    policy = None
    if "policy" in data:
        policy = path.parent / Path(data["policy"]).expanduser()
        if not policy.is_dir():
            raise ProfileError(f"profile {name}: policy directory {policy} does not exist")
    return Profile(
        name,
        data.get("description", ""),
        mode,
        level,
        policy,
        data.get("prompt", "").strip(),
        str(path),
    )


def get(name: str) -> Profile:
    """The profile ``name``: the user's file if there is one, else built in."""
    if not _NAME.match(name):
        raise ProfileError(f"'{name}' is not a valid profile name (a-z, 0-9, '-', '_')")
    path = profile_dir() / f"{name}.toml"
    if path.is_file():
        return _load_file(name, path)
    if name in BUILTIN:
        return BUILTIN[name]
    raise ProfileError(f"no profile '{name}' (choose one of: {', '.join(available())})")


def available() -> list[str]:
    names = set(BUILTIN)
    if profile_dir().is_dir():
        names |= {p.stem for p in profile_dir().glob("*.toml") if _NAME.match(p.stem)}
    return sorted(names)


def active() -> Profile:
    return _active


def activate(profile: Profile, source: str, explicit: bool = False) -> None:
    """Make ``profile`` the active one; ``source`` is what chose it.

    Its privacy level replaces one from config.toml or an earlier profile,
    but not CODEBUD_PRIVACY set in the environment -- unless ``explicit``
    (/profile in chat, which, like /privacy, overrides everything). The
    default profile nobody chose changes nothing.
    """
    global _active
    from codebud import settings

    _active = profile
    paths.PROFILE_POLICY_DIR = profile.policy
    if source == "default" and not explicit:
        return
    settings.set_value("CODEBUD_PROFILE", profile.name, source)
    origin = f"profile {profile.name}"
    if explicit:
        settings.set_value("CODEBUD_PRIVACY", profile.privacy, origin)
    else:
        settings.set_default("CODEBUD_PRIVACY", profile.privacy, origin)


def prompt_note() -> str:
    """Text added to the planner prompt for the active profile ('' if none)."""
    if not _active.prompt:
        return ""
    return f"PROFILE {_active.name}:\n{_active.prompt}\n\n"
