"""Labeling rules: turn a sandbox result into Works, Deprecated, Broken or Can't verify.

The sandbox run decides (ADR-001). Error matching uses `error.type` (the bare class
name) and `error.message`. Messages match case-insensitively. Patterns are module-level
tuples so they are easy to extend.
"""

import ast
import re
from collections.abc import Collection, Mapping

from app.extract.libs import npm_package_name
from app.models import ErrorInfo, ExecResult, Label, LabelDecision, PatchVerdict, StepKind
from app.packages import dist_for_import, normalize

REASON_TIMEOUT = "too slow: the step timed out"
REASON_KEYBOARD = "needs keyboard input"
REASON_API_KEY = "needs an API key"
REASON_NETWORK = "needs network access"
REASON_GPU = "needs a GPU"
REASON_DATA_FILE = "needs a data file that is not on the page"
REASON_LOCAL_MODULE = "imports a local file from the author's machine"
REASON_BROWSER = "runs in a web browser"
CASCADE_PREFIX = "depends on step "

DEPRECATION_CATEGORIES: tuple[str, ...] = (
    "DeprecationWarning",
    "PendingDeprecationWarning",
    "FutureWarning",
)

STDIN_ERROR_TYPES: tuple[str, ...] = ("StdinUnavailableError",)

AUTH_ERROR_TYPES: tuple[str, ...] = (
    "AuthenticationError",
    "PermissionDeniedError",
    "PermissionDenied",
    "Unauthenticated",
    "Unauthorized",
)
AUTH_MESSAGE_PATTERNS: tuple[str, ...] = (
    r"incorrect api key",
    r"invalid api key",
    r"invalid[_ ]api[_ ]key",
    r"api key not valid",
    # A bare 401 also appears in index and value errors, so require HTTP context.
    r"error code: 401\b",
    r"\b401\b.{0,40}unauthori[sz]ed",
    r"\bunauthori[sz]ed\b",
)

NETWORK_ERROR_TYPES: tuple[str, ...] = (
    "ConnectionError",
    "ConnectionRefusedError",
    "ConnectionResetError",
    "ConnectionAbortedError",
    "APIConnectionError",
    "gaierror",
    "NewConnectionError",
    "MaxRetryError",
    "ConnectError",
    "ConnectTimeout",
    "URLError",
)
NETWORK_MESSAGE_PATTERNS: tuple[str, ...] = (
    r"temporary failure in name resolution",
    r"name or service not known",
    r"network is unreachable",
)

GPU_MESSAGE_PATTERNS: tuple[str, ...] = (
    r"cuda",
    r"no cuda gpus",
    r"not compiled with cuda",
)

_QUOTED_PATH = re.compile(r": '(.*)'\s*$", re.DOTALL)
_MODULE_NAME = re.compile(r"No module named ['\"]([^'\"]+)['\"]")
_IMPORT_FROM = re.compile(r"cannot import name ['\"][^'\"]+['\"] from ['\"]([^'\"]+)['\"]")
_NODE_MISSING_MODULE = re.compile(r"Cannot find (?:module|package) ['\"]([^'\"]+)['\"]")
_BROWSER_REFERENCE = re.compile(r"(?:document|window|navigator|localStorage|alert) is not defined")
_NODE_NETWORK_CODE = r"(?:ENOTFOUND|ECONNREFUSED|EAI_AGAIN)"
_NODE_NETWORK = re.compile(rf"\b(?:{_NODE_NETWORK_CODE}|fetch failed)\b", re.IGNORECASE)
_NODE_NETWORK_CAUSE = re.compile(rf"^\s+code: ['\"]{_NODE_NETWORK_CODE}['\"]", re.M)

_COMMENTS_AND_STRINGS = re.compile(
    r"//[^\n]*|/\*[\s\S]*?\*/"
    r"""|"(?:\\[\s\S]|[^"\\\n])*"|'(?:\\[\s\S]|[^'\\\n])*'|`(?:\\[\s\S]|[^`\\])*`"""
)
_SYSTEM_INCLUDE = re.compile(r"^[ \t]*#[ \t]*include[ \t]*<([^>\n]+)>", re.M)
_C_KEYBOARD = re.compile(
    r"\b(?:(?:scanf|getchar|gets)\s*\(|cin\s*>>"
    r"|fgets\s*\((?:[^();]|\([^()]*\))*?,\s*stdin\s*\))"
)
_READLINE_SPECIFIER = r"""(['"])(?:node:)?readline(?:/promises)?\1"""
_JS_KEYBOARD = re.compile(
    r"(?<![\w$])(?:process\s*\.\s*stdin(?![\w$])|readline\s*[.(]|prompt\s*\()"
    r"|(?<![\w$])(?:require\s*\(\s*|import\s*\(?\s*|from\s+)" + _READLINE_SPECIFIER
)


