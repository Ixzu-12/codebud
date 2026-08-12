"""
tests/test_cli.py

Tests for the Codebud CLI: subcommand routing, doctor checks, plan display,
and error mapping.
"""

import json
import sys
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _good_plan():
    return {
        "status": "ok",
        "plan": [
            {
                "id": "step_0",
                "description": "list files",
                "tool": "command",
                "args": {"cmd": "ls"},
            }
        ],
    }


def _plan_error(msg="LLM returned empty output"):
    return {"status": "plan_error", "error": msg}


# ---------------------------------------------------------------------------
# Doctor checks (unit)
# ---------------------------------------------------------------------------


class TestDoctorChecks:
    def test_python_version_pass(self):
        from agent.cli.doctor import check_python_version

        passed, label, _ = check_python_version()
        assert passed
        assert "Python version" in label

    def test_agent_importable_pass(self):
        from agent.cli.doctor import check_agent_importable

        passed, label, _ = check_agent_importable()
        assert passed
        assert "importable" in label

    def test_ollama_model_not_set(self, monkeypatch):
        from agent.cli.doctor import check_ollama_model_set

        monkeypatch.delenv("OLLAMA_MODEL", raising=False)
        passed, label, hint = check_ollama_model_set()
        assert not passed
        assert "OLLAMA_MODEL" in label
        assert "export" in hint

    def test_ollama_model_set(self, monkeypatch):
        from agent.cli.doctor import check_ollama_model_set

        monkeypatch.setenv("OLLAMA_MODEL", "qwen2.5-coder:3b")
        passed, label, _ = check_ollama_model_set()
        assert passed
        assert "qwen2.5-coder:3b" in label

    def test_ollama_reachable_pass(self, monkeypatch):
        import requests

        from agent.cli import doctor

        def fake_get(url, timeout):
            r = MagicMock()
            r.status_code = 200
            return r

        monkeypatch.setattr(requests, "get", fake_get)
        passed, label, _ = doctor.check_ollama_reachable()
        assert passed

    def test_ollama_reachable_fail_connection(self, monkeypatch):
        import requests

        from agent.cli import doctor

        def fake_get(url, timeout):
            raise requests.exceptions.ConnectionError("refused")

        monkeypatch.setattr(requests, "get", fake_get)
        passed, label, hint = doctor.check_ollama_reachable()
        assert not passed
        assert "ollama serve" in hint or "systemctl" in hint

    def test_model_pulled_pass(self, monkeypatch):
        import requests

        from agent.cli import doctor

        monkeypatch.setenv("OLLAMA_MODEL", "qwen2.5-coder:3b")

        def fake_get(url, timeout):
            r = MagicMock()
            r.status_code = 200
            r.json.return_value = {"models": [{"name": "qwen2.5-coder:3b"}]}
            return r

        monkeypatch.setattr(requests, "get", fake_get)
        passed, label, _ = doctor.check_model_pulled()
        assert passed

    def test_model_pulled_fail(self, monkeypatch):
        import requests

        from agent.cli import doctor

        monkeypatch.setenv("OLLAMA_MODEL", "missing-model")

        def fake_get(url, timeout):
            r = MagicMock()
            r.status_code = 200
            r.json.return_value = {"models": []}
            return r

        monkeypatch.setattr(requests, "get", fake_get)
        passed, label, hint = doctor.check_model_pulled()
        assert not passed
        assert "ollama pull" in hint

    def test_config_files_pass(self, tmp_path, monkeypatch):
        from agent.cli import doctor

        # Point _REPO_ROOT to a temp dir with valid config files
        config = tmp_path / "config"
        config.mkdir()
        (config / "harmless_commands.json").write_text('["ls", "cat"]')
        (config / "powerful_commands.json").write_text('["rm"]')
        monkeypatch.setattr(doctor, "_REPO_ROOT", tmp_path)

        passed, label, _ = doctor.check_config_files()
        assert passed

    def test_config_files_bad_json(self, tmp_path, monkeypatch):
        from agent.cli import doctor

        config = tmp_path / "config"
        config.mkdir()
        (config / "harmless_commands.json").write_text("not-json{{{")
        (config / "powerful_commands.json").write_text("[]")
        monkeypatch.setattr(doctor, "_REPO_ROOT", tmp_path)

        passed, label, hint = doctor.check_config_files()
        assert not passed
        assert "JSON" in label or "JSON" in hint

    def test_run_doctor_all_pass(self, monkeypatch, capsys):
        import requests

        from agent.cli import doctor

        monkeypatch.setenv("OLLAMA_MODEL", "qwen2.5-coder:3b")

        def fake_get(url, timeout):
            r = MagicMock()
            r.status_code = 200
            r.json.return_value = {"models": [{"name": "qwen2.5-coder:3b"}]}
            return r

        monkeypatch.setattr(requests, "get", fake_get)

        rc = doctor.run_doctor()
        captured = capsys.readouterr()
        assert rc == 0
        assert "All checks passed" in captured.out

    def test_run_doctor_no_color_when_not_tty(self, monkeypatch, capsys):
        """Under pytest capture, stdout isn't a TTY, so no ANSI escapes appear."""
        import requests

        from agent.cli import doctor

        monkeypatch.setenv("OLLAMA_MODEL", "qwen2.5-coder:3b")

        def fake_get(url, timeout):
            r = MagicMock()
            r.status_code = 200
            r.json.return_value = {"models": [{"name": "qwen2.5-coder:3b"}]}
            return r

        monkeypatch.setattr(requests, "get", fake_get)
        doctor.run_doctor()
        captured = capsys.readouterr()
        assert "\033[" not in captured.out

    def test_run_doctor_color_when_tty(self, monkeypatch, capsys):
        """When stdout is a TTY, PASS/FAIL lines carry ANSI color codes."""
        import requests

        from agent.cli import doctor

        monkeypatch.setattr(doctor, "_IS_TTY", True)
        monkeypatch.setattr(doctor, "_BOLD", "\033[1m")
        monkeypatch.setattr(doctor, "_DIM", "\033[2m")
        monkeypatch.setattr(doctor, "_GREEN", "\033[32m")
        monkeypatch.setattr(doctor, "_RED", "\033[31m")
        monkeypatch.setattr(doctor, "_RESET", "\033[0m")
        monkeypatch.setenv("OLLAMA_MODEL", "qwen2.5-coder:3b")

        def fake_get(url, timeout):
            r = MagicMock()
            r.status_code = 200
            r.json.return_value = {"models": [{"name": "qwen2.5-coder:3b"}]}
            return r

        monkeypatch.setattr(requests, "get", fake_get)
        doctor.run_doctor()
        captured = capsys.readouterr()
        assert "\033[32m[PASS]\033[0m" in captured.out


