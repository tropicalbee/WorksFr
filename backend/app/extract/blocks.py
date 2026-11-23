"""Turn raw code blocks (text plus markup hints) into typed Blocks.

Kinds follow the table in docs/ARCHITECTURE.md. Python versus output is decided by
`ast.parse` plus whatever the page markup says (language classes, `<em>` output spans),
never by per-page rules. Tutorial code is only parsed here, never executed.
"""

import ast
import re
import textwrap
from dataclasses import dataclass

from app.models import Block, StepKind

PYTHON_HINTS = {"python", "python3", "py", "py3", "python2", "ipython", "jupyter", "pyi"}
REPL_HINTS = {"pycon", "python-repl", "pycon3", "repl", "doctest"}
SHELL_HINTS = {
    "bash", "sh", "shell", "zsh", "console", "terminal", "shell-session", "shellsession",
    "sh-session", "powershell", "ps1", "pwsh", "cmd", "bat", "batch", "fish", "command",
}  # fmt: skip
OUTPUT_HINTS = {"output", "stdout", "result", "results", "traceback", "pytb", "py3tb"}
OTHER_HINTS = {
    "r", "java", "c", "cpp", "c++", "csharp", "cs", "go", "rust", "ruby", "php", "js",
    "javascript", "typescript", "ts", "jsx", "tsx", "json", "jsonc", "yaml", "yml", "toml",
    "ini", "xml", "html", "css", "scss", "sql", "dockerfile", "makefile", "markdown", "md",
    "latex", "tex", "diff", "kotlin", "swift", "scala", "perl", "lua", "matlab", "julia",
    "vue", "graphql", "proto", "csv", "nginx", "apache", "properties",
}  # fmt: skip

_SHELL_COMMANDS = (
    "pip|pip3|python|python3|py|conda|mamba|micromamba|npm|npx|yarn|pnpm|cd|git|source|export|echo|"
    "mkdir|apt|apt-get|brew|curl|wget|docker|docker-compose|jupyter|virtualenv|uv|poetry|pipx|"
    "sudo|chmod|ls|touch|ollama|ssh|unzip|tar|kubectl|make|cmake|nvm|node|streamlit|uvicorn"
)
_SHELL_LINE = re.compile(rf"^(?:\$\s+)?(?:{_SHELL_COMMANDS})(?:\s+[^=(\s]|$)")
_PYTHONISH = re.compile(
    r"^\s*(?:import\s+\w|from\s+[\w.]+\s+import\s|def\s+\w|class\s+\w|print\s*\(|@\w|"
    r"(?:for|while|if|with)\s.+:\s*$|try:\s*$)",
    re.M,
)
_JSON_NAMES = {"true", "false", "null", "nan", "inf", "infinity"}
_MAX_TAIL_SCAN_LINES = 300


@dataclass
class RawBlock:
    """A code block as found in a page, before classification."""

    text: str
    hint: str | None = None
    """Lower-case language name from the markup (class names, fence info string)."""
    without_output: str | None = None
    """The text with output spans (`<em>` in a code block) removed, when the page marks them."""
    context: str = ""


