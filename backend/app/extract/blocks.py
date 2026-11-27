"""Turn raw code blocks (text plus markup hints) into typed Blocks.

Kinds follow the table in docs/ARCHITECTURE.md. Python versus output is decided by
`ast.parse` plus whatever the page markup says (language classes, `<em>` output spans),
never by per-page rules. Tutorial code is only parsed here, never executed.
"""

import ast
import re
import textwrap
from dataclasses import dataclass
from itertools import pairwise

from app.models import Block, StepKind

LANGUAGE_KINDS: dict[str, StepKind] = {
    "js": StepKind.JAVASCRIPT,
    "javascript": StepKind.JAVASCRIPT,
    "jsx": StepKind.JAVASCRIPT,
    "mjs": StepKind.JAVASCRIPT,
    "cjs": StepKind.JAVASCRIPT,
    "node": StepKind.JAVASCRIPT,
    "ts": StepKind.TYPESCRIPT,
    "typescript": StepKind.TYPESCRIPT,
    "tsx": StepKind.TYPESCRIPT,
    "c": StepKind.C,
    "h": StepKind.C,
    "cpp": StepKind.CPP,
    "c++": StepKind.CPP,
    "cc": StepKind.CPP,
    "cxx": StepKind.CPP,
    "hpp": StepKind.CPP,
}
"""Fence, HTML class and notebook kernel names for the non-Python languages that run.
One table for every extractor. Python and shell names stay in the hint sets below."""