# ---------------------------------------------------------------------------
# Display helpers (unit)
# ---------------------------------------------------------------------------


class TestDisplay:
    def test_print_plan_shows_steps(self, capsys):
        from agent.cli.display import print_plan

        plan = _good_plan()
        print_plan(plan)
        captured = capsys.readouterr()
        assert "step_0" not in captured.err or "list files" in captured.err
        # At minimum the tool name appears
        assert "command" in captured.err

    def test_print_step_result_shows_short_stdout_by_default(self, capsys):
        from agent.cli.display import print_step_result

        result = {"returncode": 0, "stdout": "line1\nline2\nline3", "stderr": ""}
        print_step_result(result)
        captured = capsys.readouterr()
        assert "line1" in captured.err
        assert "line2" in captured.err
        assert "line3" in captured.err
        assert "more line" not in captured.err

    def test_print_step_result_truncates_long_stdout_by_default(self, capsys):
        from agent.cli.display import _PREVIEW_LINES, print_step_result

        stdout = "\n".join(f"line{i}" for i in range(1, _PREVIEW_LINES + 11))
        result = {"returncode": 0, "stdout": stdout, "stderr": ""}
        print_step_result(result)
        captured = capsys.readouterr()
        assert f"line{_PREVIEW_LINES}" in captured.err
        assert f"line{_PREVIEW_LINES + 1}" not in captured.err
        assert "+10 more lines" in captured.err
        assert "/output" in captured.err

    def test_print_step_result_verbose_shows_everything(self, capsys):
        from agent.cli.display import _PREVIEW_LINES, print_step_result

        stdout = "\n".join(f"line{i}" for i in range(1, _PREVIEW_LINES + 11))
        result = {"returncode": 0, "stdout": stdout, "stderr": ""}
        print_step_result(result, verbose=True)
        captured = capsys.readouterr()
        assert f"line{_PREVIEW_LINES + 10}" in captured.err
        assert "more line" not in captured.err