def _matches(message: str, patterns: tuple[str, ...]) -> bool:
    return any(re.search(p, message, re.IGNORECASE) for p in patterns)


def _is_relative_missing_file(message: str) -> bool:
    """True unless the message names an absolute path. No path in the message counts as relative."""
    found = _QUOTED_PATH.search(message)
    if found is None:
        return True
    path = found.group(1)
    return not (path.startswith(("/", "\\")) or re.match(r"[A-Za-z]:[\\/]", path))


def _missing_module_is_failed(message: str, failed_packages: Collection[str]) -> bool:
    found = _MODULE_NAME.search(message)
    if found is None:
        return False
    top_level = dist_for_import(found.group(1).split(".")[0])
    return top_level in {normalize(p) for p in failed_packages}


def _failed_install_decision(package: str) -> LabelDecision:
    return LabelDecision(
        label=Label.CANT_VERIFY,
        reason=f"{package} didn't install in the sandbox, so this step couldn't run.",
    )


def _label_node_error(error: ErrorInfo, failed_packages: Collection[str]) -> LabelDecision | None:
    if error.type == "ReferenceError" and _BROWSER_REFERENCE.fullmatch(error.message):
        return LabelDecision(label=Label.CANT_VERIFY, reason=REASON_BROWSER)
    if _NODE_NETWORK.search(error.message) or _NODE_NETWORK_CAUSE.search(error.traceback):
        return LabelDecision(label=Label.CANT_VERIFY, reason=REASON_NETWORK)
    if missing := _NODE_MISSING_MODULE.match(error.message):
        package = npm_package_name(missing.group(1))
        if package is not None and package in failed_packages:
            return _failed_install_decision(package)
    return None


def label_step(
    result: ExecResult | None,
    *,
    kind: StepKind = StepKind.PYTHON,
    timed_out: bool = False,
    failed_packages: Collection[str] = (),
    guessed_packages: Collection[str] = (),
    failed_npm_packages: Collection[str] = (),
) -> LabelDecision:
    """Label one step from its sandbox result. Rules apply in the ARCHITECTURE order."""
    if timed_out or result is None:
        return LabelDecision(label=Label.CANT_VERIFY, reason=REASON_TIMEOUT)

    if result.status == "ok":
        categories = sorted(
            {w.category for w in result.warnings if w.category in DEPRECATION_CATEGORIES}
        )
        if categories:
            return LabelDecision(
                label=Label.DEPRECATED, reason="warning during run: " + ", ".join(categories)
            )
        return LabelDecision(label=Label.WORKS)

    etype = result.error.type if result.error else ""
    message = result.error.message if result.error else ""
    broken = LabelDecision(label=Label.BROKEN, reason=f"{etype}: {message}".strip(": "))
    if kind in (StepKind.JAVASCRIPT, StepKind.TYPESCRIPT) and result.error:
        return _label_node_error(result.error, failed_npm_packages) or broken
    if kind not in (StepKind.PYTHON, StepKind.REPL):
        return broken

    if etype in STDIN_ERROR_TYPES:
        return LabelDecision(label=Label.CANT_VERIFY, reason=REASON_KEYBOARD)
    if etype in AUTH_ERROR_TYPES or _matches(message, AUTH_MESSAGE_PATTERNS):
        return LabelDecision(label=Label.CANT_VERIFY, reason=REASON_API_KEY)
    if etype in NETWORK_ERROR_TYPES or _matches(message, NETWORK_MESSAGE_PATTERNS):
        return LabelDecision(label=Label.CANT_VERIFY, reason=REASON_NETWORK)
    if _matches(message, GPU_MESSAGE_PATTERNS):
        return LabelDecision(label=Label.CANT_VERIFY, reason=REASON_GPU)
    if etype == "FileNotFoundError" and _is_relative_missing_file(message):
        return LabelDecision(label=Label.CANT_VERIFY, reason=REASON_DATA_FILE)
    if etype == "ModuleNotFoundError" and _missing_module_is_failed(message, failed_packages):
        found = _MODULE_NAME.search(message)
        package = dist_for_import(found.group(1).split(".")[0]) if found else "The package"
        return _failed_install_decision(package)
    if etype == "ImportError" and (match := _IMPORT_FROM.search(message)):
        if dist_for_import(match.group(1)) in {normalize(name) for name in guessed_packages}:
            return LabelDecision(label=Label.CANT_VERIFY, reason=REASON_LOCAL_MODULE)
    return broken


def _is_cuda_str(node: ast.expr) -> bool:
    return (
        isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value.lower().startswith("cuda")
    )


