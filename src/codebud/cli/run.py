"""
codebud/cli/run.py

The one-shot subcommands: `codebud run` (plan and execute) and
`codebud plan` (plan only; --execute runs it, --json prints it).
"""

import json
import sys

from codebud.cli import common


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

    if not common.apply_model_override(args):
        sys.exit(1)

    agent = common.make_agent()
    try:
        prog = ProgressIndicator(no_progress=common.no_progress(args))
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
        # runners, file-changing commands) and web searches (data leaves
        # the machine), which need a "y" unless --yes.
        confirm_some = not args.interactive and not args.yes
        unconfirmed = 0
        for i, step in enumerate(steps, 1):
            print_step_header(i, step)

            reason = agent.confirmation_reason(step) if confirm_some else None
            if reason:
                print_warning(reason)
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
                f"{unconfirmed} step(s) were not run because no one could confirm them "
                "(powerful commands, web search). Re-run with --yes to allow them unattended."
            )
            sys.exit(1)
    finally:
        agent.close()


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

    if not common.apply_model_override(args):
        sys.exit(1)

    agent = common.make_agent()
    try:
        prog = ProgressIndicator(no_progress=common.no_progress(args))
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
