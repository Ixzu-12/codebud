"""
run_agent.py

Codebud CLI entry point.

Subcommands
-----------
  run "<msg>"     Plan + auto-execute a request (default when no subcommand given)
                  --interactive/-i prompts for approval before each step
  plan "<msg>"    Plan only — print the plan without executing
  chat            Interactive REPL: multiple requests in one session
  doctor          Check the environment (Ollama, model, config, RAM, disk)
  history         Show past step executions (data/memory/entries.json)
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

_KNOWN_SUBCOMMANDS = {"run", "plan", "chat", "doctor", "history", "models", "config", "version"}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_agent():
    from agent.core import AgentCore

    return AgentCore()


def _no_progress(args) -> bool:
    return getattr(args, "no_progress", False) or not sys.stderr.isatty()


# ---------------------------------------------------------------------------
# Subcommand: run
# ---------------------------------------------------------------------------


def cmd_run(args):
    from agent.cli.display import (
        ProgressIndicator,
        print_plan,
        print_status,
        print_step_header,
        print_step_result,
    )
    from agent.cli.errors import print_error

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
        for i, step in enumerate(steps, 1):
            print_step_header(i, step)

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
                print_step_result(out, verbose=args.verbose)
            else:
                err = str(result)
                print_error(err, label="Execution error")
                sys.exit(1)
    finally:
        agent.close()


# ---------------------------------------------------------------------------
# Subcommand: plan (plan only, no execution)
# ---------------------------------------------------------------------------


def cmd_plan(args):
    from agent.cli.display import ProgressIndicator, print_plan
    from agent.cli.errors import print_error

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
        else:
            print_plan(plan, verbose=args.verbose)
    finally:
        agent.close()


# ---------------------------------------------------------------------------
# Subcommand: chat (interactive REPL)
# ---------------------------------------------------------------------------


_CHAT_HELP = """\
Slash commands (type /cmd and press Enter):
  /help      — show this message
  /history   — show the conversation history kept in memory
  /clear     — wipe conversation history and start fresh
  /undo      — remove the last turn from history
  /doctor    — run environment health checks
  /model     — show the active Ollama model
  /plan      — re-show the last plan (if any)
  quit / exit / q — end the session
