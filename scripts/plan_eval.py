#!/usr/bin/env python3
"""
plan_eval.py -- Check whether a model's plans target the right file.

Plans six requests about codebud's own source (five edits, one lookup),
with project context from the index, and checks each plan: correct when
its steps name the file that answers the request (a file_read/file_write
path, a patch's "+++ b/" header, or a path in a command) and every step
has the arguments its tool requires. Nothing is executed.

Usage (from the repository root, Ollama running, index built):

    codebud index
    python scripts/plan_eval.py qwen2.5-coder:3b-instruct-q4_K_M qwen2.5-coder:7b-instruct-q4_K_M
    python scripts/plan_eval.py --json MODEL      # one JSON line per plan

A plan takes minutes on a CPU, so a run with two models takes about half
an hour.

Requirements: the dev environment (make install-dev), numpy, the models.
"""

import argparse
import json
import os
import re
import sys
import time

sys.path.insert(0, "src")

from codebud.core import AgentCore  # noqa: E402

REQUESTS = [
    ("add a docstring to the toolbar_text function", "src/codebud/cli/chat_input.py"),
    ("rename the local variable found to results in candidate_files", "src/codebud/rag/files.py"),
    ("change the RRF constant used for fusing rankings from 60 to 50", "src/codebud/rag/search.py"),
    (
        "add a one-line comment above the line in compact_history that builds the summary entry",
        "src/codebud/core.py",
    ),
    ("change the planner's context share from 0.25 to 0.3", "src/codebud/planner.py"),
    ("show me how chat sessions are locked against a second resume", "src/codebud/sessions.py"),
]

# The arguments each tool needs (see the tools reference).
REQUIRED_ARGS = {
    "file_read": {"path"},
    "file_write": {"path"},
    "patch": {"patch"},
    "command": {"cmd"},
}


def plan_targets(plan: dict) -> tuple[list[str], bool]:
    """Paths the plan's steps touch, and whether every step's args are valid."""
    paths, valid = [], True
    for step in plan.get("plan", []):
        args = step.get("args", {})
        tool = step.get("tool")
        if tool in REQUIRED_ARGS and not REQUIRED_ARGS[tool] <= set(args):
            valid = False
        if "path" in args:
            paths.append(str(args["path"]).removeprefix("./"))
        if tool == "patch":
            paths += re.findall(r"^\+\+\+ b/(\S+)", str(args.get("patch", "")), re.M)
        if tool == "command":
            paths += re.findall(r"src/\S+\.py", str(args.get("cmd", "")))
    return paths, valid


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("models", nargs="+", help="Ollama models to compare")
    parser.add_argument("--json", action="store_true", help="print one JSON line per plan")
    args = parser.parse_args()

    summary = []
    for model in args.models:
        os.environ["OLLAMA_MODEL"] = model
        agent = AgentCore()
        rows = []
        for request, expected in REQUESTS:
            agent.clear_history()
            started = time.monotonic()
            plan = agent.handle_user_message(request)
            seconds = time.monotonic() - started
            paths, valid = plan_targets(plan)
            ok = plan.get("status") == "ok" and valid and expected in paths
            row = {
                "model": model,
                "request": request,
                "expected": expected,
                "ok": ok,
                "valid_args": valid,
                "status": plan.get("status"),
                "paths": paths,
                "seconds": round(seconds),
            }
            rows.append(row)
            if args.json:
                print(json.dumps(row), flush=True)
            else:
                mark = "ok " if ok else "BAD"
                print(f"{mark} {seconds:5.0f} s  {request[:60]}  ->  {', '.join(paths) or '-'}")
        agent.close()
        n = len(rows)
        summary.append(
            f"{model}: {sum(r['ok'] for r in rows)}/{n} correct, "
            f"{sum(r['valid_args'] for r in rows)}/{n} with valid arguments, "
            f"{sum(r['seconds'] for r in rows) / n:.0f} s per plan"
        )
    if not args.json:
        print("\n" + "\n".join(summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())
