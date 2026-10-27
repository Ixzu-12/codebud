---
name: codebud
description: Local-first coding agent that plans and runs multi-step coding tasks (read files, run commands, write patches) on-device with Ollama, no API key or network needed. Use it to delegate a coding task to a local model, e.g. when the user wants work done privately or offline.
metadata:
  {
    "openclaw": {
      "emoji": "🐛",
      "requires": { "bins": ["codebud"] }
    }
  }
---

# Codebud — local-first coding agent

Codebud turns a natural-language request into a plan of steps (read a file,
run a command, write or patch a file) and executes them, using a model that
runs locally in Ollama. Nothing leaves the machine.

## Before using it

Ollama must be running with a model downloaded, and `OLLAMA_MODEL` must name
it (there is no default):

```bash
ollama serve &                                   # if not already running
ollama pull qwen2.5-coder:3b-instruct-q4_K_M
export OLLAMA_MODEL=qwen2.5-coder:3b-instruct-q4_K_M
codebud doctor                                   # checks everything, prints fixes
```

Codebud works on the files of the directory it is started in, so `cd` into
the project first.

## Running a task from another agent

`codebud run "<task>"` plans the task and executes it. From another agent,
add the global `--no-progress` flag (before `run`) so the output has no
spinner:

```bash
codebud --no-progress run "read README.md and summarise it"
```

Powerful commands — ones that run code or change files (`python`, `pytest`,
`ruff`, `cp`, `mv`, `sed`, ...) — need a confirmation. Called without a
terminal, `run` skips them and exits with status 1, saying so. Pass `--yes`
to allow them unattended, once the user has agreed:

```bash
codebud --no-progress run --yes "run the test suite and report failures"
```

Exit status: 0 when every step ran, 1 on a planning or execution error or
when a powerful step was not confirmed.

## Inspecting a plan without running it

```bash
codebud plan "add type hints to utils.py"
codebud plan --json "run the tests"     # machine-readable plan
codebud plan -x "list the python files" # plan, then execute it
```

## Interactive use (for the user, not for agents)

```bash
codebud chat                     # multi-turn session
codebud chat --mode plan         # modes: ask, auto, full-auto, plan, edit
codebud chat --resume            # continue the most recent session
codebud sessions                 # list saved sessions
```

Modes: `ask` approves the plan and each step; `auto` approves the plan once
(powerful steps still ask); `full-auto` asks nothing; `plan` never executes;
`edit` allows only file tools, no shell commands.

## Other commands

| Command            | Purpose                                           |
| ------------------ | ------------------------------------------------- |
| `codebud doctor`   | Check Python, Ollama, the model and policy files  |
| `codebud models`   | List downloaded models, marking the active one    |
| `codebud config`   | Show settings, data directory and policy files    |
| `codebud history`  | Show past executed steps                          |
| `codebud version`  | Show versions                                     |

## Safety

Only commands on an allow-list run, matched by whole words; arguments that
turn a program into "run anything" (`find -exec`, `python -c`, ...) are
refused. Users can adjust the policy in `~/.config/codebud/` or a project's
`.codebud/`.

## Environment variables

| Variable             | Default                  | Purpose                                |
| -------------------- | ------------------------ | -------------------------------------- |
| `OLLAMA_MODEL`       | *(required)*             | Model to plan with                     |
| `OLLAMA_BASE_URL`    | `http://localhost:11434` | Ollama server                          |
| `CODEBUD_NUM_CTX`    | `8192`                   | Context window requested from Ollama   |
| `WEB_SEARCH_ENABLED` | `0`                      | `1` enables the web search tool        |
