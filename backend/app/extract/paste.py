"""Extraction from pasted text: Markdown with fenced code, or plain code. Never executed."""

import ast
import re
from collections.abc import Callable
from dataclasses import dataclass

from app.extract.blocks import (
    C_LIKE_COMMENTS_AND_STRINGS,
    LANGUAGE_KINDS,
    build_blocks,
    classify,
    detect_c_family,
    has_js_markers,
    has_jsx_tags,
    looks_pythonish,
    normalize_text,
    parse_python,
)
from app.extract.errors import ExtractionError
from app.extract.libs import library_fields
from app.extract.markdown_doc import parse_markdown
from app.models import Block, Extraction, SourceFormat, SourceKind, StepKind

PASTE_SOURCE = "Pasted code"
_DEFAULT_TITLE = "Pasted code"
_SHELL_PREFIXES = ("!", "%", "$ ", "pip install")
_NODE_SHELL_PREFIXES = ("$ ", "pip install", "npm ", "npx ", "yarn ", "pnpm ")
_TS_PARAMETER = r"(?:[^(){}\n]*,)?[ \t]*[\w$]+\??[ \t]*:[ \t]*[\w$]+\b"
_TS_MARKERS = re.compile(
    r"^[ \t]*(?:export[ \t]+)?(?:declare[ \t]+)?(?:"
    r"(?:interface|enum)[ \t]+[\w$]+[^\n{]*\{|type[ \t]+[\w$]+[^\n=]*=|"
    r"(?:const|let|var)[ \t]+[\w$]+[ \t]*:)"
    rf"|\bfunction[^\n(]*\({_TS_PARAMETER}"
    rf"|=[ \t]*(?:async[ \t]*)?\({_TS_PARAMETER}[^()\n]*\)\s*=>|\bas[ \t]+const\b",
    re.M,
)


@dataclass(frozen=True)
class PastedCode:
    """What the pipeline gets for a pasted check, in place of a URL or a notebook tuple."""

    code: str
    title: str | None = None
    language: str = "auto"
    """`python`, `javascript`, `typescript`, `c`, `cpp` or `auto`."""


def _is_shell_line(line: str) -> bool:
    return line.lstrip().startswith(_SHELL_PREFIXES)


def _is_node_shell_line(line: str) -> bool:
    return line.lstrip().startswith(_NODE_SHELL_PREFIXES)


def _runs(
    lines: list[str], is_shell_line: Callable[[str], bool] = _is_shell_line
) -> list[tuple[bool, list[str]]]:
    """Split lines into (is_shell, lines) runs. A shell line ending in a backslash continues."""
    runs: list[tuple[bool, list[str]]] = []
    continuing = False
    for line in lines:
        shell = continuing or is_shell_line(line)
        continuing = shell and line.rstrip().endswith("\\")
        if runs and runs[-1][0] == shell:
            runs[-1][1].append(line)
        else:
            runs.append((shell, [line]))
    return runs


def _split_python(lines: list[str], tree: ast.Module) -> list[str]:
    """Step texts: consecutive top-level statements, split where a blank line separates two."""
    parts: list[list[str]] = []
    start = 0
    previous_end = 0
    for position, node in enumerate(tree.body):
        first = min([node.lineno, *(d.lineno for d in getattr(node, "decorator_list", []))])
        if position:
            gap = lines[previous_end : first - 1]
            blanks = [i for i, line in enumerate(gap) if not line.strip()]
            if blanks:
                cut = previous_end + blanks[-1] + 1
                parts.append(lines[start:cut])
                start = cut
        previous_end = node.end_lineno or node.lineno
    parts.append(lines[start:])
    return ["\n".join(part) for part in parts]


def _python_block(text: str) -> Block | None:
    text = normalize_text(text)
    if not text.strip():
        return None
    return Block(idx=0, kind=StepKind.PYTHON, code=text, runnable_code=text)


def _plain_blocks(text: str) -> list[Block]:
    lines = text.split("\n")
    pieces: list[Block | None] = []
    for is_shell, run in _runs(lines):
        body = "\n".join(run)
        if is_shell:
            code = normalize_text(body)
            only_percent = all(
                line.lstrip().startswith("%") for line in code.split("\n") if line.strip()
            )
            pieces.append(
                Block(idx=0, kind=StepKind.OTHER if only_percent else StepKind.SHELL, code=code)
            )
            continue
        if not body.strip():
            continue
        tree = parse_python(body)
        if tree is None:
            whole = _python_block(text)
            return [whole] if whole else []
        pieces.extend(_python_block(part) for part in _split_python(run, tree))
    return [piece for piece in pieces if piece is not None]