# ---------------------------------------------------------------------------
# Error mapper (unit)
# ---------------------------------------------------------------------------


class TestErrors:
    def test_known_error_connection_refused(self):
        from agent.cli.errors import explain

        result = explain("Connection refused to localhost:11434")
        assert result is not None
        explanation, fix = result
        assert "systemctl" in fix or "ollama serve" in fix

    def test_known_error_model_not_found(self):
        from agent.cli.errors import explain

        result = explain("model not found")
        assert result is not None
        _, fix = result
        assert "ollama pull" in fix

    def test_known_error_invalid_json(self):
        from agent.cli.errors import explain

        result = explain("LLM returned invalid JSON")
        assert result is not None

    def test_unknown_error_returns_none(self):
        from agent.cli.errors import explain

        result = explain("some completely unknown error xyz123")
        assert result is None


# ---------------------------------------------------------------------------
# Subcommand routing (integration-level, mocked AgentCore)
# ---------------------------------------------------------------------------


class TestSubcommandRouting:
    def _run_main(self, argv):
        """Run main() with the given argv list, capturing SystemExit."""
        import run_agent

        with patch.object(sys, "argv", ["codebud"] + argv):
            try:
                run_agent.main()
            except SystemExit as exc:
                return exc.code
        return 0

    def test_doctor_subcommand(self, monkeypatch):
        """doctor subcommand calls run_doctor."""
        from agent.cli import doctor

        called = {}

        def fake_doctor():
            called["ran"] = True
            return 0

        monkeypatch.setattr(doctor, "run_doctor", fake_doctor)
        rc = self._run_main(["doctor"])
        assert called.get("ran")
        assert rc == 0

    def test_config_subcommand(self, monkeypatch, capsys):
        monkeypatch.setenv("OLLAMA_MODEL", "qwen2.5-coder:3b")
        self._run_main(["config"])
        captured = capsys.readouterr()
        assert "qwen2.5-coder:3b" in captured.out

    def test_run_subcommand_plan_error(self, monkeypatch):
        """run subcommand exits 1 on plan_error."""

        class FakeAgent:
            executor = MagicMock()

            def handle_user_message(self, msg, on_chunk=None):
                return _plan_error()

            def close(self):
                pass

        monkeypatch.setattr("run_agent._make_agent", lambda: FakeAgent())
        rc = self._run_main(["run", "do something"])
        assert rc == 1

    def test_run_subcommand_plan_error_prints_once(self, monkeypatch, capsys):
        """The plan_error message must appear exactly once, not duplicated
        by two separate error-printing paths."""

        class FakeAgent:
            executor = MagicMock()

            def handle_user_message(self, msg, on_chunk=None):
                return _plan_error("LLM returned empty output")

            def close(self):
                pass

        monkeypatch.setattr("run_agent._make_agent", lambda: FakeAgent())
        self._run_main(["run", "do something"])
        captured = capsys.readouterr()
        assert captured.err.count("LLM returned empty output") == 1

    def test_backwards_compat_no_subcommand(self, monkeypatch):
        """Bare `codebud "msg"` maps to run."""

        ran = {}

        class FakeAgent:
            executor = MagicMock()
            executor.execute_plan = MagicMock(
                return_value={
                    "status": "ok",
                    "results": {"step_0": {"stdout": "", "returncode": 0}},
                }
            )

            def handle_user_message(self, msg, on_chunk=None):
                ran["msg"] = msg
                return _good_plan()

            def close(self):
                pass

        monkeypatch.setattr("run_agent._make_agent", lambda: FakeAgent())
        # Suppress progress output
        monkeypatch.setattr("run_agent._no_progress", lambda _: True)
        self._run_main(["list files"])
        assert ran.get("msg") == "list files"

    def _two_step_plan(self):
        return {
            "status": "ok",
            "plan": [
                {"id": "step_0", "description": "first", "tool": "command", "args": {"cmd": "ls"}},
                {"id": "step_1", "description": "second", "tool": "command", "args": {"cmd": "pwd"}},
            ],
        }

    def _fake_agent_with_plan(self, plan):
        class FakeAgent:
            executor = MagicMock()
            executor.execute_plan = MagicMock(
                return_value={
                    "status": "ok",
                    "results": {"step_0": {"stdout": "", "returncode": 0}},
                }
            )

            def handle_user_message(self, msg, on_chunk=None):
                return plan

            def close(self):
                pass

        return FakeAgent()

    def test_run_interactive_approves_each_step(self, monkeypatch):
        """run -i prompts per-step; 'y' each time executes both steps."""
        agent = self._fake_agent_with_plan(self._two_step_plan())
        monkeypatch.setattr("run_agent._make_agent", lambda: agent)
        monkeypatch.setattr("run_agent._no_progress", lambda _: True)
        monkeypatch.setattr("builtins.input", lambda _prompt="": "y")

        self._run_main(["run", "-i", "do something"])
        assert agent.executor.execute_plan.call_count == 2

    def test_run_interactive_skip_step(self, monkeypatch, capsys):
        """run -i with 'n' skips that step but continues to the next."""
        agent = self._fake_agent_with_plan(self._two_step_plan())
        monkeypatch.setattr("run_agent._make_agent", lambda: agent)
        monkeypatch.setattr("run_agent._no_progress", lambda _: True)

        answers = iter(["n", "y"])
        monkeypatch.setattr("builtins.input", lambda _prompt="": next(answers))

        self._run_main(["run", "-i", "do something"])
        assert agent.executor.execute_plan.call_count == 1
        captured = capsys.readouterr()
        assert "skipped" in captured.err

    def test_run_interactive_quit_aborts(self, monkeypatch, capsys):
        """run -i with 'q' stops before executing any further steps."""
        agent = self._fake_agent_with_plan(self._two_step_plan())
        monkeypatch.setattr("run_agent._make_agent", lambda: agent)
        monkeypatch.setattr("run_agent._no_progress", lambda _: True)
        monkeypatch.setattr("builtins.input", lambda _prompt="": "q")

        self._run_main(["run", "-i", "do something"])
        assert not agent.executor.execute_plan.called
        captured = capsys.readouterr()
        assert "aborted" in captured.err

    def test_run_interactive_all_approves_remaining_steps(self, monkeypatch):
        """run -i with 'a' approves the current and all remaining steps without further prompts."""
        agent = self._fake_agent_with_plan(self._two_step_plan())
        monkeypatch.setattr("run_agent._make_agent", lambda: agent)
        monkeypatch.setattr("run_agent._no_progress", lambda _: True)
        monkeypatch.setattr("builtins.input", lambda _prompt="": "a")

        self._run_main(["run", "-i", "do something"])
        assert agent.executor.execute_plan.call_count == 2

    def test_run_non_interactive_does_not_prompt(self, monkeypatch):
        """Without -i, run executes all steps without calling input()."""
        agent = self._fake_agent_with_plan(self._two_step_plan())
        monkeypatch.setattr("run_agent._make_agent", lambda: agent)
        monkeypatch.setattr("run_agent._no_progress", lambda _: True)

        def fail_input(_prompt=""):
            raise AssertionError("input() should not be called without -i")

        monkeypatch.setattr("builtins.input", fail_input)

        self._run_main(["run", "do something"])
        assert agent.executor.execute_plan.call_count == 2


