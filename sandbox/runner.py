"""Persistent JSON-lines executor. Container isolation is supplied by the backend."""

import ast
import builtins
import difflib
import importlib
import importlib.metadata
import inspect
import io
import json
import os
import pkgutil
import platform
import sys
import tempfile
import time
import traceback
import warnings
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, TextIO

OUTPUT_LIMIT = 64 * 1024
RESULT_LIMIT = 8 * 1024
TRUNCATION = b"\n[truncated]"
RUNNER_PATH = __file__


class StdinUnavailableError(Exception):
    """The request channel cannot be used for interactive input."""


class ProtocolError(Exception):
    """An invalid JSON-lines request."""


def unavailable_input(prompt: object = "") -> str:
    raise StdinUnavailableError("Keyboard input is unavailable in the sandbox")


def limited_text(data: bytes, limit: int) -> str:
    if len(data) > limit:
        prefix = data[: limit - len(TRUNCATION)].decode("utf-8", errors="ignore")
        return prefix + TRUNCATION.decode("ascii")
    # Re-encode because replacement characters may occupy more bytes than invalid input.
    text = data.decode("utf-8", errors="replace")
    if len(text.encode("utf-8")) > limit:
        return limited_text(text.encode("utf-8"), limit)
    return text


def safe_string(value: object) -> str:
    try:
        return str(value)
    except BaseException:
        return "<unprintable>"


def exception_details(exc: BaseException) -> dict[str, str]:
    try:
        formatted = traceback.TracebackException.from_exception(exc, lookup_lines=False)

        def trim(item: traceback.TracebackException) -> None:
            item.stack = traceback.StackSummary.from_list(
                [frame for frame in item.stack if frame.filename != RUNNER_PATH]
            )
            for linked in (item.__cause__, item.__context__):
                if linked is not None:
                    trim(linked)
            for child in getattr(item, "exceptions", None) or ():
                trim(child)

        trim(formatted)
        detail = "".join(formatted.format())
    except BaseException:
        detail = f"{type(exc).__name__}: {safe_string(exc)}"
    return {"type": type(exc).__name__, "message": safe_string(exc), "traceback": detail}


def warning_package(filename: str) -> str:
    if filename.startswith("<step-"):
        return "__step__"
    parts = filename.replace("\\", "/").split("/")
    for marker in ("site-packages", "dist-packages"):
        if marker in parts:
            index = parts.index(marker) + 1
            if index < len(parts):
                return parts[index].split(".")[0]
    return parts[-1].removesuffix(".py")


def flush_quietly(stream: TextIO) -> None:
    try:
        stream.flush()
    except BaseException:
        pass