PYTHON_HINTS = {"python", "python3", "py", "py3", "python2", "ipython", "jupyter", "pyi"}
JSX_HINTS = {"jsx", "tsx"}
REPL_HINTS = {"pycon", "python-repl", "pycon3", "repl", "doctest"}
SHELL_HINTS = {
    "bash", "sh", "shell", "zsh", "console", "terminal", "shell-session", "shellsession",
    "sh-session", "powershell", "ps1", "pwsh", "cmd", "bat", "batch", "fish", "command",
}  # fmt: skip
OUTPUT_HINTS = {"output", "stdout", "result", "results", "traceback", "pytb", "py3tb"}
OTHER_HINTS = {
    "r", "java", "csharp", "cs", "go", "rust", "ruby", "php", "json", "jsonc", "yaml", "yml",
    "toml",
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
    r"^[ \t]*(?:import\s+\w[^;\n]*$|from\s+[\w.]+\s+import\s|def\s+\w|"
    r"class\s+\w[^\n{]*$|print(?:\s*\(|[ \t]+[^=\s])|@\w|"
    r"(?:for|while|if|with)\s.+:\s*$|try:\s*$)",
    re.M,
)
_C_INCLUDE = re.compile(r"^[ \t]*#[ \t]*include[ \t]*<", re.M)
_CPP_MARKERS = re.compile(
    r"\bstd::|\bcout\b|\bcin\b|\bclass\s+\w|\bnamespace\b|\btemplate\s*<"
    r"|^[ \t]*#[ \t]*include[ \t]*<(?:iostream|vector|string|map|set|memory|algorithm"
    r"|functional|sstream|fstream|thread|mutex|chrono|cstdio|cstdlib|cstring)>",
    re.M,
)
_JS_MARKERS = re.compile(
    r"\brequire\s*\(|^[ \t]*import\b[^\n]*?\bfrom\s*['\"]|^[ \t]*import\s*['\"]"
    r"|\bconsole\.\w+\s*\(|\bmodule\.exports\b"
    r"|^[ \t]*export\s+(?:default|const|function|async)\b|\bfunction\s*\w*\s*\("
    r"|^(?:const|let|var)[ \t]+[\w$]+[ \t]*=",
    re.M,
)
_JS_BINDING = re.compile(r"\b(?:const|let)\b")
C_LIKE_COMMENTS_AND_STRINGS = re.compile(
    r"//[^\n]*|/\*[\s\S]*?\*/"
    r"""|"(?:\\[\s\S]|[^"\\\n])*"|'(?:\\[\s\S]|[^'\\\n])*'|`(?:\\[\s\S]|[^`\\])*`"""
)
_JSX_TAG_NAME = r"[A-Za-z_$][\w$.:-]*"
_JSX_TOKEN = re.compile(
    rf"{C_LIKE_COMMENTS_AND_STRINGS.pattern}|(?P<jsx>"
    rf"</(?:{_JSX_TAG_NAME})?\s*>|<{_JSX_TAG_NAME}(?:\s[^<]*?)?/\s*>)"
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


def has_js_markers(text: str) -> bool:
    text = textwrap.dedent(text)
    return bool(_JS_MARKERS.search(text)) or ("=>" in text and bool(_JS_BINDING.search(text)))


def has_jsx_tags(text: str) -> bool:
    """Identify closing or self-closing JSX syntax outside strings and comments."""
    return any(match.lastgroup == "jsx" for match in _JSX_TOKEN.finditer(text))


def looks_pythonish(text: str) -> bool:
    """Whether the text has lines only Python is likely to start with (def, class, print(...))."""
    return bool(_PYTHONISH.search(text))


def detect_c_family(text: str) -> StepKind | None:
    """C or C++ from an `#include <` line; C++ when it also uses `std::`, `cout` or a class."""
    if not _C_INCLUDE.search(text):
        return None
    return StepKind.CPP if _CPP_MARKERS.search(text) else StepKind.C


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


def _repl_output(text: str) -> str | None:
    lines = (
        line
        for line in text.split("\n")
        if line not in (">>>", "...") and not line.startswith((">>> ", "... "))
    )
    return normalize_text("\n".join(lines)) or None


def _mixed_repl(text: str) -> tuple[str, str] | None:
    lines = text.split("\n")
    for index, line in enumerate(lines):
        if not line.startswith((">>> ", "... ")):
            continue
        prefix = "\n".join(lines[:index]).rstrip()
        if not prefix or parse_python(prefix) is None:
            return None
        return prefix, "\n".join(lines[index:])
    return None


def _removed_output(text: str, runnable: str) -> str | None:
    # Removing a whole marked span can leave an empty placeholder line.
    remaining = iter(line for line in runnable.split("\n") if line.strip())
    expected = next(remaining, None)
    removed = []
    for line in text.split("\n"):
        if line == expected:
            expected = next(remaining, None)
        else:
            removed.append(line)
    return (normalize_text("\n".join(removed)) or None) if expected is None else None


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
    c_family = detect_c_family(text)
    if c_family is not None:
        return c_family, text
    if without_output and without_output != text and parse_python(without_output) is not None:
        return StepKind.PYTHON, without_output
    tree = parse_python(text)
    if tree is not None:
        if is_display_only(tree):
            return StepKind.OUTPUT, None
        return StepKind.PYTHON, text
    if text.startswith("Traceback (most recent call last)"):
        return StepKind.OUTPUT, None
    if has_js_markers(text) or has_jsx_tags(text):
        return StepKind.JAVASCRIPT, text
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
    shown = None
    if kind in (StepKind.PYTHON, StepKind.REPL, StepKind.OUTPUT) and hint not in OUTPUT_HINTS:
        if (mixed := _mixed_repl(text)) is not None:
            prefix, prompted = mixed
            kind = StepKind.REPL
            runnable = "\n".join((prefix, strip_repl(prompted)))
            shown = _repl_output(prompted)
        elif kind == StepKind.REPL:
            shown = _repl_output(text)
        elif kind == StepKind.PYTHON and runnable and runnable != text:
            if runnable == without_output:
                shown = _removed_output(text, runnable)
            else:
                shown = normalize_text(text[len(runnable) :]) or None
    if kind in (StepKind.JAVASCRIPT, StepKind.TYPESCRIPT) and (
        hint in JSX_HINTS or has_jsx_tags(text)
    ):
        # Keep a step for precheck and the report while withholding executable source.
        runnable = ""
    return Block(idx=0, kind=kind, code=text, runnable_code=runnable, shown_output=shown)


def _classify(
    text: str, hint: str | None, without_output: str | None
) -> tuple[StepKind, str | None]:
    if hint in LANGUAGE_KINDS:
        return LANGUAGE_KINDS[hint], text
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
    for step, following in pairwise(blocks):
        if (
            step.runnable_code
            and step.shown_output is None
            and following.kind == StepKind.OUTPUT
            and not following.code.startswith("Traceback (most recent call last)")
        ):
            step.shown_output = following.code
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


def classify_notebook_cell(code: str, language: StepKind = StepKind.PYTHON) -> Block | None:
    """A notebook code cell. Empty cells give None.

    Cells are python, with their `!` and `%` lines removed from `runnable_code` (comments
    left behind still count as code). When nothing is left, a cell of `!` lines is shell and
    one with `%` lines is other. `%%bash`-style cell magics mark the whole cell shell or
    other, except wrappers such as `%%time` whose first line is simply dropped.

    A cell of a non-Python kernel (`language`) is that language's code as written.
    """
    text = normalize_text(code)
    if not text.strip():
        return None
    if language in (StepKind.JAVASCRIPT, StepKind.TYPESCRIPT):
        return classify(text, language.value)
    if language != StepKind.PYTHON:
        return Block(idx=0, kind=language, code=text, runnable_code=text)
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
