"""Library detection: imports and pip install lines to a sorted list of PyPI names.

The extractor is offline, so it cannot tell an author's local module from a PyPI package.
Every non-standard-library import is listed; the sandbox reports the names pip rejects.
"""

import ast
import re
import shlex

from packaging.requirements import InvalidRequirement, Requirement

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
    return [
        name for token in _requirements_from_pip_line(line) if (name := _requirement_name(token))
    ]


def pins_from_pip_line(line: str) -> list[tuple[str, str]]:
    """Canonical package names and original specifiers, without evaluating markers."""
    pins: list[tuple[str, str]] = []
    for token in _requirements_from_pip_line(line):
        head = token.split(";", 1)[0].strip()
        try:
            requirement = Requirement(head)
        except InvalidRequirement:
            continue
        if not requirement.specifier or not (name := _requirement_name(token)):
            continue
        suffix = head[len(requirement.name) :].strip()
        suffix = re.sub(r"^\[[^\]]*\]", "", suffix).strip()
        pins.append((name, suffix))
    return pins


def _requirements_from_pip_line(line: str) -> list[str]:
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
            names.extend(_requirements_from_command(segment))
            segment = []
        else:
            segment.append(token)
    return names


def _requirements_from_command(tokens: list[str]) -> list[str]:
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
            names.append(token)
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


# JavaScript and TypeScript: npm packages

NODE_BUILTINS = frozenset(
    "assert async_hooks buffer child_process cluster console constants crypto dgram "
    "diagnostics_channel dns domain events fs http http2 https inspector module net os path "
    "perf_hooks process punycode querystring readline repl stream string_decoder sys test "
    "timers tls trace_events tty url util v8 vm wasi worker_threads zlib".split()
)
"""Node core module names. `fs/promises` and `node:fs` map to `fs` and are skipped too."""

_NPM_NAME = re.compile(r"(?:@[A-Za-z0-9~._-]+/)?[A-Za-z0-9~._-]+")
_JS_SPECIFIERS = (
    re.compile(r"""\brequire\s*\(\s*['"]([^'"\n]+)['"]\s*\)"""),
    re.compile(r"""\bimport\b[^;'"`]*?\bfrom\s*['"]([^'"\n]+)['"]"""),
    re.compile(r"""\bexport\b[^;'"`]*?\bfrom\s*['"]([^'"\n]+)['"]"""),
    re.compile(r"""\bimport\s*['"]([^'"\n]+)['"]"""),
    re.compile(r"""\bimport\s*\(\s*['"]([^'"\n]+)['"]\s*\)"""),
)
_NPM_COMMANDS = {"npm": {"install", "i", "add"}, "yarn": {"add"}, "pnpm": {"add", "install", "i"}}
_NPM_FLAGS_WITH_VALUE = {
    "--prefix", "--registry", "--workspace", "-w", "--cache", "--loglevel", "--cwd",
    "--filter", "-C", "--userconfig", "--tag",
}  # fmt: skip
_NPM_VERSION = re.compile(r"^(?:[\^~<>=]+\s*)?v?\d")


def npm_package_name(specifier: str) -> str | None:
    """The npm package a module specifier names, or None for paths and Node built-ins."""
    spec = specifier.strip()
    if not spec or spec.startswith((".", "/", "~")) or ":" in spec or "\\" in spec:
        return None
    parts = spec.split("/")
    name = "/".join(parts[:2]) if spec.startswith("@") else parts[0]
    if not _NPM_NAME.fullmatch(name) or name in NODE_BUILTINS:
        return None
    return name


def npm_packages_from_code(code: str) -> set[str]:
    """npm names from `require`, `import ... from`, `import 'x'` and `import('x')`."""
    names: set[str] = set()
    for pattern in _JS_SPECIFIERS:
        for match in pattern.finditer(code):
            name = npm_package_name(match.group(1))
            if name:
                names.add(name)
    return names


def _split_npm_spec(token: str) -> tuple[str, str] | None:
    """(name, version) from `name`, `name@1.2.3` or `@scope/name@^1`; None for paths and URLs."""
    if token.startswith(("-", ".", "/", "~", "$", "{")) or ":" in token or "\\" in token:
        return None
    at = token.find("@", 1)
    name, version = (token, "") if at < 0 else (token[:at], token[at + 1 :])
    if not _NPM_NAME.fullmatch(name) or ("/" in name and not name.startswith("@")):
        return None
    return name.lower(), version


def _npm_install_tokens(tokens: list[str]) -> list[str]:
    for i, token in enumerate(tokens[:-1]):
        if tokens[i + 1] in _NPM_COMMANDS.get(token, ()):
            return tokens[i + 2 :]
    return []