"""


def cmd_chat(args):
    from agent.cli.display import (
        ProgressIndicator,
        print_assistant_reply,
        print_plan,
        print_status,
        print_step_header,
        print_step_result,
    )
    from agent.cli.errors import print_error

    try:
        import readline as _rl  # noqa: F401 — enables arrow-key history and Ctrl+R in input()
        _rl.parse_and_bind("tab: complete")
    except ImportError:
        pass

    agent = _make_agent()
    model_name = os.environ.get("OLLAMA_MODEL", "(not set)")
    print(
        f"Codebud chat  |  model: {model_name}  |  /help for commands\n",
        file=sys.stderr,
    )

    turn = 0
    last_plan = None

    try:
        while True:
            try:
                message = input(f"[{turn + 1}] You: ").strip()
            except (EOFError, KeyboardInterrupt):
                print(file=sys.stderr)
                break
            if not message or message.lower() in ("quit", "exit", "q"):
                break

            # --- slash command handling ---
            if message.startswith("/"):
                cmd = message.split()[0].lower()
                if cmd == "/help":
                    print(_CHAT_HELP, file=sys.stderr)
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
                if cmd == "/doctor":
                    from agent.cli.doctor import run_doctor
                    run_doctor()
                    continue
                if cmd == "/model":
                    print_status(f"Active model: {model_name}\n")
                    continue
                if cmd == "/plan":
                    if last_plan:
                        print_plan(last_plan, verbose=args.verbose)
                    else:
                        print_status("(no plan yet)\n")
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
            # --- plan review loop: accept / revise / skip ---
            while True:
                print_plan(plan, verbose=args.verbose)
                try:
                    review = input(
                        "  [y] execute  [r] revise  [r: feedback] revise with note  [n] skip  "
                    ).strip()
                except (EOFError, KeyboardInterrupt):
                    print(file=sys.stderr)
                    review = "n"

                if review.lower() == "n" or review == "":
                    print_status("-- skipped")
                    break

                if review.lower().startswith("r"):
                    feedback = review[1:].lstrip(": ").strip()
                    revision_msg = message if not feedback else f"{message} (revision note: {feedback})"
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
                    print_step_header(i, step)
                    try:
                        step_approval = input(
                            f"  Execute step {i}? [y/N/a(all)/q(quit)] "
                        ).strip().lower()
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
                    else:
                        print_error(str(result), label="Execution error")
                        break
                break  # done executing — exit review loop

            print(file=sys.stderr)
    finally:
        agent.close()


# ---------------------------------------------------------------------------
# Subcommand: doctor
# ---------------------------------------------------------------------------


def cmd_doctor(_args):
    from agent.cli.doctor import run_doctor

    sys.exit(run_doctor())


# ---------------------------------------------------------------------------
# Subcommand: history
# ---------------------------------------------------------------------------


def cmd_history(args):
    """Show past step executions recorded in data/memory/entries.json.

    This is persisted, cross-session execution history (MemoryStore), distinct
    from the in-session conversation history shown by /history in `chat`.
    """
    from agent.cli.display import _OUT_CYAN, _OUT_DIM, _OUT_GREEN, _OUT_RED, _OUT_RESET
    from agent.memory import MemoryStore

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
        print(f"{_OUT_CYAN}[{i}]{_OUT_RESET} {_OUT_DIM}{ts}{_OUT_RESET}  {tool:<16} {status} {desc}")


# ---------------------------------------------------------------------------
# Subcommand: models
# ---------------------------------------------------------------------------


def cmd_models(_args):
    import requests as _req

    from agent.cli.display import _OUT_BOLD, _OUT_GREEN, _OUT_RESET

    base = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
    selected = os.environ.get("OLLAMA_MODEL", "")
    try:
        r = _req.get(f"{base}/api/tags", timeout=5)
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
    from agent.cli.display import _OUT_BOLD, _OUT_DIM, _OUT_RESET

    base = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
    model = os.environ.get("OLLAMA_MODEL", "(not set)")
    embed = os.environ.get("EMBED_MODEL", model)
    openai_key = "(set)" if os.environ.get("OPENAI_API_KEY") else "(not set)"
    web_search = os.environ.get("WEB_SEARCH_ENABLED", "0")

    print(f"{_OUT_BOLD}Codebud resolved configuration:{_OUT_RESET}")
    print(f"  {_OUT_DIM}OLLAMA_MODEL      {_OUT_RESET} = {model}")
    print(f"  {_OUT_DIM}OLLAMA_BASE_URL   {_OUT_RESET} = {base}")
    print(f"  {_OUT_DIM}EMBED_MODEL       {_OUT_RESET} = {embed}")
    print(f"  {_OUT_DIM}OPENAI_API_KEY    {_OUT_RESET} = {openai_key}")
    print(f"  {_OUT_DIM}WEB_SEARCH_ENABLED{_OUT_RESET} = {web_search}")


# ---------------------------------------------------------------------------
# Subcommand: version
# ---------------------------------------------------------------------------


def cmd_version(_args):
    import requests as _req

    model = os.environ.get("OLLAMA_MODEL", "(not set)")
    base = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")

    # Try to get Ollama version
    try:
        r = _req.get(f"{base}/api/version", timeout=3)
        ollama_ver = r.json().get("version", "unknown") if r.ok else "unreachable"
    except Exception:
        ollama_ver = "unreachable"

    print("codebud  0.1.0")
    print(f"ollama   {ollama_ver}")
    print(f"model    {model}")
    print(f"python   {sys.version.split()[0]}")


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="codebud",
        description="Codebud — local coding agent powered by Ollama.",
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

    sub = parser.add_subparsers(dest="subcommand")

    # run
    p_run = sub.add_parser("run", help="Plan and execute a request.")
    p_run.add_argument("message", help="Natural-language request.")
    p_run.add_argument(
        "--interactive",
        "-i",
        action="store_true",
        help="Prompt for approval before executing each step.",
    )

    # plan
    p_plan = sub.add_parser("plan", help="Plan a request without executing it.")
    p_plan.add_argument("message", help="Natural-language request.")
    p_plan.add_argument("--json", action="store_true", help="Output raw JSON plan.")

    # chat
    sub.add_parser("chat", help="Interactive multi-turn session.")

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

    dispatch = {
        "run": cmd_run,
        "plan": cmd_plan,
        "chat": cmd_chat,
        "doctor": cmd_doctor,
        "history": cmd_history,
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
