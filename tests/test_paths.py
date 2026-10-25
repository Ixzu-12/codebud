"""
tests/test_paths.py

Where codebud keeps its files (decision D5b). Nothing may depend on the
directory codebud is started from:

- runtime data (memory, sessions, embeddings) in $XDG_DATA_HOME/codebud;
- policy files looked up in a project's .codebud/ (found by walking up
  from the current directory, like .git), then $XDG_CONFIG_HOME/codebud,
  then the defaults shipped with codebud;
- data left in the old checkout-relative data/ is moved once.
"""

import json
from pathlib import Path

from codebud import paths


def test_data_dir_follows_xdg(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    assert paths.data_dir() == tmp_path / "d" / "codebud"


def test_data_dir_defaults_to_local_share(tmp_path, monkeypatch):
    monkeypatch.delenv("XDG_DATA_HOME")
    monkeypatch.setenv("HOME", str(tmp_path))
    assert paths.data_dir() == tmp_path / ".local" / "share" / "codebud"


def test_config_dir_follows_xdg_and_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    assert paths.config_dir() == tmp_path / "c" / "codebud"
    monkeypatch.delenv("XDG_CONFIG_HOME")
    monkeypatch.setenv("HOME", str(tmp_path))
    assert paths.config_dir() == tmp_path / ".config" / "codebud"


def test_project_dir_is_found_by_walking_up(tmp_path):
    (tmp_path / "proj" / ".codebud").mkdir(parents=True)
    deep = tmp_path / "proj" / "src" / "pkg"
    deep.mkdir(parents=True)
    assert paths.project_config_dir(deep) == tmp_path / "proj" / ".codebud"
    assert paths.project_config_dir(tmp_path) is None


def test_policy_file_precedence(tmp_path, monkeypatch):
    name = "harmless_commands.json"
    assert paths.policy_file(name, start=tmp_path) == paths.PACKAGED_POLICY_DIR / name

    user = paths.config_dir()
    user.mkdir(parents=True)
    (user / name).write_text("[]")
    assert paths.policy_file(name, start=tmp_path) == user / name

    project = tmp_path / "proj" / ".codebud"
    project.mkdir(parents=True)
    (project / name).write_text("[]")
    assert paths.policy_file(name, start=tmp_path / "proj") == project / name


def test_policy_loads_from_any_working_directory(tmp_path, monkeypatch):
    """The bug: run outside the checkout, the policy loaded empty."""
    from codebud.safety import SafetyEngine

    monkeypatch.chdir(tmp_path)
    engine = SafetyEngine()
    assert "ls" in engine.harmless_commands
    assert "pytest" in engine.powerful_commands


def test_project_policy_overrides_defaults(tmp_path, monkeypatch):
    from codebud.safety import SafetyEngine

    (tmp_path / ".codebud").mkdir()
    (tmp_path / ".codebud" / "harmless_commands.json").write_text(json.dumps(["ls", "tree"]))
    monkeypatch.chdir(tmp_path)
    engine = SafetyEngine()
    assert engine.harmless_commands == ["ls", "tree"]
    assert "pytest" in engine.powerful_commands  # other files still from defaults


def test_memory_and_sessions_live_in_the_data_dir(tmp_path, monkeypatch):
    from codebud import memory, sessions

    monkeypatch.chdir(tmp_path)
    memory.MemoryStore().add_snapshot({"tool": "command"}, {"returncode": 0})
    s = sessions.Session.create()
    s.close()

    assert (paths.data_dir() / "memory" / "entries.jsonl").exists()
    assert (paths.data_dir() / "sessions" / f"{s.id}.json").exists()
    assert not (tmp_path / "data").exists()


def test_legacy_data_is_copied_once(tmp_path, monkeypatch):
    legacy = tmp_path / "old-checkout" / "data"
    (legacy / "memory").mkdir(parents=True)
    (legacy / "memory" / "entries.jsonl").write_text('{"x": 1}\n')
    (legacy / "sessions").mkdir()
    (legacy / "sessions" / "20261025-000000-abcd.json").write_text("{}")
    monkeypatch.setattr(paths, "LEGACY_DATA_DIR", legacy)

    copied = paths.migrate_legacy_data()

    new = paths.data_dir()
    assert (new / "memory" / "entries.jsonl").read_text() == '{"x": 1}\n'
    assert (new / "sessions" / "20261025-000000-abcd.json").exists()
    assert len(copied) == 2
    assert paths.migrate_legacy_data() == []  # already copied


def test_legacy_migration_never_overwrites(tmp_path, monkeypatch):
    legacy = tmp_path / "old" / "data"
    (legacy / "memory").mkdir(parents=True)
    (legacy / "memory" / "entries.jsonl").write_text("old\n")
    monkeypatch.setattr(paths, "LEGACY_DATA_DIR", legacy)
    target = paths.data_dir() / "memory"
    target.mkdir(parents=True)
    (target / "entries.jsonl").write_text("new\n")

    assert paths.migrate_legacy_data() == []
    assert (target / "entries.jsonl").read_text() == "new\n"
    assert (legacy / "memory" / "entries.jsonl").read_text() == "old\n"


def test_packaged_policy_dir_has_the_default_files():
    for name in ["harmless_commands.json", "powerful_commands.json", "trusted_sequences.json"]:
        assert (Path(paths.PACKAGED_POLICY_DIR) / name).is_file()


def test_config_subcommand_shows_data_dir_and_policy_source(tmp_path, monkeypatch, capsys):
    import sys

    from codebud.cli import main as run_agent

    (tmp_path / ".codebud").mkdir()
    (tmp_path / ".codebud" / "harmless_commands.json").write_text('["ls"]')
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["codebud", "config"])
    run_agent.main()
    out = capsys.readouterr().out
    assert str(paths.data_dir()) in out
    assert str(tmp_path / ".codebud" / "harmless_commands.json") in out


def test_legacy_migration_never_deletes_the_originals(tmp_path, monkeypatch):
    """User data must survive even if the copy goes somewhere unexpected."""
    legacy = tmp_path / "old" / "data"
    (legacy / "memory").mkdir(parents=True)
    (legacy / "memory" / "entries.jsonl").write_text("precious\n")
    monkeypatch.setattr(paths, "LEGACY_DATA_DIR", legacy)

    paths.migrate_legacy_data()

    assert (legacy / "memory" / "entries.jsonl").read_text() == "precious\n"


def test_child_processes_see_the_isolated_legacy_dir(tmp_path):
    """Regression: a test that ran the real `codebud` in a subprocess
    inherited the temporary XDG dirs but not the in-process override of the
    legacy dir, and its migration carried the checkout's real data/ away."""
    import subprocess
    import sys

    out = subprocess.run(
        [sys.executable, "-c", "from codebud import paths; print(paths.legacy_data_dir())"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert out == str(paths.legacy_data_dir())
    assert out != str(paths._SOURCE_ROOT / "data")


def test_python_dash_m_codebud_runs_the_cli():
    """`python -m codebud` replaces the old `python run_agent.py` entry."""
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-m", "codebud", "--help"], capture_output=True, text=True
    )
    assert result.returncode == 0
    assert "usage: codebud" in result.stdout