def _npm_specs_from_line(line: str) -> list[tuple[str, str]]:
    line = line.strip().lstrip("$").strip()
    if not line or line.startswith("#"):
        return []
    try:
        tokens = shlex.split(line, comments=True)
    except ValueError:
        tokens = [token.strip("'\"") for token in line.split()]
    specs: list[tuple[str, str]] = []
    segment: list[str] = []
    for token in [*tokens, ";"]:
        if token not in _COMMAND_BREAKS:
            segment.append(token)
            continue
        skip_next = False
        for argument in _npm_install_tokens(segment):
            if skip_next:
                skip_next = False
            elif argument.startswith("-"):
                skip_next = argument in _NPM_FLAGS_WITH_VALUE
            elif parsed := _split_npm_spec(argument):
                specs.append(parsed)
        segment = []
    return specs


def npm_packages_from_shell(text: str) -> set[str]:
    """npm names from `npm install`, `npm i`, `yarn add` and `pnpm add` lines."""
    names: set[str] = set()
    for line in re.sub(r"\[ \t]*\n", " ", text).split("\n"):
        names.update(name for name, _ in _npm_specs_from_line(line))
    return names


def npm_pins_from_line(line: str) -> list[tuple[str, str]]:
    """(name, version) for each `name@version` argument. Tags such as `@latest` are not pins."""
    return [
        (name, version)
        for name, version in _npm_specs_from_line(line)
        if _NPM_VERSION.match(version)
    ]


def detect_npm_packages(blocks: list[Block]) -> list[str]:
    """Sorted npm names used by JavaScript and TypeScript blocks plus npm install lines."""
    names: set[str] = set()
    for block in blocks:
        if block.kind in (StepKind.JAVASCRIPT, StepKind.TYPESCRIPT) and block.runnable_code:
            names |= npm_packages_from_code(block.runnable_code)
        elif block.kind is StepKind.SHELL:
            names |= npm_packages_from_shell(block.code)
    return sorted(names)


# C and C++: system headers

C_STANDARD_HEADERS = frozenset(
    "assert.h complex.h ctype.h errno.h fenv.h float.h inttypes.h iso646.h limits.h locale.h "
    "math.h setjmp.h signal.h stdalign.h stdarg.h stdatomic.h stdbit.h stdbool.h stdckdint.h "
    "stddef.h stdint.h stdio.h stdlib.h stdnoreturn.h string.h tgmath.h threads.h time.h "
    "uchar.h wchar.h wctype.h".split()
)
CPP_STANDARD_HEADERS = frozenset(
    "algorithm any array atomic barrier bit bitset charconv chrono codecvt compare complex "
    "concepts condition_variable coroutine deque exception execution expected filesystem "
    "flat_map flat_set format forward_list fstream functional future generator "
    "initializer_list iomanip ios iosfwd iostream istream iterator latch limits list locale "
    "map mdspan memory memory_resource mutex new numbers numeric optional ostream print queue "
    "random ranges ratio regex scoped_allocator semaphore set shared_mutex source_location "
    "span spanstream sstream stack stacktrace stdexcept stop_token streambuf string "
    "string_view strstream syncstream system_error thread tuple type_traits typeindex "
    "typeinfo unordered_map unordered_set utility valarray variant vector version "
    "cassert cctype cerrno cfenv cfloat cinttypes ciso646 climits clocale cmath csetjmp "
    "csignal cstdalign cstdarg cstdbool cstddef cstdint cstdio cstdlib cstring ctgmath ctime "
    "cuchar cwchar cwctype ccomplex".split()
)
_SYSTEM_INCLUDE = re.compile(r"^[ \t]*#[ \t]*include[ \t]*<([^>\n]+)>", re.M)


def detect_c_system_headers(blocks: list[Block]) -> list[str]:
    """Sorted `<header>` names in C and C++ blocks that the language standard does not provide."""
    names: set[str] = set()
    for block in blocks:
        if block.kind not in (StepKind.C, StepKind.CPP) or not block.runnable_code:
            continue
        for match in _SYSTEM_INCLUDE.finditer(block.runnable_code):
            header = match.group(1).strip()
            if header not in C_STANDARD_HEADERS and header not in CPP_STANDARD_HEADERS:
                names.add(header)
    return sorted(names)


def library_fields(blocks: list[Block]) -> dict[str, list[str]]:
    """The `Extraction` fields that come from the blocks' libraries, for `Extraction(**...)`."""
    return {
        "packages": detect_packages(blocks),
        "npm_packages": detect_npm_packages(blocks),
        "c_system_headers": detect_c_system_headers(blocks),
    }