# ---------------------------------------------------------------------------
# history subcommand
# ---------------------------------------------------------------------------


def _write_entries(path, entries):
    import json as _json

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_json.dumps(entries))


def _entry(desc, tool="command", returncode=0, timestamp="2026-01-01T00:00:00"):
    return {
        "timestamp": timestamp,
        "data": {
            "step": {"id": "step_0", "description": desc, "tool": tool, "args": {}},
            "result": {"stdout": "", "stderr": "", "returncode": returncode},
        },
    }


class TestHistorySubcommand:
    def _run_main(self, argv):
        import run_agent

        with patch.object(sys, "argv", ["codebud"] + argv):
            try:
                run_agent.main()
            except SystemExit as exc:
                return exc.code
        return 0

    def test_no_entries(self, tmp_path, monkeypatch, capsys):
        from agent import memory as memory_mod

        monkeypatch.setattr(memory_mod, "MEMORY_PATH", str(tmp_path / "entries.json"))
        self._run_main(["history"])
        captured = capsys.readouterr()
        assert "No history yet" in captured.out

    def test_lists_entries_newest_relevant_fields(self, tmp_path, monkeypatch, capsys):
        from agent import memory as memory_mod

        path = tmp_path / "entries.json"
        _write_entries(path, [_entry("first"), _entry("second", returncode=1)])
        monkeypatch.setattr(memory_mod, "MEMORY_PATH", str(path))

        self._run_main(["history"])
        captured = capsys.readouterr()
        assert "first" in captured.out
        assert "second" in captured.out
        assert "exit 1" in captured.out
        assert "ok" in captured.out

    def test_limit_option(self, tmp_path, monkeypatch, capsys):
        from agent import memory as memory_mod

        path = tmp_path / "entries.json"
        _write_entries(path, [_entry("one"), _entry("two"), _entry("three")])
        monkeypatch.setattr(memory_mod, "MEMORY_PATH", str(path))

        self._run_main(["history", "-n", "1"])
        captured = capsys.readouterr()
        assert "three" in captured.out
        assert "one" not in captured.out
        assert "two" not in captured.out

    def test_limit_zero_shows_all(self, tmp_path, monkeypatch, capsys):
        from agent import memory as memory_mod

        path = tmp_path / "entries.json"
        _write_entries(path, [_entry("one"), _entry("two")])
        monkeypatch.setattr(memory_mod, "MEMORY_PATH", str(path))

        self._run_main(["history", "-n", "0"])
        captured = capsys.readouterr()
        assert "one" in captured.out
        assert "two" in captured.out

    def test_json_output(self, tmp_path, monkeypatch, capsys):
        from agent import memory as memory_mod

        path = tmp_path / "entries.json"
        _write_entries(path, [_entry("first")])
        monkeypatch.setattr(memory_mod, "MEMORY_PATH", str(path))

        self._run_main(["history", "--json"])
        captured = capsys.readouterr()
        parsed = json.loads(captured.out)
        assert parsed[0]["data"]["step"]["description"] == "first"


