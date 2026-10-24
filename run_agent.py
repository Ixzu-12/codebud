"""
run_agent.py

Codebud CLI entry point.

Subcommands
-----------
  run "<msg>"     Plan + auto-execute a request (default when no subcommand given)
                  --interactive/-i prompts for approval before each step
                  --model/-m <name> overrides OLLAMA_MODEL, validated + warmed first
  plan "<msg>"    Plan only — print the plan without executing
                  --execute/-x also runs the steps and prints full output
                  (cannot combine with --json)
                  --model/-m <name> overrides OLLAMA_MODEL, validated + warmed first
  chat            Interactive REPL: multiple requests in one session
                  --model/-m <name> overrides OLLAMA_MODEL at startup;
                  /model <name> switches mid-session, /models lists available
  doctor          Check the environment (Ollama, model, config, RAM, disk)
  history         Show past step executions (data/memory/entries.jsonl)
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


def _apply_model_override(args) -> bool:
    """Handle --model/-m: validate it's downloaded, warm it with live progress,
    then set OLLAMA_MODEL so agent construction picks it up.

    Returns False (after printing an error) if the override was requested but
    invalid, so the caller can abort before spending time on planning.
    """
    requested = getattr(args, "model", None)
    if not requested:
        return True

    from agent.cli.display import ProgressIndicator
    from agent.llm_stub import list_models, warm_model_sync

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
    from agent.cli.display import (
        ProgressIndicator,
        print_plan,
        print_status,
        print_step_header,
        print_step_result,
    )
    from agent.cli.errors import print_error

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
                print_step_result(out, verbose=args.verbose, full=True)
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
    from agent.cli.display import (
        ProgressIndicator,
        print_plan,
        print_step_header,
        print_step_result,
    )
    from agent.cli.errors import print_error

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


_CHAT_HELP = """\
Slash commands (type /cmd and press Enter):
  /help      — show this message
  /history   — show the conversation history kept in memory
  /clear     — wipe conversation history and start fresh
  /undo      — remove the last turn from history
  /compact   — summarise older turns to shrink the conversation history
  /doctor    — run environment health checks
  /model     — show the active Ollama model, or /model <name> to switch
  /models    — list downloaded models, marking the active one
  /plan      — re-show the last plan (if any)
  /output    — show the full stdout of the last executed step (untruncated)
  quit / exit / q — end the session
"""


def cmd_chat(args):
    from agent.cli.display import (
        ProgressIndicator,
        print_assistant_reply,
        print_plan,
        print_rule,
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

    if not _apply_model_override(args):
        sys.exit(1)

    agent = _make_agent()
    print(
        f"Codebud chat  |  model: {agent.get_model() or '(not set)'}  |  /help for commands\n",
        file=sys.stderr,
    )

    turn = 0
    last_plan = None
    last_step_output = None

    try:
        while True:
            print_rule()
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
                    from agent.cli.doctor import run_doctor

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
                    from agent.llm_stub import list_models

                    names = list_models()
                    if not names:
                        print_status("Cannot reach Ollama to list models.\n")
                        continue
                    from agent.cli.display import _GREEN, _RESET

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
            # --- plan review loop: accept / revise / skip ---
            while True:
                print_plan(plan, verbose=args.verbose)
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
    """Show past step executions recorded in data/memory/entries.jsonl.

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
        print(
            f"{_OUT_CYAN}[{i}]{_OUT_RESET} {_OUT_DIM}{ts}{_OUT_RESET}  {tool:<16} {status} {desc}"
        )


# ---------------------------------------------------------------------------
# Subcommand: models
# ---------------------------------------------------------------------------


def cmd_models(_args):
    import requests as _req

    from agent.cli.display import _OUT_BOLD, _OUT_GREEN, _OUT_RESET
    from agent.llm_stub import ollama_proxies

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
    from agent.cli.display import _OUT_BOLD, _OUT_DIM, _OUT_RESET
    from agent.llm_stub import num_ctx

    base = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
    model = os.environ.get("OLLAMA_MODEL", "(not set)")
    embed = os.environ.get("EMBED_MODEL", model)
    openai_key = "(set)" if os.environ.get("OPENAI_API_KEY") else "(not set)"
    web_search = os.environ.get("WEB_SEARCH_ENABLED", "0")

    print(f"{_OUT_BOLD}Codebud resolved configuration:{_OUT_RESET}")
    print(f"  {_OUT_DIM}OLLAMA_MODEL      {_OUT_RESET} = {model}")
    print(f"  {_OUT_DIM}OLLAMA_BASE_URL   {_OUT_RESET} = {base}")
    print(f"  {_OUT_DIM}EMBED_MODEL       {_OUT_RESET} = {embed}")
    print(f"  {_OUT_DIM}CODEBUD_NUM_CTX   {_OUT_RESET} = {num_ctx()}")
    print(f"  {_OUT_DIM}OPENAI_API_KEY    {_OUT_RESET} = {openai_key}")
    print(f"  {_OUT_DIM}WEB_SEARCH_ENABLED{_OUT_RESET} = {web_search}")


# ---------------------------------------------------------------------------
# Subcommand: version
# ---------------------------------------------------------------------------


def cmd_version(_args):
    import requests as _req

    from agent.llm_stub import ollama_proxies

    model = os.environ.get("OLLAMA_MODEL", "(not set)")
    base = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")

    # Try to get Ollama version
    try:
        r = _req.get(f"{base}/api/version", timeout=3, proxies=ollama_proxies(base))
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
    sub.add_parser("chat", help="Interactive multi-turn session.", parents=[model_parent])

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
