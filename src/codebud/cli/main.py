"""
codebud/cli/main.py

Codebud CLI entry point.

Subcommands
-----------
  run "<msg>"     Plan + execute a request (default when no subcommand given)
                  --interactive/-i prompts for approval before each step
                  --yes/-y runs powerful commands without asking
                  --model/-m <name> overrides OLLAMA_MODEL, validated + warmed first
  plan "<msg>"    Plan only — print the plan without executing
                  --execute/-x also runs the steps and prints full output
                  (cannot combine with --json)
                  --model/-m <name> overrides OLLAMA_MODEL, validated + warmed first
  chat            Interactive REPL: multiple requests in one session
                  --model/-m <name> overrides OLLAMA_MODEL at startup;
                  /model <name> switches mid-session, /models lists available
                  --mode <ask|auto|full-auto|plan|edit> sets the agent mode
                  --resume [SESSION] continues a saved session
  doctor          Check the environment (Ollama, model, policy files, RAM, disk)
  history         Show past step executions (-n/--limit, --json)
  sessions        List saved chat sessions (-n/--limit, --json)
  index [PATH]    Build or refresh the project index for search
                  --status, --rebuild, --drop [--yes]
  skill           Show or install codebud's skill for other agents
  models          List downloaded Ollama models
  config          Show resolved configuration (env vars + defaults)
  version         Print version information

Backwards compatibility: `codebud "some request"` (no subcommand) works
exactly as before — it maps to `run`.
"""

import argparse
import json
import os
import sys

from codebud.modes import MODES

