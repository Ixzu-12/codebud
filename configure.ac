# configure.ac — GNU Autoconf input for codebud.
#
# Generate ./configure with ./bootstrap (autoreconf), then:
#     ./configure && make && make check && make install
#
# Targets GNU/Linux (Debian/Ubuntu, Fedora/RHEL, Arch), macOS and FreeBSD.
# The generated configure is portable POSIX sh.

AC_PREREQ([2.69])

# The version has one source of truth: pyproject.toml.
AC_INIT([codebud],
        m4_esyscmd_s([sed -n 's/^version = "\(.*\)"$/\1/p' pyproject.toml]),
        [https://github.com/mkhellat/codebud/issues],
        [codebud],
        [https://github.com/mkhellat/codebud])
AC_CONFIG_SRCDIR([src/codebud/__init__.py])
AC_CONFIG_AUX_DIR([build-aux])

# foreign: the GNU file set (NEWS, AUTHORS, ChangeLog) is not required.
AM_INIT_AUTOMAKE([1.16 foreign -Wall -Werror])
AM_SILENT_RULES([yes])

AC_PROG_SED
AC_PROG_MKDIR_P
AC_PROG_LN_S

# ─── Python ───────────────────────────────────────────────────────────────
# Choose a specific interpreter with: ./configure PYTHON=/path/to/python3
AM_PATH_PYTHON([3.10])

# Runtime dependencies come from the system (or the active virtualenv).
# Distribution builds run in clean chroots without them: they can pass
# --disable-dependency-check and declare the dependencies in the package.
AC_ARG_ENABLE([dependency-check],
  [AS_HELP_STRING([--disable-dependency-check],
    [do not require the runtime Python modules at configure time])],
  [], [enable_dependency_check=yes])

AS_IF([test "x$enable_dependency_check" != xno], [
  AC_MSG_CHECKING([for the Python modules codebud needs])
  codebud_missing=
  for codebud_mod in requests prompt_toolkit; do
    "$PYTHON" -c "import $codebud_mod" >/dev/null 2>&1 \
      || codebud_missing="$codebud_missing $codebud_mod"
  done
  AS_IF([test -n "$codebud_missing"], [
    AC_MSG_RESULT([missing:$codebud_missing])
    AC_MSG_ERROR([install the missing Python modules, for example:
    Debian/Ubuntu:  apt install python3-requests python3-prompt-toolkit
    Fedora/RHEL:    dnf install python3-requests python3-prompt-toolkit
    Arch Linux:     pacman -S python-requests python-prompt_toolkit
    FreeBSD:        pkg install py311-requests py311-prompt-toolkit
    macOS:          $PYTHON -m pip install requests prompt_toolkit  (in a venv)
or point configure at a Python that has them: ./configure PYTHON=/path/to/python3
(--disable-dependency-check skips this check, e.g. for distribution builds)])
  ], [AC_MSG_RESULT([yes])])
])

# numpy is optional: only project search (codebud index) needs it.
AC_MSG_CHECKING([whether $PYTHON has numpy (for project search)])
AS_IF(["$PYTHON" -c "import numpy" >/dev/null 2>&1],
  [codebud_have_numpy=yes], [codebud_have_numpy=no])
AC_MSG_RESULT([$codebud_have_numpy])
AS_IF([test "x$codebud_have_numpy" = xno], [
  AC_MSG_WARN([numpy not found; project search will be unavailable until it is
installed (apt install python3-numpy, dnf install python3-numpy,
pacman -S python-numpy, pkg install py311-numpy, or pip install numpy)])
])

# pytest is only needed for `make check`.
AC_MSG_CHECKING([whether $PYTHON has pytest])
AS_IF(["$PYTHON" -c "import pytest" >/dev/null 2>&1],
  [codebud_have_pytest=yes], [codebud_have_pytest=no])
AC_MSG_RESULT([$codebud_have_pytest])
AM_CONDITIONAL([HAVE_PYTEST], [test "x$codebud_have_pytest" = xyes])

# ─── Ollama (runtime only) ──────────────────────────────────────────────────
AC_PATH_PROG([OLLAMA], [ollama])
AS_IF([test -z "$OLLAMA"], [
  AC_MSG_WARN([ollama was not found; codebud needs a running Ollama server
(https://ollama.com), local or remote via OLLAMA_BASE_URL])
])

AC_CONFIG_FILES([Makefile src/Makefile docs/Makefile])
AC_OUTPUT

AC_MSG_NOTICE([
  codebud $PACKAGE_VERSION
    prefix:          $prefix
    Python:          $PYTHON ($PYTHON_VERSION)
    package dir:     $pythondir
    tests (pytest):  $codebud_have_pytest
    project search:  $codebud_have_numpy (numpy)
    ollama:          ${OLLAMA:-not found}
])
