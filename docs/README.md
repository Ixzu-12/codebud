# Codebud Documentation

The Codebud manual is written in GNU Texinfo.  It produces HTML, PDF, and
`info` pages from a single source.

## Prerequisites

```bash
sudo pacman -S texinfo          # Arch Linux
# or: sudo apt install texinfo  # Debian/Ubuntu
```

## Build

The manual is built by the top-level GNU Autotools build (run `./bootstrap`
and `./configure` in the repository root first):

```bash
make info    # → docs/codebud.info
make html    # → docs/codebud.html   (single page)
make pdf     # → docs/codebud.pdf    (needs TeX)
```

`make install` installs the info manual and registers it with
`install-info`. `docs/version.texi` is generated from the package version
(single source: `pyproject.toml`).

## Read it

```bash
info codebud                  # after make install
info -f docs/codebud.info     # from the build tree
```

## Source layout

```
docs/
├── codebud.texi          top-level manual
├── version.texi          generated: version / date (not in git)
├── Makefile.am           automake input
└── chapters/
    ├── introduction.texi
    ├── installation.texi
    ├── model-management.texi
    ├── llm-execution.texi
    ├── performance-benchmarking.texi
    ├── usage.texi
    ├── configuration.texi
    ├── architecture.texi
    ├── tools-reference.texi
    ├── safety-and-sandbox.texi
    ├── development.texi
    ├── troubleshooting.texi
    └── glossary.texi
```
