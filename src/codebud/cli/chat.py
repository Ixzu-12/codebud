"""
codebud/cli/chat.py

`codebud chat`: the interactive session -- the REPL, slash commands,
plan review and step execution, and saving and resuming sessions.
"""

import argparse
import os
import sys

from codebud.cli import common
from codebud.cli.admin import apply_retention, cmd_index
from codebud.modes import MODES

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
        from codebud import privacy

        self.session.save(history, self.agent.get_model(), mode=mode, privacy=privacy.level())
        self._saved = (history, mode)

    def close(self) -> None:
        if self.session is not None:
            self.session.close()


def _may_change_files(agent, step: dict) -> bool:
    """Whether running ``step`` can have changed files in the project."""
    return step.get("tool") in ("file_write", "patch") or agent.is_powerful(step)


def _chat_sources(agent) -> None:
    """/sources: the chunks the last request was given as project context."""
    from codebud.cli.display import print_status

    hits = agent.last_context
    if not hits:
        print_status("(no project context was used for the last request)\n")
        return
    print_status("Project context of the last request:")
    for hit in hits:
        c = hit.chunk
        symbol = f" ({c.symbol})" if c.symbol else ""
        print_status(f"  {c.path}:{c.start_line}-{c.end_line}{symbol}")
    print(file=sys.stderr)


def _chat_rag(agent, words: list[str]) -> None:
    """/rag [on|off]: show or switch project context for this session."""
    from codebud.cli.display import print_status
    from codebud.rag import indexer

    if words:
        if words[0].lower() not in ("on", "off"):
            print_status("Use /rag on or /rag off (or /rag to show the state).\n")
            return
        agent.set_rag(words[0].lower() == "on")
    print_status(f"Project context: {'on' if agent.get_rag() else 'off'}")
    root = indexer.project_root()
    status = indexer.index_status(root)
    if status is None:
        print_status(f"No index for {root}; build one with /index.\n")
        return
    changes = (status["changed"], status["new"], status["deleted"])
    stale = (
        " -- {} changed, {} new, {} deleted since: /index to refresh".format(*changes)
        if any(changes)
        else ""
    )
    print_status(
        f"Index: {common.plural(status['files'], 'file')}, {common.plural(status['chunks'], 'chunk')}, "
        f"{status['model']}, updated {status['updated']}{stale}\n"
    )


def _chat_index(words: list[str]) -> None:
    """/index [status|rebuild]: run `codebud index` without leaving chat."""
    import contextlib

    from codebud.cli.display import print_status

    action = words[0].lower() if words else ""
    if action not in ("", "status", "rebuild"):
        print_status("Use /index, /index status or /index rebuild.\n")
        return
    index_args = argparse.Namespace(
        path=None,
        status=action == "status",
        rebuild=action == "rebuild",
        drop=False,
        yes=False,
        no_progress=False,
    )
    # Chat writes everything to stderr, so stdout stays clean for piping.
    with contextlib.redirect_stdout(sys.stderr):
        try:
            cmd_index(index_args)
        except SystemExit:
            pass  # cmd_index already printed the error; the chat goes on
    print(file=sys.stderr)


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

    if not common.apply_model_override(args):
        sys.exit(1)

    from codebud import sessions

    resumed = _open_session_to_resume(args) if args.resume is not None else None
    apply_retention()  # after the resumed session is locked, so it is kept

    agent = common.make_agent()
    if resumed is not None and resumed.mode:
        agent.set_mode(resumed.mode)
    if args.mode:
        agent.set_mode(args.mode)  # an explicit --mode wins over the session's
    print(
        f"Codebud chat  |  model: {agent.get_model() or '(not set)'}  |  "
        f"mode: {agent.get_mode()}  |  /help for commands\n",
        file=sys.stderr,
    )

    from codebud.cli.index_hints import IndexHints, rag_indicator
    from codebud.rag.indexer import project_root

    turn = 0
    checked_turn = 0
    last_plan = None
    last_step_output = None
    checkpoint = _ChatCheckpoint(agent, resumed)
    root = project_root()
    hints = IndexHints(agent, root)
    reader = chat_input.ChatInput(agent, rag_state=lambda: rag_indicator(agent, root))
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

    start_hint = hints.at_start()
    if start_hint:
        print_status(start_hint + "\n")

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
            if not message:
                continue  # an accidental Enter must not end the session
            if message.lower() in ("quit", "exit", "q"):
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
                if cmd == "/privacy":
                    from codebud import privacy

                    parts = message.split(maxsplit=1)
                    if len(parts) > 1:
                        wanted = parts[1].strip().lower()
                        if wanted not in privacy.LEVELS:
                            print_status(
                                f"Unknown privacy level '{wanted}'. "
                                f"Choose one of: {', '.join(privacy.LEVELS)}.\n"
                            )
                            continue
                        os.environ["CODEBUD_PRIVACY"] = wanted
                        common.announce_privacy()
                    print_status(
                        f"Privacy: {privacy.level()}  (choose with /privacy <name>: "
                        f"{', '.join(privacy.LEVELS)})\n"
                    )
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
                    prog = ProgressIndicator(no_progress=common.no_progress(args))
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
                    prog = ProgressIndicator(no_progress=common.no_progress(args))
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
                if cmd == "/sources":
                    _chat_sources(agent)
                    continue
                if cmd == "/rag":
                    _chat_rag(agent, message.split()[1:])
                    continue
                if cmd == "/index":
                    _chat_index(message.split()[1:])
                    hints.refreshed()
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

            prog = ProgressIndicator(no_progress=common.no_progress(args))
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
                    prog = ProgressIndicator(no_progress=common.no_progress(args))
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
                changed_files = False
                for i, step in enumerate(steps, 1):
                    if agent.get_mode() == "full-auto":
                        print_step_header(i, step)
                        step_approval = "y"  # unattended: no step prompts
                    elif agent.get_mode() == "auto":
                        # Plan approved once; only powerful steps (code
                        # runners, file-changing commands) and web searches
                        # ask again. (full-auto runs them unattended.)
                        print_step_header(i, step)
                        reason = agent.confirmation_reason(step)
                        if reason:
                            print_warning(reason)
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
                            changed_files |= _may_change_files(agent, s)
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
                    changed_files |= _may_change_files(agent, step)
                    if result.get("status") == "ok":
                        out = result["results"].get(step["id"], {})
                        print_step_result(out, verbose=args.verbose)
                        last_step_output = out
                    else:
                        print_error(str(result), label="Execution error")
                        break
                if changed_files:
                    hint = hints.after_changes()
                    if hint:
                        print_status(hint)
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
