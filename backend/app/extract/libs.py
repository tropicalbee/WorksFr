"""Library detection: imports and pip install lines to a sorted list of PyPI names.

The extractor is offline, so it cannot tell an author's local module from a PyPI package.
Every non-standard-library import is listed; the sandbox reports the names pip rejects.
"""

import ast
import re
import shlex

from app.models import Block, StepKind
from app.packages import IMPORT_TO_PYPI as IMPORT_TO_PYPI
from app.packages import dist_for_import as pypi_name
from app.packages import is_stdlib
from app.packages import normalize as canonical_name

_IMPORT_LINE = re.compile(
    r"^\s*(?:from\s+([A-Za-z_][\w.]*)\s+import\s|import\s+([A-Za-z_][\w., ]*))", re.M
)

_PIP_FLAGS_WITH_VALUE = {
    "-r", "--requirement", "-c", "--constraint", "-e", "--editable", "-i", "--index-url",
    "--extra-index-url", "-f", "--find-links", "-t", "--target", "--prefix", "--root",
    "--platform", "--python-version", "--implementation", "--abi", "--trusted-host",
    "--cache-dir", "--src", "--upgrade-strategy", "--progress-bar", "--only-binary",
    "--no-binary", "--global-option", "--config-settings", "-C", "--proxy", "--log",
    "--python", "--root-user-action", "--report", "--use-feature", "--use-deprecated",
    "--exists-action", "--retries", "--timeout", "--cert", "--client-cert", "-b", "--build",
}  # fmt: skip
_COMMAND_BREAKS = {"&&", "||", ";", "|", "&"}
_PIP_NAME = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)(?=$|[\[=<>~!;@ ])")
_PIP_EXE = re.compile(r"pip(\d+(\.\d+)*)?(\.exe)?")


def imported_modules(code: str) -> set[str]:
    """Dotted names of absolute imports (the full module path for `from x.y import z`)."""
    try:
        tree = ast.parse(code)
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return _imports_by_regex(code)
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.add(node.module)
    return found


def _imports_by_regex(code: str) -> set[str]:
    """Fallback for code that does not parse (Python 2, pasted output): scan import lines."""
    found: set[str] = set()
    for match in _IMPORT_LINE.finditer(code):
        if match.group(1):
            found.add(match.group(1))
        else:
            for part in match.group(2).split(","):
                name = part.split(" as ")[0].strip()
                if name:
                    found.add(name)
    return found


def packages_from_imports(code: str) -> set[str]:
    """PyPI names for every non-standard-library import in `code`."""
    names: set[str] = set()
    for module in imported_modules(code):
        top = module.split(".")[0]
        if is_stdlib(top):
            continue
        names.add(pypi_name(module))
    return names


def packages_from_pip_line(line: str) -> list[str]:
    """Package names from the `pip install` commands on one logical shell line.

    Version pins, extras, markers, flags, requirement files, paths and URLs are dropped.
    """
    line = line.strip().lstrip("!%$").strip()
    if not line or line.startswith("#"):
        return []
    try:
        tokens = shlex.split(line, comments=True)
    except ValueError:
        tokens = [token.strip("'\"") for token in line.split()]
    names: list[str] = []
    segment: list[str] = []
    for token in [*tokens, ";"]:
        if token in _COMMAND_BREAKS:
            names.extend(_packages_from_command(segment))
            segment = []
        else:
            segment.append(token)
    return names


def _packages_from_command(tokens: list[str]) -> list[str]:
    start = _install_index(tokens)
    if start is None:
        return []
    names: list[str] = []
    skip_next = False
    for token in tokens[start:]:
        if skip_next:
            skip_next = False
            continue
        if token.startswith("-"):
            skip_next = token in _PIP_FLAGS_WITH_VALUE
            continue
        name = _requirement_name(token)
        if name:
            names.append(name)
    return names


def _install_index(tokens: list[str]) -> int | None:
    """Index of the first token after `pip install` (also `python -m pip install`)."""
    for i, token in enumerate(tokens[:-1]):
        base = token.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
        if _PIP_EXE.fullmatch(base) and tokens[i + 1] == "install":
            return i + 2
    return None


def _requirement_name(token: str) -> str | None:
    head = token.split(";")[0].split("@")[0]
    if (
        token.startswith((".", "/", "~", "<", "$", "{"))
        or "://" in token
        or token.startswith(("git+", "hg+", "svn+", "bzr+"))
        or token.lower().endswith((".whl", ".zip", ".tar.gz", ".tgz", ".txt", ".cfg", ".toml"))
        or "\\" in head
        or "/" in head
    ):
        return None
    match = _PIP_NAME.match(token)
    return canonical_name(match.group(1)) if match else None


def packages_from_shell(text: str) -> set[str]:
    """Package names from every pip install command in a shell block or `!pip` lines."""
    names: set[str] = set()
    for line in re.sub(r"\\[ \t]*\n", " ", text).split("\n"):
        names.update(packages_from_pip_line(line))
    return names


def detect_packages(blocks: list[Block]) -> list[str]:
    """Sorted PyPI names used by the blocks: imports of runnable code plus pip install lines."""
    names: set[str] = set()
    for block in blocks:
        if block.kind in (StepKind.PYTHON, StepKind.REPL) and block.runnable_code:
            names |= packages_from_imports(block.runnable_code)
        if block.kind is StepKind.SHELL:
            names |= packages_from_shell(block.code)
        elif block.kind is StepKind.PYTHON:
            bang = "\n".join(
                line for line in block.code.split("\n") if line.lstrip().startswith(("!", "%"))
            )
            if bang:
                names |= packages_from_shell(bang)
    return sorted(names)