_KNOWN_SUBCOMMANDS = {
    "run",
    "plan",
    "chat",
    "doctor",
    "history",
    "sessions",
    "index",
    "skill",
    "models",
    "config",
    "version",
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_agent():
    from codebud.core import AgentCore

    return AgentCore()


def _no_progress(args) -> bool:
    return getattr(args, "no_progress", False) or not sys.stderr.isatty()


def _apply_model_override(args) -> bool:
    """Handle --model/-m: validate it's downloaded, warm it with live progress,
    then set OLLAMA_MODEL so agent construction picks it up.

    Returns False (after printing an error) if the override was requested but
    invalid, so the caller can abort before spending time on planning.
    """
    requested = getattr(args, "model", None)
    if not requested:
        return True

    from codebud.cli.display import ProgressIndicator
    from codebud.llm_stub import list_models, warm_model_sync

    available = list_models()
    if not available:
        print("Cannot reach Ollama to verify --model.", file=sys.stderr)
        return False
    if requested not in available:
        print(
            f"Model '{requested}' is not downloaded. Run: ollama pull {requested}", file=sys.stderr
        )
        return False

    prog = ProgressIndicator(no_progress=_no_progress(args))
    prog.start()
    try:
        ok = warm_model_sync(requested, on_chunk=prog.on_chunk)
    finally:
        prog.stop()
    if not ok:
        print(f"Failed to warm up '{requested}'.", file=sys.stderr)
        return False

    os.environ["OLLAMA_MODEL"] = requested
    return True


# ---------------------------------------------------------------------------
# Subcommand: run
# ---------------------------------------------------------------------------


def cmd_run(args):
    from codebud.cli.display import (
        ProgressIndicator,
        print_plan,
        print_status,
        print_step_header,
        print_step_result,
        print_warning,
    )
    from codebud.cli.errors import print_error

    if not _apply_model_override(args):
        sys.exit(1)

    agent = _make_agent()
    try:
        prog = ProgressIndicator(no_progress=_no_progress(args))
        prog.start()
        try:
            plan = agent.handle_user_message(args.message, on_chunk=prog.on_chunk)
        finally:
            prog.stop()

        if plan.get("status") == "chat_reply":
            print(plan.get("reply", ""), flush=True)
            return

        if plan.get("status") != "ok":
            err = plan.get("error", "unknown error")
            print_error(err, label="Plan error")
            sys.exit(1)

        print_plan(plan, verbose=args.verbose)

        steps = plan.get("plan", [])
        approve_all = not args.interactive
        # Without -i, steps run unprompted, except powerful ones (code
        # runners, file-changing commands), which need a "y" unless --yes.
        confirm_powerful = not args.interactive and not args.yes
        unconfirmed = 0
        for i, step in enumerate(steps, 1):
            print_step_header(i, step)

            if confirm_powerful and agent.is_powerful(step):
                print_warning("Powerful command (runs code or changes files).")
                try:
                    answer = input("  Run it? [y/N] ").strip().lower()
                except (EOFError, KeyboardInterrupt):
                    print(file=sys.stderr)
                    unconfirmed += 1
                    print_status("-- not run: no one to confirm")
                    continue
                if not answer.startswith("y"):
                    print_status("-- skipped")
                    continue

            if not approve_all:
                try:
                    approval = input(f"  Execute step {i}? [y/N/a(all)/q(quit)] ").strip().lower()
                except (EOFError, KeyboardInterrupt):
                    approval = "q"

                if approval == "q":
                    print_status("-- aborted")
                    break
                if approval == "a":
                    approve_all = True
                elif not approval.startswith("y"):
                    print_status("-- skipped")
                    continue

            result = agent.executor.execute_plan({"status": "ok", "plan": [step]})
            if result.get("status") == "ok":
                out = result["results"].get(step["id"], {})
                print_step_result(out, verbose=args.verbose, full=True)
            else:
                err = str(result)
                print_error(err, label="Execution error")
                sys.exit(1)

        if unconfirmed:
            print_warning(
                f"{unconfirmed} powerful step(s) were not run because no one could confirm "
                "them. Re-run with --yes to allow powerful commands unattended."
            )
            sys.exit(1)
    finally:
        agent.close()


# ---------------------------------------------------------------------------
# Subcommand: plan (plan only, no execution)
# ---------------------------------------------------------------------------


def cmd_plan(args):
    from codebud.cli.display import (
        ProgressIndicator,
        print_plan,
        print_step_header,
        print_step_result,
    )
    from codebud.cli.errors import print_error

    if args.execute and args.json:
        print("--execute and --json cannot be combined.", file=sys.stderr)
        sys.exit(2)

    if not _apply_model_override(args):
        sys.exit(1)

    agent = _make_agent()
    try:
        prog = ProgressIndicator(no_progress=_no_progress(args))
        prog.start()
        try:
            plan = agent.handle_user_message(args.message, on_chunk=prog.on_chunk)
        finally:
            prog.stop()

        if plan.get("status") == "chat_reply":
            print(plan.get("reply", ""), flush=True)
            return

        if plan.get("status") != "ok":
            err = plan.get("error", "unknown error")
            print_error(err, label="Plan error")
            sys.exit(1)

        if args.json:
            print(json.dumps(plan, indent=2))
            return

        print_plan(plan, verbose=args.verbose)

        if not args.execute:
            return

        for i, step in enumerate(plan.get("plan", []), 1):
            print_step_header(i, step)
            result = agent.executor.execute_plan({"status": "ok", "plan": [step]})
            if result.get("status") == "ok":
                out = result["results"].get(step["id"], {})
                print_step_result(out, verbose=args.verbose, full=True)
            else:
                print_error(str(result), label="Execution error")
                sys.exit(1)
    finally:
        agent.close()


# ---------------------------------------------------------------------------
# Subcommand: chat (interactive REPL)
# ---------------------------------------------------------------------------


# chat warns once the next planner prompt reaches this share of num_ctx.
_CONTEXT_WARN_RATIO = 0.70


def _open_session_to_resume(args):
    """Open the session named by --resume, restoring its model if possible.

    Exits with status 1 if there is no such session or it is open in
    another chat. An explicit --model wins over the session's saved model.
    """
    from codebud import sessions
    from codebud.cli.display import print_warning

    ref = args.resume or sessions.most_recent_available()
    if ref is None:
        print("No saved session to resume. Start one with: codebud chat", file=sys.stderr)
        sys.exit(1)
    try:
        session = sessions.Session.open(ref)
    except sessions.SessionError as exc:
        print(f"Cannot resume: {exc}", file=sys.stderr)
        sys.exit(1)

    saved = session.model
    if saved and not getattr(args, "model", None) and saved != os.environ.get("OLLAMA_MODEL"):
        from codebud.llm_stub import list_models

        if saved in list_models():
            os.environ["OLLAMA_MODEL"] = saved
        else:
            current = os.environ.get("OLLAMA_MODEL", "(not set)")
            print_warning(
                f"The session used '{saved}', which is not downloaded; continuing with {current}."
            )
    return session


class _ChatCheckpoint:
    """Keeps a chat's conversation saved to a session file (codebud/sessions.py).

    The session file is created lazily, the first time there is something
    to save, so a chat that ends without any turns leaves nothing behind.
    """

    def __init__(self, agent, session=None):
        self.agent = agent
        self.session = session
        self._saved = (list(session.history), session.mode) if session else ([], None)

    @property
    def ref(self) -> str:
        """The name to pass to --resume: the session's name, else its ID."""
        return self.session.name or self.session.id

    def save(self, force: bool = False) -> None:
        """Save the history if it changed (or always, with ``force``)."""
        from codebud import sessions

        history = self.agent.get_history()
        mode = self.agent.get_mode()
        if (history, mode) == self._saved and not force:
            return
        if self.session is None:
            if not history and not force:
                return
            self.session = sessions.Session.create()
        self.session.save(history, self.agent.get_model(), mode=mode)
        self._saved = (history, mode)

    def close(self) -> None:
        if self.session is not None:
            self.session.close()


def cmd_chat(args):
    from codebud.cli import chat_input
    from codebud.cli.display import (
        ProgressIndicator,
        print_assistant_reply,
        print_plan,
        print_rule,
        print_status,
        print_step_header,
        print_step_result,
        print_warning,
    )
    from codebud.cli.errors import print_error

    try:
        import readline as _rl  # noqa: F401 — enables arrow-key history and Ctrl+R in input()

        _rl.parse_and_bind("tab: complete")
    except ImportError:
        pass

    if not _apply_model_override(args):
        sys.exit(1)

    from codebud import sessions

    resumed = _open_session_to_resume(args) if args.resume is not None else None

    agent = _make_agent()
    if resumed is not None and resumed.mode:
        agent.set_mode(resumed.mode)
    if args.mode:
        agent.set_mode(args.mode)  # an explicit --mode wins over the session's
    print(
        f"Codebud chat  |  model: {agent.get_model() or '(not set)'}  |  "
        f"mode: {agent.get_mode()}  |  /help for commands\n",
        file=sys.stderr,
    )

    turn = 0
    checked_turn = 0
    last_plan = None
    last_step_output = None
    checkpoint = _ChatCheckpoint(agent, resumed)
    reader = chat_input.ChatInput(agent)
    if resumed is not None:
        agent.restore_history(resumed.history)
        turn = sum(1 for t in resumed.history if t.get("role") == "user")
        checked_turn = turn
        print_status(f"Resumed session {checkpoint.ref} ({len(resumed.history)} entries).")
        for t in resumed.history[-2:]:
            print_status(f"  {t['role'].upper()}: {t['content'][:120]}")
        print(file=sys.stderr)

    def autosave():
        try:
            checkpoint.save()
        except (sessions.SessionError, OSError) as exc:
            print_warning(f"Could not save the session: {exc}")

    try:
        while True:
            autosave()
            # After each completed turn (not after slash commands), warn
            # if the next planner prompt would fill most of the context.
            if turn and turn != checked_turn:
                checked_turn = turn
                used, total = agent.context_usage()
                if used >= _CONTEXT_WARN_RATIO * total:
                    print_warning(
                        f"Context {round(100 * used / total)}% full "
                        f"(~{used:,} of {total:,} tokens). "
                        "Use /compact to summarise older turns."
                    )
            print_rule()
            try:
                message = reader.read(f"[{turn + 1}] You: ").strip()
            except (EOFError, KeyboardInterrupt):
                print(file=sys.stderr)
                break
            if not message or message.lower() in ("quit", "exit", "q"):
                break

            # --- slash command handling ---
            if message.startswith("/"):
                cmd = message.split()[0].lower()
                if cmd == "/help":
                    print(chat_input.help_text(), file=sys.stderr)
                    continue
                if cmd == "/clear":
                    agent.clear_history()
                    last_plan = None
                    turn = 0
                    print_status("History cleared.\n")
                    continue
                if cmd == "/history":
                    history = agent.get_history()
                    if not history:
                        print_status("(no history yet)\n")
                    else:
                        for i, t in enumerate(history, 1):
                            role = t["role"].upper()
                            print(f"  [{i}] {role}: {t['content'][:120]}", file=sys.stderr)
                        print(file=sys.stderr)
                    continue
                if cmd == "/undo":
                    if agent.undo_last_turn():
                        turn = max(0, turn - 1)
                        print_status("Last turn removed.\n")
                    else:
                        turn = 0
                        print_status("History cleared (nothing left to undo).\n")
                    continue
                if cmd == "/mode":
                    parts = message.split(maxsplit=1)
                    if len(parts) == 1:
                        print_status(
                            f"Mode: {agent.get_mode()}  (choose with /mode <name>: "
                            f"{', '.join(MODES)})\n"
                        )
                        continue
                    ok, msg = agent.set_mode(parts[1].strip())
                    print_status(f"{msg}\n")
                    continue
                if cmd == "/save":
                    parts = message.split(maxsplit=1)
                    try:
                        if len(parts) > 1:
                            sessions.validate_name(parts[1].strip())
                        checkpoint.save(force=True)
                        if len(parts) > 1:
                            checkpoint.session.set_name(parts[1].strip())
                    except (sessions.SessionError, OSError) as exc:
                        print_status(f"{exc}\n")
                        continue
                    print_status(
                        f"Session saved: {checkpoint.ref}  "
                        f"(resume with: codebud chat --resume {checkpoint.ref})\n"
                    )
                    continue
                if cmd == "/compact":
                    prog = ProgressIndicator(no_progress=_no_progress(args))
                    prog.start()
                    try:
                        ok, msg = agent.compact_history(on_chunk=prog.on_chunk)
                    finally:
                        prog.stop()
                    print_status(f"{msg}\n")
                    continue
                if cmd == "/doctor":
                    from codebud.cli.doctor import run_doctor

                    run_doctor()
                    continue
                if cmd == "/model":
                    parts = message.split(maxsplit=1)
                    if len(parts) == 1:
                        print_status(f"Active model: {agent.get_model() or '(not set)'}\n")
                        continue
                    requested = parts[1].strip()
                    prog = ProgressIndicator(no_progress=_no_progress(args))
                    prog.start()
                    try:
                        ok, msg = agent.set_model(requested, on_chunk=prog.on_chunk)
                    finally:
                        prog.stop()
                    print_status(f"{msg}\n")
                    continue
                if cmd == "/models":
                    from codebud.llm_stub import list_models

                    names = list_models()
                    if not names:
                        print_status("Cannot reach Ollama to list models.\n")
                        continue
                    from codebud.cli.display import _GREEN, _RESET

                    active = agent.get_model()
                    for name in names:
                        marker = f"  {_GREEN}<-- active{_RESET}" if name == active else ""
                        print(f"  {name}{marker}", file=sys.stderr)
                    print(file=sys.stderr)
                    continue
                if cmd == "/plan":
                    if last_plan:
                        print_plan(last_plan, verbose=args.verbose)
                    else:
                        print_status("(no plan yet)\n")
                    continue
                if cmd == "/output":
                    if last_step_output is not None:
                        stdout = (last_step_output.get("stdout") or "").rstrip()
                        if stdout:
                            print(stdout, file=sys.stderr)
                        else:
                            print_status("(last step produced no stdout)")
                        print(file=sys.stderr)
                    else:
                        print_status("(no step output yet)\n")
                    continue
                print_status(f"Unknown command: {cmd}  (try /help)\n")
                continue

            prog = ProgressIndicator(no_progress=_no_progress(args))
            prog.start()
            try:
                plan = agent.handle_user_message(message, on_chunk=prog.on_chunk)
            finally:
                prog.stop()

            turn += 1

            if plan.get("status") == "chat_reply":
                print_assistant_reply(plan.get("reply", ""))
                continue

            if plan.get("status") != "ok":
                err = plan.get("error", "unknown error")
                print_error(err, label="Plan error")
                continue

            last_plan = plan
            if agent.get_mode() == "plan":
                print_plan(plan, verbose=args.verbose)
                print_status("plan mode: not executed (switch with /mode ask to run plans)\n")
                continue
            # --- plan review loop: accept / revise / skip ---
            while True:
                print_plan(plan, verbose=args.verbose)
                if agent.get_mode() == "full-auto":
                    review = "y"  # unattended: no plan approval
                else:
                    try:
                        review = input("  Run this plan? [y/N/r(evise)[: note]] ").strip()
                    except (EOFError, KeyboardInterrupt):
                        print(file=sys.stderr)
                        review = "n"

                if review.lower() == "n" or review == "":
                    print_status("-- skipped")
                    break

                if review.lower().startswith("r"):
                    feedback = review[1:].lstrip(": ").strip()
                    revision_msg = (
                        message if not feedback else f"{message} (revision note: {feedback})"
                    )
                    print(file=sys.stderr)
                    prog = ProgressIndicator(no_progress=_no_progress(args))
                    prog.start()
                    try:
                        plan = agent.handle_user_message(revision_msg, on_chunk=prog.on_chunk)
                    finally:
                        prog.stop()
                    if plan.get("status") == "chat_reply":
                        print_assistant_reply(plan.get("reply", ""))
                        break
                    if plan.get("status") != "ok":
                        err = plan.get("error", "unknown error")
                        print_error(err, label="Plan error")
                        break
                    continue  # show revised plan for another review

                # y / yes / anything else → proceed to execution
                steps = plan.get("plan", [])
                for i, step in enumerate(steps, 1):
                    if agent.get_mode() == "full-auto":
                        print_step_header(i, step)
                        step_approval = "y"  # unattended: no step prompts
                    elif agent.get_mode() == "auto":
                        # Plan approved once; only powerful steps (code
                        # runners, file-changing commands) ask again.
                        print_step_header(i, step)
                        if agent.is_powerful(step):
                            print_warning("Powerful command (runs code or changes files).")
                            try:
                                step_approval = input("  Run it? [y/N] ").strip().lower()
                            except (EOFError, KeyboardInterrupt):
                                step_approval = "n"
                        else:
                            step_approval = "y"
                    else:
                        print_step_header(i, step)
                        try:
                            step_approval = (
                                input(f"  Execute step {i}? [y/N/a(all)/q(quit)] ").strip().lower()
                            )
                        except (EOFError, KeyboardInterrupt):
                            step_approval = "q"

                    if step_approval == "q":
                        break
                    if step_approval == "a":
                        for j, s in enumerate(steps[i - 1 :], i):
                            print_step_header(j, s)
                            result = agent.executor.execute_plan({"status": "ok", "plan": [s]})
                            if result.get("status") == "ok":
                                out = result["results"].get(s["id"], {})
                                print_step_result(out, verbose=args.verbose)
                                last_step_output = out
                            else:
                                print_error(str(result), label="Execution error")
                        break
                    if not step_approval.startswith("y"):
                        print_status("-- skipped")
                        continue

                    result = agent.executor.execute_plan({"status": "ok", "plan": [step]})
                    if result.get("status") == "ok":
                        out = result["results"].get(step["id"], {})
                        print_step_result(out, verbose=args.verbose)
                        last_step_output = out
                    else:
                        print_error(str(result), label="Execution error")
                        break
                break  # done executing — exit review loop

            print(file=sys.stderr)
    finally:
        autosave()
        if checkpoint.session is not None:
            print_status(
                f"Session saved: {checkpoint.ref}  "
                f"(resume with: codebud chat --resume {checkpoint.ref})"
            )
        checkpoint.close()
        agent.close()


# ---------------------------------------------------------------------------
# Subcommand: doctor
# ---------------------------------------------------------------------------


def cmd_doctor(_args):
    from codebud.cli.doctor import run_doctor

    sys.exit(run_doctor())


# ---------------------------------------------------------------------------
# Subcommand: history
# ---------------------------------------------------------------------------


def cmd_sessions(args):
    """List saved chat sessions, most recently updated first."""
    from codebud import sessions
    from codebud.cli.display import _OUT_BOLD, _OUT_DIM, _OUT_GREEN, _OUT_RESET

    found = sessions.list_sessions()
    shown = found[: args.limit] if args.limit else found

    if args.json:
        print(json.dumps(shown, indent=2))
        return
    if not shown:
        print(f"{_OUT_DIM}No saved sessions yet. Start one with: codebud chat{_OUT_RESET}")
        return

    for s in shown:
        label = f"{s['id']}" + (f"  {_OUT_BOLD}{s['name']}{_OUT_RESET}" if s["name"] else "")
        busy = f"  {_OUT_GREEN}(open){_OUT_RESET}" if s["in_use"] else ""
        print(f"{label}{busy}")
        print(
            f"  {_OUT_DIM}updated {s['updated'][:19]}  ·  {s['turns']} entries  ·  "
            f"{s['model'] or '?'}{_OUT_RESET}"
        )
        if s["first_request"]:
            print(f"  {s['first_request'][:100]}")
    print(f"\n{_OUT_DIM}Resume with: codebud chat --resume <id or name>{_OUT_RESET}")


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


def cmd_index(args):
    """Build, refresh, inspect or delete the project index."""
    from codebud.cli.display import _OUT_BOLD, _OUT_DIM, _OUT_RESET
    from codebud.rag import indexer
    from codebud.rag.store import ModelMismatch
    from codebud.tools.embeddings import EmbeddingUnavailable

    root = indexer.project_root(args.path)

    if args.status:
        status = indexer.index_status(root)
        if status is None:
            print(f"No index for {root}. Create one with: codebud index")
            return
        print(f"{_OUT_BOLD}Index of {status['root']}{_OUT_RESET}")
        print(f"  {_OUT_DIM}model         {_OUT_RESET} {status['model']} ({status['dim']}-dim)")
        print(
            f"  {_OUT_DIM}contents      {_OUT_RESET} "
            f"{_plural(status['files'], 'file')}, {_plural(status['chunks'], 'chunk')}"
        )
        keyword = "yes" if status["keyword_search"] else "no (SQLite lacks FTS5)"
        print(f"  {_OUT_DIM}keyword search{_OUT_RESET} {keyword}")
        print(f"  {_OUT_DIM}updated       {_OUT_RESET} {status['updated']}")
        print(f"  {_OUT_DIM}file          {_OUT_RESET} {status['path']}")
        changes = (status["changed"], status["new"], status["deleted"])
        if any(changes):
            print(
                f"  {_OUT_DIM}since then    {_OUT_RESET} "
                "{} changed, {} new, {} deleted -- refresh with: codebud index".format(*changes)
            )
        else:
            print(f"  {_OUT_DIM}since then    {_OUT_RESET} no changes")
        return

    if args.drop:
        directory = indexer.index_dir(root)
        if not directory.is_dir():
            print(f"No index for {root}.")
            return
        if not args.yes:
            if not sys.stdin.isatty():
                print(
                    f"codebud: not deleting {directory} without confirmation; re-run with --yes.",
                    file=sys.stderr,
                )
                sys.exit(1)
            answer = input(f"Delete the index in {directory}? [y/N] ").strip().lower()
            if answer not in ("y", "yes"):
                print("Kept.")
                sys.exit(1)
        for path in indexer.drop_index(root):
            print(f"removed {path}")
        return

    show = not _no_progress(args)

    def progress(number: int, total: int, path: str) -> None:
        if show:
            sys.stderr.write(f"\r\033[K  [{number}/{total}] {path}"[:200])
            sys.stderr.flush()

    try:
        report = indexer.index_project(root, rebuild=args.rebuild, progress=progress)
    except (EmbeddingUnavailable, ModelMismatch) as exc:
        if show:
            sys.stderr.write("\r\033[K")
        print(f"codebud: {exc}", file=sys.stderr)
        sys.exit(1)
    if show:
        sys.stderr.write("\r\033[K")

    if report.indexed or report.removed:
        print(
            f"Indexed {_plural(report.indexed, 'file')} "
            f"({_plural(report.chunks, 'chunk')}) in {report.seconds:.1f} s; "
            f"{report.unchanged} unchanged, {report.removed} removed."
        )
    else:
        print(f"Index is up to date ({_plural(report.unchanged, 'file')}).")
    if report.skipped:
        skipped = ", ".join(f"{n} {reason}" for reason, n in sorted(report.skipped.items()))
        print(f"{_OUT_DIM}Skipped: {skipped}{_OUT_RESET}")


def cmd_skill(args):
    """Print codebud's SKILL.md, or install it where an agent finds skills."""
    from codebud import skill

    if args.skill_action == "show":
        try:
            kind = skill.kind_for_show(args.agent)
        except skill.SkillError as exc:
            print(f"codebud: {exc}", file=sys.stderr)
            sys.exit(1)
        sys.stdout.write(skill.skill_text(kind))
        return
    try:
        directory = args.dir or skill.target_dir(args.agent)
        path = skill.install(directory, force=args.force)
    except skill.SkillError as exc:
        print(f"codebud: {exc}", file=sys.stderr)
        sys.exit(1)
    print(f"Installed codebud's skill: {path}")


def cmd_history(args):
    """Show past step executions recorded by MemoryStore (in the data directory).

    This is persisted, cross-session execution history (MemoryStore), distinct
    from the in-session conversation history shown by /history in `chat`.
    """
    from codebud.cli.display import _OUT_CYAN, _OUT_DIM, _OUT_GREEN, _OUT_RED, _OUT_RESET
    from codebud.memory import MemoryStore

    store = MemoryStore()
    entries = store.history[-args.limit :] if args.limit else store.history

    if args.json:
        print(json.dumps(entries, indent=2))
        return

    if not entries:
        print(f'{_OUT_DIM}No history yet. Run `codebud run "..."` to record some.{_OUT_RESET}')
        return

    for i, entry in enumerate(entries, 1):
        ts = entry.get("timestamp", "?")
        data = entry.get("data", {})
        step = data.get("step", {})
        result = data.get("result", {})
        tool = step.get("tool", "?")
        desc = step.get("description", "")
        rc = result.get("returncode")
        status_text = "ok" if rc == 0 else f"exit {rc}" if rc is not None else "?"
        status_color = _OUT_GREEN if rc == 0 else _OUT_RED if rc is not None else ""
        status = f"{status_color}{status_text:<8}{_OUT_RESET}"
        print(
            f"{_OUT_CYAN}[{i}]{_OUT_RESET} {_OUT_DIM}{ts}{_OUT_RESET}  {tool:<16} {status} {desc}"
        )


# ---------------------------------------------------------------------------
# Subcommand: models
# ---------------------------------------------------------------------------


def cmd_models(_args):
    import requests as _req

    from codebud.cli.display import _OUT_BOLD, _OUT_GREEN, _OUT_RESET
    from codebud.llm_stub import ollama_proxies

    base = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
    selected = os.environ.get("OLLAMA_MODEL", "")
    try:
        r = _req.get(f"{base}/api/tags", timeout=5, proxies=ollama_proxies(base))
        r.raise_for_status()
        models = r.json().get("models", [])
    except Exception as exc:
        print(f"Cannot reach Ollama: {exc}", file=sys.stderr)
        print("Start it with: sudo systemctl start ollama", file=sys.stderr)
        sys.exit(1)

    if not models:
        print("No models downloaded. Pull one with:")
        print("  ollama pull qwen2.5-coder:3b-instruct-q4_K_M")
        return

    print(f"{_OUT_BOLD}{'NAME':<45} {'SIZE':>8}  {'SELECTED'}{_OUT_RESET}")
    for m in models:
        name = m.get("name", "")
        size_bytes = m.get("size", 0)
        size_gb = size_bytes / 1024**3
        marker = f"{_OUT_GREEN}<-- active{_OUT_RESET}" if name == selected else ""
        print(f"  {name:<43} {size_gb:>6.1f}G  {marker}")


# ---------------------------------------------------------------------------
# Subcommand: config
# ---------------------------------------------------------------------------


def cmd_config(_args):
    from codebud.cli.display import _OUT_BOLD, _OUT_DIM, _OUT_RESET
    from codebud.llm_stub import num_ctx
    from codebud.tools.embeddings import embed_model

    base = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
    model = os.environ.get("OLLAMA_MODEL", "(not set)")
    embed = embed_model()
    openai_key = "(set)" if os.environ.get("OPENAI_API_KEY") else "(not set)"
    web_search = os.environ.get("WEB_SEARCH_ENABLED", "0")

    print(f"{_OUT_BOLD}Codebud resolved configuration:{_OUT_RESET}")
    print(f"  {_OUT_DIM}OLLAMA_MODEL      {_OUT_RESET} = {model}")
    print(f"  {_OUT_DIM}OLLAMA_BASE_URL   {_OUT_RESET} = {base}")
    print(f"  {_OUT_DIM}EMBED_MODEL       {_OUT_RESET} = {embed}")
    print(f"  {_OUT_DIM}CODEBUD_NUM_CTX   {_OUT_RESET} = {num_ctx()}")
    print(f"  {_OUT_DIM}OPENAI_API_KEY    {_OUT_RESET} = {openai_key}")
    print(f"  {_OUT_DIM}WEB_SEARCH_ENABLED{_OUT_RESET} = {web_search}")

    from codebud.paths import data_dir, policy_file

    print(f"\n{_OUT_BOLD}Files:{_OUT_RESET}")
    print(f"  {_OUT_DIM}{'data directory':<24}{_OUT_RESET} = {data_dir()}")
    for name in ("harmless_commands.json", "powerful_commands.json", "trusted_sequences.json"):
        print(f"  {_OUT_DIM}{name:<24}{_OUT_RESET} = {policy_file(name)}")


# ---------------------------------------------------------------------------
# Subcommand: version
# ---------------------------------------------------------------------------


def cmd_version(_args):
    import requests as _req

    from codebud.llm_stub import ollama_proxies

    model = os.environ.get("OLLAMA_MODEL", "(not set)")
    base = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")

    # Try to get Ollama version
    try:
        r = _req.get(f"{base}/api/version", timeout=3, proxies=ollama_proxies(base))
        ollama_ver = r.json().get("version", "unknown") if r.ok else "unreachable"
    except Exception:
        ollama_ver = "unreachable"

    from codebud import __version__

    print(f"codebud  {__version__}")
    print(f"ollama   {ollama_ver}")
    print(f"model    {model}")
    print(f"python   {sys.version.split()[0]}")


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="codebud",
        description="Codebud — local-first coding agent using Ollama.",
    )
    parser.add_argument(
        "--no-progress",
        action="store_true",
        help="Suppress the live progress indicator (useful when piping output).",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Show full step output instead of a one-line summary.",
    )

    # --model is defined per-subcommand (not here at the top level) because
    # argparse silently lets a subparser's same-named default clobber a
    # parent-parser value — defining it once, shared via `parents=`, avoids
    # that trap entirely and keeps `--model` consistently *after* the
    # subcommand, same position as --interactive/--json/--execute.
    model_parent = argparse.ArgumentParser(add_help=False)
    model_parent.add_argument(
        "--model",
        "-m",
        default=None,
        help="Override OLLAMA_MODEL for this invocation (must already be downloaded).",
    )

    sub = parser.add_subparsers(dest="subcommand")

    # run
    p_run = sub.add_parser("run", help="Plan and execute a request.", parents=[model_parent])
    p_run.add_argument("message", help="Natural-language request.")
    p_run.add_argument(
        "--interactive",
        "-i",
        action="store_true",
        help="Prompt for approval before executing each step.",
    )
    p_run.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="Run powerful commands (python, pytest, cp, ...) without asking; for scripts.",
    )

    # plan
    p_plan = sub.add_parser(
        "plan", help="Plan a request without executing it.", parents=[model_parent]
    )
    p_plan.add_argument("message", help="Natural-language request.")
    p_plan.add_argument("--json", action="store_true", help="Output raw JSON plan.")
    p_plan.add_argument(
        "--execute",
        "-x",
        action="store_true",
        help="Also execute the plan's steps and print their full output (cannot combine with --json).",
    )

    # chat
    p_chat = sub.add_parser("chat", help="Interactive multi-turn session.", parents=[model_parent])
    p_chat.add_argument(
        "--mode",
        choices=MODES,
        help="Start in this agent mode (default: ask). Switch later with /mode.",
    )
    p_chat.add_argument(
        "--resume",
        nargs="?",
        const="",
        default=None,
        metavar="SESSION",
        help="Resume a saved session by ID or name (default: the most recent one).",
    )

    # doctor
    sub.add_parser("doctor", help="Check the environment.")

    # history
    p_history = sub.add_parser("history", help="Show past step executions.")
    p_history.add_argument(
        "-n",
        "--limit",
        type=int,
        default=20,
        help="Show at most N most-recent entries (0 = all). Default: 20.",
    )
    p_history.add_argument("--json", action="store_true", help="Output raw JSON entries.")

    # sessions
    p_sessions = sub.add_parser("sessions", help="List saved chat sessions.")
    p_sessions.add_argument(
        "-n",
        "--limit",
        type=int,
        default=20,
        help="Show at most N most recent sessions (0 = all). Default: 20.",
    )
    p_sessions.add_argument("--json", action="store_true", help="Output raw JSON.")

    # index
    p_index = sub.add_parser("index", help="Build or refresh the project index for search.")
    p_index.add_argument(
        "path",
        nargs="?",
        default=None,
        help="Project directory (default: the git project containing the current directory).",
    )
    action = p_index.add_mutually_exclusive_group()
    action.add_argument("--status", action="store_true", help="Show the index and what changed.")
    action.add_argument(
        "--rebuild", action="store_true", help="Delete the index and build it from scratch."
    )
    action.add_argument("--drop", action="store_true", help="Delete the index.")
    p_index.add_argument("--yes", "-y", action="store_true", help="Do not ask before --drop.")

    # skill
    p_skill = sub.add_parser("skill", help="Show or install codebud's skill for other agents.")
    skill_sub = p_skill.add_subparsers(dest="skill_action", required=True)
    p_show = skill_sub.add_parser("show", help="Print a SKILL.md variant.")
    p_show.add_argument(
        "--for",
        dest="agent",
        choices=["claude", "openclaw", "agensi"],
        default=None,
        help="Which variant: claude/openclaw (default text) or agensi (marketplace hand-off).",
    )
    p_install = skill_sub.add_parser("install", help="Install the SKILL.md for an agent.")
    where = p_install.add_mutually_exclusive_group(required=True)
    where.add_argument(
        "--for",
        dest="agent",
        choices=["claude", "openclaw"],
        help="Install into that agent's personal skills directory.",
    )
    where.add_argument("--dir", help="Install into this directory instead.")
    p_install.add_argument(
        "--force", action="store_true", help="Replace an existing copy that differs."
    )

    # models
    sub.add_parser("models", help="List downloaded Ollama models.")

    # config
    sub.add_parser("config", help="Show resolved configuration.")

    # version
    sub.add_parser("version", help="Show version information.")

    return parser


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main():
    # Backwards compatibility: if first arg is not a known subcommand and
    # doesn't start with '-', treat the whole invocation as `codebud run`.
    argv = sys.argv[1:]
    if argv and argv[0] not in _KNOWN_SUBCOMMANDS and not argv[0].startswith("-"):
        argv = ["run"] + argv

    parser = _build_parser()
    args = parser.parse_args(argv)

    # One-time copy of data written by older versions to the checkout's data/.
    from codebud.paths import data_dir, migrate_legacy_data

    copied = migrate_legacy_data()
    if copied:
        print(
            f"codebud: copied {len(copied)} data file(s) from the old data/ directory to "
            f"{data_dir()}; the originals were left in place.",
            file=sys.stderr,
        )

    dispatch = {
        "run": cmd_run,
        "plan": cmd_plan,
        "chat": cmd_chat,
        "doctor": cmd_doctor,
        "history": cmd_history,
        "sessions": cmd_sessions,
        "index": cmd_index,
        "skill": cmd_skill,
        "models": cmd_models,
        "config": cmd_config,
        "version": cmd_version,
    }

    handler = dispatch.get(args.subcommand)
    if handler is None:
        parser.print_help()
        sys.exit(0)

    handler(args)


if __name__ == "__main__":
    main()