def _code_chunks(lines: list[str]) -> list[str]:
    """Split C-like code at blank lines that sit at brace, bracket and paren depth 0.

    Quotes, template strings and comments are skipped so a stray brace in them does not count.
    """
    chunks: list[list[str]] = [[]]
    depth = 0
    in_block_comment = False
    in_template = False
    for line in lines:
        if not line.strip() and depth == 0 and not in_block_comment and not in_template:
            if chunks[-1]:
                chunks.append([])
            continue
        chunks[-1].append(line)
        quote = ""
        i = 0
        while i < len(line):
            char = line[i]
            pair = line[i : i + 2]
            if in_block_comment:
                if pair == "*/":
                    in_block_comment = False
                    i += 1
            elif in_template:
                if char == "\\":
                    i += 1
                elif char == "`":
                    in_template = False
            elif quote:
                if char == "\\":
                    i += 1
                elif char == quote:
                    quote = ""
            elif pair == "//":
                break
            elif pair == "/*":
                in_block_comment = True
                i += 1
            elif char in "\"'":
                quote = char
            elif char == "`":
                in_template = True
            elif char in "{[(":
                depth += 1
            elif char in "}])":
                depth = max(0, depth - 1)
            i += 1
    return ["\n".join(chunk) for chunk in chunks if chunk]


def _language_blocks(text: str, kind: StepKind) -> list[Block]:
    """Non-Python plain text: shell lines are shell steps, the rest is code in blank-line chunks."""
    pieces: list[Block] = []
    for is_shell, run in _runs(text.split("\n"), _is_node_shell_line):
        if is_shell:
            code = normalize_text("\n".join(run))
            if code.strip():
                pieces.append(Block(idx=0, kind=StepKind.SHELL, code=code))
            continue
        for chunk in _code_chunks(run):
            code = normalize_text(chunk)
            if code.strip():
                pieces.append(classify(code, kind.value))
    return pieces


def _resolve_language(text: str, language: str) -> StepKind | None:
    """Choose a supported language from an explicit selection or signs in a plain paste."""
    name = language.strip().lower()
    if name in ("python", "py"):
        return StepKind.PYTHON
    if name in LANGUAGE_KINDS:
        return LANGUAGE_KINDS[name]
    python_lines = "\n".join(
        line for is_shell, run in _runs(text.split("\n")) if not is_shell for line in run
    )
    if parse_python(python_lines) is not None:
        return StepKind.PYTHON
    c_family = detect_c_family(text)
    if c_family is not None:
        return c_family
    unquoted = C_LIKE_COMMENTS_AND_STRINGS.sub(
        lambda match: " " + "\n" * match[0].count("\n"), text
    )
    if _TS_MARKERS.search(unquoted):
        return StepKind.TYPESCRIPT
    if has_js_markers(text) or has_jsx_tags(text):
        return StepKind.JAVASCRIPT
    return StepKind.PYTHON if looks_pythonish(python_lines) else None


def extract_paste(code: str, title: str | None, language: str = "auto") -> Extraction:
    """Pasted text. With Markdown code fences each fenced block is a block and the rest is
    ignored; otherwise the text is code, split into steps at blank lines between statements
    (for Python) or at blank lines outside any braces (for the other languages)."""
    text = code.replace("\r\n", "\n").replace("\r", "\n")
    _, raws = parse_markdown(text)
    if raws:
        blocks = build_blocks(raws)
        for block in blocks:
            block.context = ""
    else:
        kind = _resolve_language(text, language)
        if kind is None:
            raise ExtractionError("No Python, JavaScript, TypeScript, C or C++ code found.")
        blocks = _plain_blocks(text) if kind == StepKind.PYTHON else _language_blocks(text, kind)
        for index, block in enumerate(blocks):
            block.idx = index
    return Extraction(
        source_kind=SourceKind.PASTE,
        source_format=SourceFormat.PASTE,
        source=PASTE_SOURCE,
        title=(title or "").strip() or _DEFAULT_TITLE,
        blocks=blocks,
        **library_fields(blocks),
    )