# ---------------------------------------------------------------------------
# chat subcommand (interactive REPL)
# ---------------------------------------------------------------------------


class FakeChatAgent:
    """Stand-in for AgentCore driven entirely through its public API."""

    def __init__(self, plan_by_message=None):
        self._history = []
        self.executor = MagicMock()
        self.executor.execute_plan = MagicMock(
            return_value={
                "status": "ok",
                "results": {"step_0": {"stdout": "", "returncode": 0}},
            }
        )
        self._plan_by_message = plan_by_message or {}
        self.closed = False

    def handle_user_message(self, msg, on_chunk=None):
        self._history.append({"role": "user", "content": msg})
        plan = self._plan_by_message.get(msg, _good_plan())
        summary = "planned" if plan.get("status") == "ok" else plan.get("status")
        self._history.append({"role": "assistant", "content": summary})
        return plan

    def clear_history(self):
        self._history.clear()

    def get_history(self):
        return list(self._history)

    def undo_last_turn(self):
        if len(self._history) >= 2:
            self._history.pop()
            self._history.pop()
            return True
        self._history.clear()
        return False

    def close(self):
        self.closed = True


class TestChatSubcommand:
    def _run_chat(self, monkeypatch, inputs, agent=None, extra_argv=None):
        import run_agent

        agent = agent or FakeChatAgent()
        monkeypatch.setattr("run_agent._make_agent", lambda: agent)
        monkeypatch.setattr("run_agent._no_progress", lambda _: True)

        it = iter(inputs)

        def fake_input(_prompt=""):
            try:
                return next(it)
            except StopIteration:
                raise EOFError from None

        monkeypatch.setattr("builtins.input", fake_input)

        with patch.object(sys, "argv", ["codebud", "chat"]):
            run_agent.main()

        return agent

    def test_quit_closes_agent_without_sending_message(self, monkeypatch):
        agent = self._run_chat(monkeypatch, ["quit"])
        assert agent.closed
        assert agent.get_history() == []

    def test_plan_accept_executes_all_steps(self, monkeypatch, capsys):
        agent = self._run_chat(monkeypatch, ["do something", "y", "y"])
        assert agent.executor.execute_plan.called
        captured = capsys.readouterr()
        assert "step" in captured.err.lower() or "step" in captured.out.lower()

    def test_plan_skip_does_not_execute(self, monkeypatch):
        agent = self._run_chat(monkeypatch, ["do something", "n"])
        assert not agent.executor.execute_plan.called

    def test_chat_reply_fallback_printed(self, monkeypatch, capsys):
        agent = FakeChatAgent(plan_by_message={"hello": {"status": "chat_reply", "reply": "hi there"}})
        self._run_chat(monkeypatch, ["hello"], agent=agent)
        captured = capsys.readouterr()
        # Interactive chat output (including the assistant reply) goes to
        # stderr, matching every other prompt/status line in chat mode, so
        # stdout stays clean for piping.
        assert "hi there" in captured.err

    def test_slash_help(self, monkeypatch, capsys):
        self._run_chat(monkeypatch, ["/help"])
        captured = capsys.readouterr()
        assert "/undo" in captured.err

    def test_slash_clear_resets_history_and_turn(self, monkeypatch, capsys):
        agent = self._run_chat(monkeypatch, ["do something", "n", "/clear"])
        captured = capsys.readouterr()
        assert "History cleared." in captured.err
        assert agent.get_history() == []

    def test_slash_history_empty(self, monkeypatch, capsys):
        self._run_chat(monkeypatch, ["/history"])
        captured = capsys.readouterr()
        assert "no history yet" in captured.err

    def test_slash_history_lists_turns(self, monkeypatch, capsys):
        self._run_chat(monkeypatch, ["do something", "n", "/history"])
        captured = capsys.readouterr()
        assert "USER: do something" in captured.err
        assert "ASSISTANT:" in captured.err

    def test_slash_undo_removes_last_turn(self, monkeypatch, capsys):
        agent = self._run_chat(monkeypatch, ["do something", "n", "/undo"])
        captured = capsys.readouterr()
        assert "Last turn removed." in captured.err
        assert agent.get_history() == []

    def test_slash_undo_with_no_history_clears(self, monkeypatch, capsys):
        agent = self._run_chat(monkeypatch, ["/undo"])
        captured = capsys.readouterr()
        assert "nothing left to undo" in captured.err
        assert agent.get_history() == []

    def test_slash_model_prints_active_model(self, monkeypatch, capsys):
        monkeypatch.setenv("OLLAMA_MODEL", "qwen2.5-coder:3b")
        self._run_chat(monkeypatch, ["/model"])
        captured = capsys.readouterr()
        assert "qwen2.5-coder:3b" in captured.err

    def test_slash_plan_before_any_plan(self, monkeypatch, capsys):
        self._run_chat(monkeypatch, ["/plan"])
        captured = capsys.readouterr()
        assert "no plan yet" in captured.err

    def test_unknown_slash_command(self, monkeypatch, capsys):
        self._run_chat(monkeypatch, ["/bogus"])
        captured = capsys.readouterr()
        assert "Unknown command" in captured.err

    def test_revise_then_accept(self, monkeypatch):
        agent = self._run_chat(
            monkeypatch, ["do something", "r: use a different tool", "y", "y"]
        )
        # Two handle_user_message calls: original + revision.
        assert len(agent.get_history()) == 4
        assert "revision note: use a different tool" in agent.get_history()[2]["content"]
        assert agent.executor.execute_plan.called

    def test_plan_error_from_planner_shown_and_loop_continues(self, monkeypatch, capsys):
        agent = FakeChatAgent(plan_by_message={"bad": _plan_error("LLM returned invalid JSON")})
        self._run_chat(monkeypatch, ["bad"], agent=agent)
        captured = capsys.readouterr()
        assert "LLM returned invalid JSON" in captured.err