def normalize_text(text: str) -> str:
    """Unify newlines, drop invisible characters, dedent, trim blank edges."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\xa0", " ").replace("​", "").replace("﻿", "")
    text = textwrap.dedent(text)
    return text.strip("\n").rstrip()


def first_line(text: str) -> str:
    """First non-empty line, used for matching blocks in tests and logs."""
    return next((line for line in text.split("\n") if line.strip()), "")


def parse_python(code: str) -> ast.Module | None:
    try:
        return ast.parse(code)
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return None


def _is_display_node(node: ast.expr) -> bool:
    """True for literals and containers of literals, as printed output looks."""
    if isinstance(node, ast.Constant):
        return True
    if isinstance(node, ast.Name):
        return node.id.lower() in _JSON_NAMES
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub | ast.UAdd):
        return _is_display_node(node.operand)
    if isinstance(node, ast.Dict):
        return all(
            (k is None or _is_display_node(k)) and _is_display_node(v)
            for k, v in zip(node.keys, node.values, strict=True)
        )
    if isinstance(node, ast.List | ast.Tuple | ast.Set):
        return all(_is_display_node(e) for e in node.elts)
    return False


def is_display_only(tree: ast.Module) -> bool:
    """A module that only evaluates literals does nothing a step could verify: it is output."""
    return bool(tree.body) and all(
        isinstance(stmt, ast.Expr) and _is_display_node(stmt.value) for stmt in tree.body
    )


def looks_like_repl(text: str) -> bool:
    return first_line(text).startswith(">>>")


def strip_repl(text: str) -> str:
    """Keep the code after `>>> ` and `... ` prompts; drop output lines."""
    code: list[str] = []
    for line in text.split("\n"):
        if line.startswith(">>> ") or line.startswith("... "):
            code.append(line[4:])
        elif line in (">>>", "..."):
            code.append("")
    return normalize_text("\n".join(code))


def looks_like_shell(text: str) -> bool:
    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped or (stripped.startswith("#") and not stripped.startswith("#!")):
            continue
        return stripped.startswith("#!") or bool(_SHELL_LINE.match(stripped))
    return False


def _code_prefix(text: str) -> str | None:
    """Longest leading run of lines that parses as Python while the tail does not.

    Handles code followed by pasted output when the page has no markup for it.
    """
    lines = text.split("\n")
    if len(lines) > _MAX_TAIL_SCAN_LINES:
        return None
    for end in range(len(lines) - 1, 0, -1):
        prefix = "\n".join(lines[:end]).rstrip()
        tree = parse_python(prefix) if prefix else None
        if tree is not None and not is_display_only(tree):
            return prefix
    return None


def _classify_python_text(text: str, without_output: str | None) -> tuple[StepKind, str | None]:
    if without_output and without_output != text and parse_python(without_output) is not None:
        return StepKind.PYTHON, without_output
    tree = parse_python(text)
    if tree is not None:
        if is_display_only(tree):
            return StepKind.OUTPUT, None
        return StepKind.PYTHON, text
    if text.startswith("Traceback (most recent call last)"):
        return StepKind.OUTPUT, None
    prefix = _code_prefix(text)
    if prefix is not None:
        return StepKind.PYTHON, prefix
    if _PYTHONISH.search(text):
        # Python-looking but broken (Python 2, typos): let the sandbox report the error.
        return StepKind.PYTHON, text
    return StepKind.OUTPUT, None


def classify(text: str, hint: str | None, without_output: str | None = None) -> Block:
    """Classify normalized text. Returns a Block with idx 0; callers renumber."""
    kind, runnable = _classify(text, hint, without_output)
    return Block(idx=0, kind=kind, code=text, runnable_code=runnable)


def _classify(
    text: str, hint: str | None, without_output: str | None
) -> tuple[StepKind, str | None]:
    if hint in OTHER_HINTS:
        return StepKind.OTHER, None
    if hint in SHELL_HINTS:
        return StepKind.SHELL, None
    if hint in OUTPUT_HINTS:
        return StepKind.OUTPUT, None
    if (hint in REPL_HINTS and ">>>" in text) or looks_like_repl(text):
        runnable = strip_repl(text)
        return (StepKind.REPL, runnable) if runnable else (StepKind.OUTPUT, None)
    if hint in PYTHON_HINTS or hint in REPL_HINTS:
        if without_output and parse_python(without_output) is not None:
            return StepKind.PYTHON, without_output
        if parse_python(text) is None and looks_like_shell(text):
            return StepKind.SHELL, None
        return StepKind.PYTHON, text
    if looks_like_shell(text):
        return StepKind.SHELL, None
    return _classify_python_text(text, without_output)


def build_blocks(raws: list[RawBlock]) -> list[Block]:
    """Classify raw blocks in page order; blocks with no text are dropped."""
    blocks: list[Block] = []
    for raw in raws:
        text = normalize_text(raw.text)
        if not text.strip():
            continue
        without_output = normalize_text(raw.without_output) if raw.without_output else None
        block = classify(text, raw.hint, without_output)
        block.idx = len(blocks)
        block.context = raw.context
        blocks.append(block)
    return blocks


# Notebook cells


def split_notebook_lines(code: str) -> tuple[str, list[str]]:
    """Split a code cell into (python lines, `!`/`%` lines).

    A `!` line ending in a backslash continues onto the next line, which goes with it.
    """
    kept: list[str] = []
    magics: list[str] = []
    continuing = False
    for line in code.split("\n"):
        if continuing:
            magics.append(line)
            continuing = line.rstrip().endswith("\\")
        elif line.lstrip().startswith(("!", "%")):
            magics.append(line)
            continuing = line.rstrip().endswith("\\")
        else:
            kept.append(line)
    return "\n".join(kept), magics


_CELL_MAGIC = re.compile(r"^%%(\w+)")
_SHELL_CELL_MAGICS = {"bash", "sh", "script", "shell"}
_WRAPPER_CELL_MAGICS = {"time", "timeit", "capture", "prun", "python", "python3", "debug"}


def classify_notebook_cell(code: str) -> Block | None:
    """A notebook code cell. Empty cells give None.

    Cells are python, with their `!` and `%` lines removed from `runnable_code` (comments
    left behind still count as code). When nothing is left, a cell of `!` lines is shell and
    one with `%` lines is other. `%%bash`-style cell magics mark the whole cell shell or
    other, except wrappers such as `%%time` whose first line is simply dropped.
    """
    text = normalize_text(code)
    if not text.strip():
        return None
    cell_magic = _CELL_MAGIC.match(text)
    if cell_magic and cell_magic.group(1).lower() not in _WRAPPER_CELL_MAGICS:
        shell = cell_magic.group(1).lower() in _SHELL_CELL_MAGICS
        return Block(idx=0, kind=StepKind.SHELL if shell else StepKind.OTHER, code=text)
    kept, magics = split_notebook_lines(text)
    runnable = normalize_text(kept)
    if runnable.strip():
        return Block(idx=0, kind=StepKind.PYTHON, code=text, runnable_code=runnable)
    only_bang = all(not m.lstrip().startswith("%") for m in magics)
    return Block(idx=0, kind=StepKind.SHELL if only_bang else StepKind.OTHER, code=text)
