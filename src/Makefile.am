# src/Makefile.am — installs the codebud package.
#
# Files are listed explicitly (automake has no wildcards);
# tests/test_build.py fails if a module or data file is missing here.

nobase_python_PYTHON = \
	codebud/__init__.py \
	codebud/__main__.py \
	codebud/cli/__init__.py \
	codebud/cli/chat_input.py \
	codebud/cli/display.py \
	codebud/cli/doctor.py \
	codebud/cli/errors.py \
	codebud/cli/main.py \
	codebud/core.py \
	codebud/executor.py \
	codebud/fsutil.py \
	codebud/llm_stub.py \
	codebud/memory.py \
	codebud/modes.py \
	codebud/paths.py \
	codebud/planner.py \
	codebud/safety.py \
	codebud/sandbox.py \
	codebud/sessions.py \
	codebud/skill.py \
	codebud/tools/__init__.py \
	codebud/tools/commands.py \
	codebud/tools/embeddings.py \
	codebud/tools/file_io.py \
	codebud/tools/patcher.py \
	codebud/tools/tool_registry.py \
	codebud/tools/web_search.py

nobase_dist_python_DATA = \
	codebud/data/policy/harmless_commands.json \
	codebud/data/policy/powerful_commands.json \
	codebud/data/policy/trusted_sequences.json \
	codebud/data/skill/SKILL.md

# A minimal dist-info directory, so importlib.metadata (and therefore
# `codebud version`) knows the installed version without pip.
distinfodir = $(pythondir)/codebud-$(VERSION).dist-info

install-data-hook:
	$(MKDIR_P) "$(DESTDIR)$(distinfodir)"
	printf 'Metadata-Version: 2.1\nName: codebud\nVersion: %s\n' '$(VERSION)' \
	  > "$(DESTDIR)$(distinfodir)/METADATA"
	printf 'autotools\n' > "$(DESTDIR)$(distinfodir)/INSTALLER"

uninstall-hook:
	rm -f "$(DESTDIR)$(distinfodir)/METADATA" "$(DESTDIR)$(distinfodir)/INSTALLER"
	-rmdir "$(DESTDIR)$(distinfodir)" 2>/dev/null