class Capture:
    def __init__(self) -> None:
        directory = os.environ.get("WORKSFR_CAPTURE_DIR") or tempfile.gettempdir()
        out_fd, self.stdout_path = tempfile.mkstemp(prefix="worksfr-out-", dir=directory)
        err_fd, self.stderr_path = tempfile.mkstemp(prefix="worksfr-err-", dir=directory)
        self.stdout = os.fdopen(out_fd, "w+b")
        self.stderr = os.fdopen(err_fd, "w+b")
        self.empty_input = open(os.devnull, "rb")
        self.text_streams = [self.text_stream(fd) for fd in (1, 2)]

    @staticmethod
    def text_stream(fd: int) -> TextIO:
        return os.fdopen(
            fd,
            "w",
            encoding="utf-8",
            errors="replace",
            buffering=1,
            newline="\n",
            closefd=False,
        )

    @contextmanager
    def redirected(self) -> Iterator[None]:
        streams = (sys.stdin, sys.stdout, sys.stderr, sys.__stdin__, sys.__stdout__, sys.__stderr__)
        original_input = builtins.input
        flush_quietly(sys.stdout)
        flush_quietly(sys.stderr)
        saved_fds = [os.dup(fd) for fd in (0, 1, 2)]
        try:
            for capture in (self.stdout, self.stderr):
                capture.seek(0)
                capture.truncate(0)
            os.dup2(self.empty_input.fileno(), 0)
            os.dup2(self.stdout.fileno(), 1)
            os.dup2(self.stderr.fileno(), 2)
            for index, stream in enumerate(self.text_streams):
                if stream.closed:
                    self.text_streams[index] = self.text_stream(index + 1)
            sys.stdin = sys.__stdin__ = io.StringIO("")
            sys.stdout = sys.__stdout__ = self.text_streams[0]
            sys.stderr = sys.__stderr__ = self.text_streams[1]
            builtins.input = unavailable_input
            yield
        finally:
            for stream in (sys.stdout, sys.stderr, *self.text_streams):
                flush_quietly(stream)
            for fd, saved in enumerate(saved_fds):
                os.dup2(saved, fd)
                os.close(saved)
            (sys.stdin, sys.stdout, sys.stderr, sys.__stdin__, sys.__stdout__, sys.__stderr__) = (
                streams
            )
            builtins.input = original_input

    def read(self) -> tuple[str, str]:
        output = []
        for capture in (self.stdout, self.stderr):
            capture.seek(0)
            output.append(limited_text(capture.read(OUTPUT_LIMIT + 1), OUTPUT_LIMIT))
        return output[0], output[1]

    def close(self) -> None:
        self.stdout.close()
        self.stderr.close()
        self.empty_input.close()


def execute(request: dict[str, Any], namespace: dict[str, Any], capture: Capture) -> dict[str, Any]:
    started = time.monotonic()
    step_id = request.get("id")
    filename = f"<step-{step_id}>"
    result = None
    error = None
    recorded: list[Any] = []
    with capture.redirected():
        try:
            with warnings.catch_warnings(record=True) as recorded:
                warnings.simplefilter("always")
                tree = ast.parse(request["code"], filename=filename, mode="exec")
                expression = None
                if tree.body and isinstance(tree.body[-1], ast.Expr):
                    expression = ast.Expression(tree.body.pop().value)
                exec(compile(tree, filename, "exec"), namespace)
                if expression is not None:
                    value = eval(compile(expression, filename, "eval"), namespace)
                    if value is not None:
                        result = limited_text(
                            repr(value).encode("utf-8", errors="replace"), RESULT_LIMIT
                        )
        except BaseException as exc:
            error = exception_details(exc)
        warning_records = [
            {
                "category": item.category.__name__,
                "message": safe_string(item.message),
                "filename": item.filename,
                "lineno": item.lineno,
                "module": warning_package(item.filename),
            }
            for item in recorded
        ]
    stdout, stderr = capture.read()
    return {
        "id": step_id,
        "status": "error" if error else "ok",
        "stdout": stdout,
        "stderr": stderr,
        "result": result,
        "warnings": warning_records,
        "error": error,
        "duration_ms": round((time.monotonic() - started) * 1000),
    }


def resolve_target(target: str, namespace: dict[str, Any]) -> Any:
    parts = target.split(".")
    if parts[0] in namespace:
        obj = namespace[parts[0]]
        remaining = parts[1:]
    else:
        for length in range(len(parts), 0, -1):
            try:
                obj = importlib.import_module(".".join(parts[:length]))
            except ModuleNotFoundError as exc:
                # Don't hide a missing dependency from inside an importable module.
                prefix = ".".join(parts[:length])
                if exc.name != prefix and not prefix.startswith(f"{exc.name}."):
                    raise
            else:
                remaining = parts[length:]
                break
        else:
            raise ModuleNotFoundError(f"Cannot resolve {target}")
    for part in remaining:
        obj = getattr(obj, part)
    return obj


def installed_version(package: str) -> str | None:
    distributions = importlib.metadata.packages_distributions().get(package, [])
    for distribution in distributions:
        try:
            return importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            continue
    return None


