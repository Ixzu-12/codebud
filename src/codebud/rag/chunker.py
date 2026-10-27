"""
codebud/rag/chunker.py

Split a file into chunks for the project index (phase 9, B1).

Chunks follow the file's structure where it has one, so a retrieved chunk
is a meaningful unit with a name:

- Python: top-level functions and classes via ``ast`` (decorators
  included); module-level code between them becomes ``<module>`` chunks;
  a class longer than MAX_LINES is split into its header and its methods
  (``Class.method``).
- Texinfo: one chunk per ``@node``.
- Markdown: one chunk per heading (not counting ``#`` lines in code fences).
- Anything else, or Python that does not parse: overlapping line windows.

Any section longer than MAX_LINES is cut into overlapping windows that keep
its symbol. Line numbers are 1-based and inclusive; leading and trailing
blank lines are trimmed and blank chunks dropped.
"""

import ast
import re
from dataclasses import dataclass

# Longest chunk, in lines. Retrieved context gets ~2,000 tokens for ~5
# chunks, so a chunk should stay around 400 tokens.
MAX_LINES = 60
# Window size for text without structure, and the overlap between windows.
TEXT_WINDOW = 40
OVERLAP = 8

_MD_HEADING = re.compile(r"^#{1,6}\s+(.*?)\s*#*\s*$")
_MD_FENCE = re.compile(r"^\s*(```|~~~)")


@dataclass(frozen=True)
class Chunk:
    path: str
    start_line: int
    end_line: int
    symbol: str | None
    kind: str
    text: str


# A section before trimming and windowing: (start, end, symbol), 1-based.
_Section = tuple[int, int, str | None]


def chunk_file(path: str, text: str) -> list[Chunk]:
    """Return the chunks of ``text``, the contents of the file at ``path``."""
    lines = text.splitlines()
    kind, sections = _sections(path, text, lines)
    chunks: list[Chunk] = []
    for start, end, symbol in sections:
        chunks.extend(_finish(path, lines, start, end, symbol, kind, MAX_LINES))
    return chunks


def _sections(path: str, text: str, lines: list[str]) -> tuple[str, list[_Section]]:
    if path.endswith(".py"):
        try:
            return "python", _python_sections(ast.parse(text), len(lines))
        except (SyntaxError, ValueError):
            pass
    elif path.endswith((".texi", ".texinfo", ".txi")):
        return "texinfo", _split_at(lines, _texinfo_node)
    elif path.endswith((".md", ".markdown")):
        return "markdown", _markdown_sections(lines)
    return "text", [(1, len(lines), None)]


def _finish(path, lines, start, end, symbol, kind, max_lines) -> list[Chunk]:
    """Trim blank edges of lines[start..end], then window it if too long."""
    while start <= end and not lines[start - 1].strip():
        start += 1
    while end >= start and not lines[end - 1].strip():
        end -= 1
    if start > end:
        return []
    window = max_lines if kind != "text" else TEXT_WINDOW
    if end - start + 1 <= window:
        return [Chunk(path, start, end, symbol, kind, "\n".join(lines[start - 1 : end]))]
    chunks = []
    step = max(1, window - OVERLAP)
    first = start
    while True:
        last = min(first + window - 1, end)
        chunks.append(Chunk(path, first, last, symbol, kind, "\n".join(lines[first - 1 : last])))
        if last == end:
            return chunks
        first += step


# --- Python -----------------------------------------------------------------

_DEFS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def _span(node: ast.AST) -> tuple[int, int]:
    """Lines of a definition, starting at its first decorator."""
    start = min([node.lineno, *(d.lineno for d in getattr(node, "decorator_list", []))])
    return start, node.end_lineno or node.lineno


def _python_sections(tree: ast.Module, n_lines: int) -> list[_Section]:
    sections: list[_Section] = []
    covered: list[tuple[int, int]] = []
    for node in tree.body:
        if not isinstance(node, _DEFS):
            continue
        start, end = _span(node)
        covered.append((start, end))
        if isinstance(node, ast.ClassDef) and end - start + 1 > MAX_LINES:
            sections.extend(_class_sections(node, start, end))
        else:
            sections.append((start, end, node.name))
    sections.extend((s, e, "<module>") for s, e in _gaps(covered, 1, n_lines))
    return sorted(sections)


def _class_sections(node: ast.ClassDef, start: int, end: int) -> list[_Section]:
    methods = [m for m in node.body if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))]
    spans = [_span(m) for m in methods]
    sections = [(s, e, f"{node.name}.{m.name}") for m, (s, e) in zip(methods, spans, strict=True)]
    # The class line, docstring and attributes outside the methods.
    sections.extend((s, e, node.name) for s, e in _gaps(spans, start, end))
    return sections


def _gaps(covered: list[tuple[int, int]], first: int, last: int) -> list[tuple[int, int]]:
    """Line ranges in first..last not inside any of the covered ranges."""
    gaps = []
    cursor = first
    for start, end in sorted(covered):
        if start > cursor:
            gaps.append((cursor, start - 1))
        cursor = max(cursor, end + 1)
    if cursor <= last:
        gaps.append((cursor, last))
    return gaps


# --- Texinfo and Markdown ---------------------------------------------------


def _texinfo_node(line: str) -> str | None:
    if line.startswith("@node "):
        return line[len("@node ") :].split(",")[0].strip()
    return None


def _split_at(lines: list[str], heading_of) -> list[_Section]:
    """Start a new section at every line for which heading_of returns a name."""
    sections: list[_Section] = []
    start, symbol = 1, None
    for number, line in enumerate(lines, 1):
        name = heading_of(line)
        if name is not None and number > start:
            sections.append((start, number - 1, symbol))
            start = number
        if name is not None:
            symbol = name
    sections.append((start, len(lines), symbol))
    return sections


def _markdown_sections(lines: list[str]) -> list[_Section]:
    in_fence = False

    def heading(line: str) -> str | None:
        nonlocal in_fence
        if _MD_FENCE.match(line):
            in_fence = not in_fence
            return None
        match = None if in_fence else _MD_HEADING.match(line)
        return match.group(1) if match else None

    return _split_at(lines, heading)
