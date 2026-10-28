"""
codebud/cli/run.py

The one-shot subcommands: `codebud run` (plan and execute) and
`codebud plan` (plan only; --execute runs it, --json prints it).
"""

import json
import sys

from codebud.cli import common
from codebud.cli.steps import run_steps


def cmd_run(args):
    from codebud.cli.display import (
        ProgressIndicator,
        print_plan,
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

        # -i asks before every step; otherwise steps run unprompted except
        # powerful ones and web searches, which need a "y" unless --yes.
        policy = "ask" if args.interactive else "full-auto" if args.yes else "auto"
        outcome = run_steps(
            agent, plan.get("plan", []), policy, verbose=args.verbose, full_output=True
        )
        if outcome.failed:
            sys.exit(1)
        if outcome.unconfirmed:
            print_warning(
                f"{outcome.unconfirmed} step(s) were not run because no one could confirm them "
                "(powerful commands, web search). Re-run with --yes to allow them unattended."
            )
            sys.exit(1)
    finally:
        agent.close()


def cmd_plan(args):
    from codebud.cli.display import (
        ProgressIndicator,
        print_plan,
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

        outcome = run_steps(
            agent, plan.get("plan", []), "full-auto", verbose=args.verbose, full_output=True
        )
        if outcome.failed:
            sys.exit(1)
    finally:
        agent.close()
