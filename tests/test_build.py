"""
tests/test_build.py

The GNU Autotools build lists every installed file explicitly (automake has
no wildcards). These tests fail when a module, data file or manual chapter
is added without being listed, which would otherwise silently leave it out
of `make install` and the release tarball.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _am_list(makefile_am: Path, variable: str) -> set[str]:
    """Return the words assigned to ``variable`` in a Makefile.am."""
    text = makefile_am.read_text().replace("\\\n", " ")
    match = re.search(rf"^{re.escape(variable)}\s*=\s*(.*)$", text, re.M)
    assert match, f"{variable} not found in {makefile_am}"
    return set(match.group(1).split())


def test_every_python_module_is_installed():
    package = ROOT / "src"
    on_disk = {
        str(p.relative_to(package))
        for name in ("codebud", "codebud_sec")
        for p in (package / name).rglob("*.py")
        if "__pycache__" not in p.parts
    }
    assert _am_list(package / "Makefile.am", "nobase_python_PYTHON") == on_disk


def test_every_package_data_file_is_installed():
    package = ROOT / "src"
    on_disk = {
        str(p.relative_to(package))
        for p in (package / "codebud" / "data").rglob("*")
        if p.is_file()
    }
    assert _am_list(package / "Makefile.am", "nobase_dist_python_DATA") == on_disk


def test_every_manual_chapter_is_listed():
    docs = ROOT / "docs"
    on_disk = {str(p.relative_to(docs)) for p in (docs / "chapters").glob("*.texi")}
    assert _am_list(docs / "Makefile.am", "codebud_TEXINFOS") == on_disk


def test_configure_takes_its_version_from_pyproject():
    configure_ac = (ROOT / "configure.ac").read_text()
    assert "pyproject.toml" in configure_ac
    assert re.search(r"AC_INIT\(\[codebud\],\s*m4_esyscmd_s", configure_ac)