def _call_name(func: ast.expr) -> str:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _precheck_call(node: ast.Call) -> LabelDecision | None:
    name = _call_name(node.func)
    gpu = LabelDecision(label=Label.CANT_VERIFY, reason=REASON_GPU)
    if name == "cuda" and isinstance(node.func, ast.Attribute):
        return gpu
    if any(kw.arg == "device" and _is_cuda_str(kw.value) for kw in node.keywords):
        return gpu
    if (
        isinstance(node.func, ast.Attribute)
        and name in ("device", "to")
        and node.args
        and _is_cuda_str(node.args[0])
    ):
        return gpu
    if (isinstance(node.func, ast.Name) and name in ("input", "getpass")) or (
        isinstance(node.func, ast.Attribute)
        and name == "getpass"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "getpass"
    ):
        return LabelDecision(label=Label.CANT_VERIFY, reason=REASON_KEYBOARD)
    return None


def _input_scan_code(code: str, *, keep_readline: bool = False) -> str:
    def mask(match: re.Match[str]) -> str:
        token = match.group()
        if keep_readline and re.fullmatch(_READLINE_SPECIFIER, token):
            return token
        # Keep line boundaries and leave a placeholder where a string was an argument.
        replacement = " " if token.startswith("/") else "_"
        return replacement + "\n" * token.count("\n")

    return _COMMENTS_AND_STRINGS.sub(mask, code)


def precheck(
    code: str, *, kind: StepKind = StepKind.PYTHON, c_system_headers: Collection[str] = ()
) -> LabelDecision | None:
    """Decide Can't verify before running, or leave the decision to the sandbox."""
    if kind in (StepKind.C, StepKind.CPP):
        source = _input_scan_code(code)
        for match in _SYSTEM_INCLUDE.finditer(source):
            header = match.group(1).strip()
            if header in c_system_headers:
                return LabelDecision(
                    label=Label.CANT_VERIFY, reason=f"needs the system library {header}"
                )
        if _C_KEYBOARD.search(source):
            return LabelDecision(label=Label.CANT_VERIFY, reason=REASON_KEYBOARD)
        return None
    if kind in (StepKind.JAVASCRIPT, StepKind.TYPESCRIPT):
        if _JS_KEYBOARD.search(_input_scan_code(code, keep_readline=True)):
            return LabelDecision(label=Label.CANT_VERIFY, reason=REASON_KEYBOARD)
        return None
    if kind not in (StepKind.PYTHON, StepKind.REPL):
        return None
    try:
        tree = ast.parse(code)
    except (SyntaxError, ValueError):
        return None
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            decision = _precheck_call(node)
            if decision is not None:
                return decision
    return None


def judge_patch(
    result: ExecResult | None,
    *,
    timed_out: bool = False,
    failed_packages: Collection[str] = (),
    guessed_packages: Collection[str] = (),
) -> tuple[LabelDecision, PatchVerdict]:
    decision = label_step(
        result,
        timed_out=timed_out,
        failed_packages=failed_packages,
        guessed_packages=guessed_packages,
    )
    if decision.label in (Label.WORKS, Label.DEPRECATED):
        return decision, PatchVerdict.FIXED
    environment_reasons = {
        REASON_API_KEY,
        REASON_NETWORK,
        REASON_DATA_FILE,
        REASON_GPU,
        REASON_LOCAL_MODULE,
    }
    if (
        result
        and result.error
        and decision.label == Label.CANT_VERIFY
        and (
            decision.reason in environment_reasons
            or (
                result.error.type == "ModuleNotFoundError"
                and _missing_module_is_failed(result.error.message, failed_packages)
            )
        )
    ):
        frames = re.findall(r'File "([^"]+)", line [0-9]+', result.error.traceback)
        if frames and not frames[-1].startswith("<step-"):
            return decision, PatchVerdict.FIXED_UNVERIFIED
    return decision, PatchVerdict.FAILED


def cascade_source(error: ErrorInfo | None, unbound: Mapping[str, int]) -> int | None:
    if error is None or error.type != "NameError":
        return None
    match = re.fullmatch(r"name ['\"]([^'\"]+)['\"] is not defined", error.message)
    return unbound.get(match.group(1)) if match else None


def cascade_decision(source_idx: int, source_label: Label = Label.BROKEN) -> LabelDecision:
    unverified = source_label == Label.CANT_VERIFY
    return LabelDecision(
        label=Label.CANT_VERIFY if unverified else Label.BROKEN,
        reason=f"{CASCADE_PREFIX}{source_idx + 1}, which "
        + ("couldn't be verified" if unverified else "is still broken"),
    )


def is_cascade(decision: LabelDecision) -> bool:
    return decision.label in (Label.BROKEN, Label.CANT_VERIFY) and decision.reason.startswith(
        CASCADE_PREFIX
    )
