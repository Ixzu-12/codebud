"""
tests/test_rag_chunker.py

Splitting files into retrievable chunks (phase 9, B1): by structure where
the format has one (Python definitions, Texinfo nodes, Markdown headings),
by overlapping line windows otherwise.
"""

import textwrap

from codebud.rag import chunker
from codebud.rag.chunker import Chunk, chunk_file


def _src(text: str) -> str:
    return textwrap.dedent(text).lstrip("\n")


def _lines(chunk: Chunk) -> tuple[int, int]:
    return chunk.start_line, chunk.end_line


# --- Python -----------------------------------------------------------------

PY = _src(
    '''
    """Module docstring."""

    import os

    LIMIT = 3


    @decorator
    def first(a):
        return a


    class Thing:
        """A thing."""

        def method(self):
            return 1


    async def last():
        pass
    '''
)


def test_python_top_level_definitions_become_chunks():
    chunks = chunk_file("pkg/mod.py", PY)
    by_symbol = {c.symbol: c for c in chunks}
    assert set(by_symbol) == {"<module>", "first", "Thing", "last"}
    # Decorators belong to their function.
    assert _lines(by_symbol["first"]) == (8, 10)
    assert by_symbol["first"].text.startswith("@decorator\ndef first(a):")
    assert _lines(by_symbol["Thing"]) == (13, 17)
    assert _lines(by_symbol["last"]) == (20, 21)
    assert all(c.kind == "python" and c.path == "pkg/mod.py" for c in chunks)


def test_python_module_level_code_is_kept_with_its_lines():
    module = [c for c in chunk_file("m.py", PY) if c.symbol == "<module>"]
    assert len(module) == 1
    assert _lines(module[0]) == (1, 5)
    assert module[0].text == '"""Module docstring."""\n\nimport os\n\nLIMIT = 3'


def test_large_python_class_is_split_into_methods(monkeypatch):
    monkeypatch.setattr(chunker, "MAX_LINES", 6)
    src = _src(
        """
        class Big:
            x = 1

            def a(self):
                return 1

            def b(self):
                return 2
        """
    )
    symbols = [c.symbol for c in chunk_file("big.py", src)]
    assert symbols == ["Big", "Big.a", "Big.b"]


def test_oversized_definition_is_windowed_and_keeps_its_symbol(monkeypatch):
    monkeypatch.setattr(chunker, "MAX_LINES", 4)
    monkeypatch.setattr(chunker, "OVERLAP", 1)
    body = "\n".join(f"    x{i} = {i}" for i in range(8))
    chunks = chunk_file("f.py", f"def long():\n{body}\n")
    assert {c.symbol for c in chunks} == {"long"}
    assert [_lines(c) for c in chunks] == [(1, 4), (4, 7), (7, 9)]


def test_python_syntax_error_falls_back_to_line_windows():
    chunks = chunk_file("broken.py", "def oops(:\n    pass\n")
    assert [c.kind for c in chunks] == ["text"]
    assert chunks[0].symbol is None


# --- Texinfo and Markdown ---------------------------------------------------


def test_texinfo_splits_on_nodes():
    src = _src(
        """
        @c header
        @node Top
        @top Manual

        @node Pulling a Model, Next, Prev, Up
        @section Pulling
        Text.
        """
    )
    chunks = chunk_file("docs/x.texi", src)
    assert [(c.symbol, _lines(c)) for c in chunks] == [
        (None, (1, 1)),
        ("Top", (2, 3)),
        ("Pulling a Model", (5, 7)),
    ]
    assert all(c.kind == "texinfo" for c in chunks)


def test_markdown_splits_on_headings_but_not_inside_code_fences():
    src = _src(
        """
        # Title
        intro
        ```sh
        # not a heading
        ```
        ## Install
        steps
        """
    )
    chunks = chunk_file("README.md", src)
    assert [(c.symbol, _lines(c)) for c in chunks] == [("Title", (1, 5)), ("Install", (6, 7))]
    assert all(c.kind == "markdown" for c in chunks)


# --- other text -------------------------------------------------------------


def test_other_text_uses_overlapping_line_windows(monkeypatch):
    monkeypatch.setattr(chunker, "TEXT_WINDOW", 4)
    monkeypatch.setattr(chunker, "OVERLAP", 1)
    text = "\n".join(f"line {i}" for i in range(1, 11)) + "\n"
    chunks = chunk_file("notes.txt", text)
    assert [_lines(c) for c in chunks] == [(1, 4), (4, 7), (7, 10)]
    assert chunks[0].text == "line 1\nline 2\nline 3\nline 4"


def test_blank_chunks_are_dropped():
    assert chunk_file("empty.py", "") == []
    assert chunk_file("blank.txt", "\n\n   \n") == []
    assert chunk_file("x.md", "# Only\n\n") == [Chunk("x.md", 1, 1, "Only", "markdown", "# Only")]
