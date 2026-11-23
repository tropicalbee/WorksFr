"""Labeling rules: turn a sandbox result into Works, Deprecated, Broken or Can't verify.

The sandbox run decides (ADR-001). Error matching uses `error.type` (the bare class
name) and `error.message`. Messages match case-insensitively. Patterns are module-level
tuples so they are easy to extend.
"""

import ast
import re
from collections.abc import Collection

from app.models import ExecResult, Label, LabelDecision

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


def _matches(message: str, patterns: tuple[str, ...]) -> bool:
    return any(re.search(p, message, re.IGNORECASE) for p in patterns)


def _normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


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
    top_level = _normalize(found.group(1).split(".")[0])
    return top_level in {_normalize(p) for p in failed_packages}


def label_step(
    result: ExecResult | None,
    *,
    timed_out: bool = False,
    failed_packages: Collection[str] = (),
) -> LabelDecision:
    """Label one step from its sandbox result. Rules apply in the ARCHITECTURE order."""
    if timed_out or result is None:
        return LabelDecision(label=Label.CANT_VERIFY, reason="too slow: the step timed out")

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

    if etype in STDIN_ERROR_TYPES:
        return LabelDecision(label=Label.CANT_VERIFY, reason="needs keyboard input")
    if etype in AUTH_ERROR_TYPES or _matches(message, AUTH_MESSAGE_PATTERNS):
        return LabelDecision(label=Label.CANT_VERIFY, reason="needs an API key")
    if etype in NETWORK_ERROR_TYPES or _matches(message, NETWORK_MESSAGE_PATTERNS):
        return LabelDecision(label=Label.CANT_VERIFY, reason="needs network access")
    if _matches(message, GPU_MESSAGE_PATTERNS):
        return LabelDecision(label=Label.CANT_VERIFY, reason="needs a GPU")
    if etype == "FileNotFoundError" and _is_relative_missing_file(message):
        return LabelDecision(
            label=Label.CANT_VERIFY, reason="needs a data file that is not on the page"
        )
    if etype == "ModuleNotFoundError" and _missing_module_is_failed(message, failed_packages):
        return LabelDecision(
            label=Label.CANT_VERIFY, reason="imports a local file from the author's machine"
        )
    return LabelDecision(label=Label.BROKEN, reason=f"{etype}: {message}".strip(": "))


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
    gpu = LabelDecision(label=Label.CANT_VERIFY, reason="needs a GPU")
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
        return LabelDecision(label=Label.CANT_VERIFY, reason="needs keyboard input")
    return None


def precheck(code: str) -> LabelDecision | None:
    """Decide Can't verify before running, or None. Unparseable code returns None."""
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
