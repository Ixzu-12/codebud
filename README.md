# Codebud — Local Cursor-Style Coding Agent

Codebud is a local coding agent that turns natural-language requests into
executable plans using a locally-running LLM (Ollama + Qwen2.5-Coder).
No data leaves your machine.

## Quick start

```bash
# 1. Install Ollama (Arch Linux — prefer the AUR package over the curl installer)
yay -S ollama          # or: paru -S ollama
# For other distros: curl -fsSL https://ollama.com/install.sh | sh

# 2. Start the server and pull the recommended model
sudo systemctl enable --now ollama
ollama pull qwen2.5-coder:3b-instruct-q4_K_M

# 3. Get codebud and install it (GNU Autotools)
git clone https://github.com/mkhellat/codebud.git
cd codebud
./bootstrap               # git checkout only; release tarballs ship ./configure
./configure               # checks Python 3.10+, requests, prompt_toolkit, ollama
make && make check
sudo make install         # or: ./configure --prefix=$HOME/.local && make install

# 4. Run
export OLLAMA_MODEL=qwen2.5-coder:3b-instruct-q4_K_M
codebud doctor            # verify the environment
codebud run "your task"

# 5. Optional: let other agents (Claude Code, OpenClaw) use codebud
codebud skill install --for claude    # or: --for openclaw
```

Alternatively, install it with pip like any Python package:
`pip install .` (add `"[openai]"` for the optional OpenAI fallback).

## Build system

`./configure && make` follows the GNU Coding Standards; targets are
GNU/Linux, macOS and FreeBSD (verified so far on Arch Linux). Run `make help`
for the full list.

| Target | Effect |
|---|---|
| `make` / `make check` | Build / run the test suite |
| `make install` / `make uninstall` | Install / remove codebud (prefix: `--prefix`) |
| `make installcheck` | Check the installed `codebud` runs |
| `make info` / `html` / `pdf` | Build the manual |
| `make dist` / `make distcheck` | Build / fully verify the release tarball |
| `make clean` / `make distclean` | Remove what make / configure produced |
| `make maintainer-clean` | Back to a fresh checkout (then `./bootstrap`); `make clean-venv` also drops `.venv/` |

For working on codebud itself:

```bash
./bootstrap --dev                          # also creates .venv/ with the dev tools
./configure PYTHON="$PWD/.venv/bin/python"
make && make check
make lint | format | coverage | dist-python | install-link | clean-dev
```

## Documentation

The full manual lives in `docs/` and is written in GNU Texinfo.

```bash
make info    # → docs/codebud.info   (installed by make install; read with: info codebud)
make html    # → docs/codebud.html
make pdf     # → docs/codebud.pdf
```

Topics covered: Installation · Model Management · Usage · Configuration ·
Architecture · Tools Reference · Safety & Sandbox · Development ·
Troubleshooting · Glossary.

## Project layout

```
src/codebud/    The package: agent, CLI (cli/main.py), tools, default policy
docs/           GNU Texinfo manual
scripts/        Developer scripts (benchmark)
tests/          Test suite (pytest)
Makefile        GNU-standard build system
pyproject.toml  PEP 621 package metadata and tool configuration
```

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `OLLAMA_MODEL` | *(required)* | Model name as shown in `codebud models` |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama server URL |
| `EMBED_MODEL` | same as `OLLAMA_MODEL` | Override model used for embeddings |
| `WEB_SEARCH_ENABLED` | `0` | Set to `1` to enable DuckDuckGo search |
| `OPENAI_API_KEY` | — | Fallback to OpenAI if Ollama is unavailable (needs `pip install "codebud[openai]"`) |

Run `codebud config` to see the resolved values of all variables.

## License

Codebud is free software: you can redistribute it and/or modify it under the
terms of the GNU General Public License as published by the Free Software
Foundation, either version 3 of the License, or (at your option) any later
version (GPL-3.0-or-later). See [COPYING](COPYING) for the full text.