def describe(target: Any, name: str) -> dict[str, Any]:
    try:
        attribute = getattr(target, name)
    except AttributeError:
        if not inspect.ismodule(target) or not hasattr(target, "__path__"):
            raise
        attribute = importlib.import_module(f"{target.__name__}.{name}")
    try:
        signature = str(inspect.signature(attribute))
    except (TypeError, ValueError):
        signature = None
    return {"name": name, "signature": signature, "doc": (inspect.getdoc(attribute) or "")[:2000]}


def probe(request: dict[str, Any], namespace: dict[str, Any], capture: Capture) -> dict[str, Any]:
    reply: dict[str, Any] = {
        "id": request.get("id"),
        "status": "ok",
        "target_found": False,
        "exists": False,
        "close_matches": [],
        "closest": None,
        "package": None,
        "version": None,
        "error": None,
    }
    with capture.redirected():
        try:
            target = resolve_target(request["target"], namespace)
        except BaseException as exc:
            reply["error"] = exception_details(exc)
            return reply
        reply["target_found"] = True
        try:
            module = (
                target.__name__ if inspect.ismodule(target) else getattr(target, "__module__", None)
            )
            if module is None:
                module = type(target).__module__
            package = module.split(".")[0]
            reply["package"] = package
            reply["version"] = installed_version(package)
            candidates = set(dir(target))
            if inspect.ismodule(target) and hasattr(target, "__path__"):
                candidates.update(item.name for item in pkgutil.iter_modules(target.__path__))
            name = request["name"]
            reply["exists"] = hasattr(target, name)
            matches = difflib.get_close_matches(name, sorted(candidates), n=5)
            reply["close_matches"] = matches
            closest = name if reply["exists"] else matches[0] if matches else None
            if closest is not None:
                reply["closest"] = describe(target, closest)
        except BaseException as exc:
            reply["status"] = "error"
            reply["error"] = exception_details(exc)
    return reply


def validate(request: Any) -> dict[str, Any]:
    if not isinstance(request, dict):
        raise ProtocolError("Request must be a JSON object")
    op = request.get("op")
    if op not in ("exec", "probe"):
        raise ProtocolError("Unknown operation; expected exec or probe")
    fields = ("code",) if op == "exec" else ("target", "name")
    for field in fields:
        if not isinstance(request.get(field), str):
            raise ProtocolError(f"{field} must be a string")
    return request


def send(protocol_fd: int, reply: dict[str, Any]) -> None:
    data = (json.dumps(reply, ensure_ascii=True) + "\n").encode("utf-8")
    while data:
        written = os.write(protocol_fd, data)
        data = data[written:]


def main() -> None:
    protocol_fd = os.dup(1)
    os.set_inheritable(protocol_fd, False)
    flush_quietly(sys.stdout)
    flush_quietly(sys.stderr)
    with open(os.devnull, "wb") as sink:
        os.dup2(sink.fileno(), 1)
        os.dup2(sink.fileno(), 2)
    requests = sys.stdin
    namespace: dict[str, Any] = {"__name__": "__main__", "__builtins__": builtins}
    capture = Capture()
    try:
        send(protocol_fd, {"op": "ready", "python": platform.python_version()})
        for line in requests:
            request: Any = None
            try:
                try:
                    request = json.loads(line)
                except (ValueError, RecursionError) as exc:
                    raise ProtocolError(f"Invalid JSON: {exc}") from None
                validate(request)
                if request["op"] == "exec":
                    reply = execute(request, namespace, capture)
                else:
                    reply = probe(request, namespace, capture)
            except BaseException as exc:
                reply = {
                    "id": request.get("id") if isinstance(request, dict) else None,
                    "status": "error",
                    "error": exception_details(exc),
                }
            send(protocol_fd, reply)
    finally:
        capture.close()
        os.close(protocol_fd)


if __name__ == "__main__":
    main()
