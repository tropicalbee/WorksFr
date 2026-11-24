"""Extraction from pasted text: Markdown with fenced code, or plain code. Never executed."""

import ast
from dataclasses import dataclass

from app.extract.blocks import build_blocks, normalize_text, parse_python
from app.extract.libs import detect_packages
from app.extract.markdown_doc import parse_markdown
from app.models import Block, Extraction, SourceFormat, SourceKind, StepKind

PASTE_SOURCE = "Pasted code"
_DEFAULT_TITLE = "Pasted code"
_SHELL_PREFIXES = ("!", "%", "$ ", "pip install")


@dataclass(frozen=True)
class PastedCode:
    """What the pipeline gets for a pasted check, in place of a URL or a notebook tuple."""

    code: str
    title: str | None = None


def _is_shell_line(line: str) -> bool:
    return line.lstrip().startswith(_SHELL_PREFIXES)


def _runs(lines: list[str]) -> list[tuple[bool, list[str]]]:
    """Split lines into (is_shell, lines) runs. A shell line ending in a backslash continues."""
    runs: list[tuple[bool, list[str]]] = []
    continuing = False
    for line in lines:
        shell = continuing or _is_shell_line(line)
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


def extract_paste(code: str, title: str | None) -> Extraction:
    """Pasted text. With Markdown code fences each fenced block is a block and the rest is
    ignored; otherwise the text is code, split into steps at blank lines between statements."""
    text = code.replace("\r\n", "\n").replace("\r", "\n")
    _, raws = parse_markdown(text)
    if raws:
        blocks = build_blocks(raws)
        for block in blocks:
            block.context = ""
    else:
        blocks = _plain_blocks(text)
        for index, block in enumerate(blocks):
            block.idx = index
    return Extraction(
        source_kind=SourceKind.PASTE,
        source_format=SourceFormat.PASTE,
        source=PASTE_SOURCE,
        title=(title or "").strip() or _DEFAULT_TITLE,
        blocks=blocks,
        packages=detect_packages(blocks),
    )
