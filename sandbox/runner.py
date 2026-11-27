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
import re
import signal
import subprocess
import sys
import tempfile
import time
import traceback
import uuid
import warnings
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, BinaryIO, TextIO

OUTPUT_LIMIT = 64 * 1024
RESULT_LIMIT = 8 * 1024
MAX_REPLY_BYTES = 1024 * 1024
TRUNCATION = b"\n[truncated]"
RUNNER_PATH = __file__
EXEC_LANGUAGES = ("python", "javascript", "typescript", "c", "cpp")
SOURCE_TOKENS = re.compile(
    r"""//[^\r\n]*|/\*[\s\S]*?\*/|"(?:\\[\s\S]|[^"\\])*"|'(?:\\[\s\S]|[^'\\])*'|`(?:\\[\s\S]|[^`\\])*`"""
)
JS_MODULE_DECLARATION = re.compile(
    r"""(?:^|[;\r\n])\s*(?:import\s*(?:['"{*]|\.\s*meta\b)|import\s+[$\w]|export(?:\s*[{*]|\s+[$\w]))"""
)
NODE_ERROR = re.compile(r"^(\w*Error|\w*Exception)(?: \[([A-Z_0-9]+)\])?: (.*)$", re.MULTILINE)
NODE_DEPRECATION = re.compile(r"^\(node:\d+\) \[DEP\d+\] DeprecationWarning: (.*)$", re.MULTILINE)
TSX_REDECLARATION = re.compile(
    r'^.*\.[cm]ts:\d+:\d+: ERROR: The symbol ".*" has already been declared$', re.MULTILINE
)
NODE_TOP_LEVEL_AWAIT = re.compile(
    r"await is only valid in async functions and the top level bodies of modules"
    r'|Top-level await is currently not supported with the "cjs" output format'
)
COMPILED_RESULT = "compiled"
C_MAIN = re.compile(r"\bmain\s*\([^();{}]*\)\s*(?:->\s*int\s*)?\{")
C_PREAMBLE = re.compile(r"^\s*#\s*(?:include|define)\b")
COMPILER_ERROR = re.compile(r"^(?:.*?: )?(?:fatal )?error:.*$", re.MULTILINE)
COMPILER_REDECLARATION = re.compile(
    r"^(?:.*?: )?(?:error|note): "
    r"(?:redefinition of|conflicting types for|previous definition of)\b",
    re.MULTILINE,
)


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


def output_after_marker(stream: BinaryIO, marker: bytes) -> tuple[bytes, bool]:
    stream.seek(0)
    tail = b""
    while chunk := stream.read(OUTPUT_LIMIT):
        data = tail + chunk
        index = data.find(marker)
        if index >= 0:
            stream.seek(stream.tell() - (len(data) - index - len(marker)))
            return stream.read(OUTPUT_LIMIT + 1), True
        tail = data[-(len(marker) - 1) :] if len(marker) > 1 else b""
    stream.seek(0)
    return stream.read(OUTPUT_LIMIT + 1), False


def mask_comments_and_strings(code: str) -> str:
    def mask(match: re.Match[str]) -> str:
        token = match[0]
        lines = "\n" * token.count("\n")
        if token.startswith("/"):
            return " " + lines
        return token[0] + lines + token[0]

    return SOURCE_TOKENS.sub(mask, code)


def node_extension(code: str, typescript: bool, *, as_module: bool = False) -> str:
    module = as_module or JS_MODULE_DECLARATION.search(mask_comments_and_strings(code))
    if typescript:
        return ".mts" if module else ".cts"
    return ".mjs" if module else ".cjs"


def node_error(stderr: str, returncode: int, traceback_text: str) -> dict[str, str]:
    if matches := list(NODE_ERROR.finditer(stderr)):
        category, code, message = matches[-1].groups()
        return {
            "type": code if code == "ERR_MODULE_NOT_FOUND" else category,
            "message": message,
            "traceback": traceback_text,
        }
    return exit_status(returncode, traceback_text)


def node_redeclaration(error: dict[str, str]) -> bool:
    message = error["message"]
    if error["type"] == "SyntaxError":
        return message.startswith("Identifier ") and message.endswith(" has already been declared")
    return (
        error["type"] == "Error"
        and message.startswith("Transform failed with ")
        and TSX_REDECLARATION.search(error["traceback"]) is not None
    )


def node_top_level_await(error: dict[str, str]) -> bool:
    # Node reports a SyntaxError. tsx reports an esbuild transform failure with the detail
    # in the traceback.
    return NODE_TOP_LEVEL_AWAIT.search(f"{error['message']}\n{error['traceback']}") is not None


def deprecation(message: str, filename: str, lineno: int | None) -> dict[str, Any]:
    return {
        "category": "DeprecationWarning",
        "message": message,
        "filename": filename,
        "lineno": lineno,
        "module": "__step__",
    }


def node_warnings(
    stderr: str, source_name: str, current_start: int, filename: str
) -> list[dict[str, Any]]:
    location = re.compile(rf"{re.escape(source_name)}:(\d+):\d+")
    records = []
    # Node can print warnings on the next tick, after the history marker has been written.
    blocks = re.split(r"(?m)(?=^\(node:\d+\) )", stderr)
    for block in blocks:
        if not (match := NODE_DEPRECATION.match(block)):
            continue
        stack_lines = [int(item[1]) for item in location.finditer(block, match.end())]
        current = next((line for line in stack_lines if line >= current_start), None)
        if current is not None:
            records.append(deprecation(match[1], filename, current - current_start + 1))
        elif not stack_lines:
            records.append(deprecation(match[1], filename, None))
    return records


def compiler_error(stderr: str, returncode: int) -> dict[str, str]:
    match = COMPILER_ERROR.search(stderr)
    message = match[0] if match else f"Compiler exited with status {returncode}"
    return {"type": "CompileError", "message": message, "traceback": stderr}


def compiler_redeclaration(stderr: str) -> bool:
    return COMPILER_REDECLARATION.search(stderr) is not None


def compiler_warnings(stderr: str, source_name: str, filename: str) -> list[dict[str, Any]]:
    diagnostic = re.compile(
        rf"^{re.escape(source_name)}:(\d+):(?:\d+:)? warning: (.*) \[-Wdeprecated-declarations\]$",
        re.MULTILINE,
    )
    return [deprecation(match[2], filename, int(match[1])) for match in diagnostic.finditer(stderr)]


def wrap_c_main(code: str, source_name: str) -> str:
    preamble, body = [], []
    continued = False
    masked = mask_comments_and_strings(code)
    for line, masked_line in zip(code.split("\n"), masked.split("\n"), strict=True):
        directive = continued or C_PREAMBLE.match(masked_line)
        preamble.append(line if directive else "")
        body.append("" if directive else line)
        continued = bool(directive) and line.rstrip().endswith("\\")
    line_directive = f'#line 1 "{source_name}"\n'
    includes_and_defines = "\n".join(preamble)
    statements = "\n".join(body)
    return (
        f"{line_directive}{includes_and_defines}\n"
        "int main(void) {\n"
        f"{line_directive}{statements}\n}}\n"
    )


def exit_status(returncode: int, stderr: str) -> dict[str, str]:
    return {
        "type": "ExitStatus",
        "message": f"Process exited with status {returncode}",
        "traceback": stderr,
    }


def native_run_error(returncode: int, stderr: str) -> dict[str, str] | None:
    if returncode == 0:
        return None
    if returncode > 0:
        return exit_status(returncode, stderr)
    try:
        name = signal.Signals(-returncode).name
    except ValueError:
        name = f"signal {-returncode}"
    return {"type": "Signal", "message": name, "traceback": stderr}


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

    def reset(self) -> None:
        for capture in (self.stdout, self.stderr):
            capture.seek(0)
            capture.truncate(0)

    def run_child(self, args: list[str], workdir: Path) -> int:
        self.reset()
        return subprocess.run(
            args,
            cwd=workdir,
            stdin=subprocess.DEVNULL,
            stdout=self.stdout,
            stderr=self.stderr,
            check=False,
        ).returncode

    @contextmanager
    def redirected(self) -> Iterator[None]:
        streams = (sys.stdin, sys.stdout, sys.stderr, sys.__stdin__, sys.__stdout__, sys.__stderr__)
        original_input = builtins.input
        flush_quietly(sys.stdout)
        flush_quietly(sys.stderr)
        saved_fds = [os.dup(fd) for fd in (0, 1, 2)]
        try:
            self.reset()
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


# stdout, stderr, warnings, error, result
ChildOutcome = tuple[str, str, list[dict[str, Any]], dict[str, str] | None, str | None]


class ChildExecutor:
    def __init__(self, workdir: Path, capture: Capture) -> None:
        self.workdir = workdir
        self.capture = capture
        self.node_history: list[tuple[str, str]] = []
        self.native_history: dict[str, list[tuple[str, bool]]] = {"c": [], "cpp": []}

    def execute(self, request: dict[str, Any], lang: str) -> dict[str, Any]:
        started = time.monotonic()
        filename = f"<step-{request.get('id')}>"
        code = request["code"]
        if lang in self.native_history:
            stdout, stderr, records, error, result = self.run_native(
                code, lang, filename, self.native_history[lang]
            )
        else:
            stdout, stderr, records, error, result = self.run_node(
                code, lang, filename, self.node_history
            )
        return {
            "id": request.get("id"),
            "status": "error" if error else "ok",
            "stdout": stdout,
            "stderr": stderr,
            "result": result,
            "warnings": records,
            "error": error,
            "duration_ms": round((time.monotonic() - started) * 1000),
        }

    def run_node(
        self,
        code: str,
        lang: str,
        filename: str,
        history: list[tuple[str, str]],
        *,
        as_module: bool = False,
    ) -> ChildOutcome:
        prefix = "".join(f"{previous}\n;\n" for _, previous in history)
        current_start = prefix.count("\n") + 2
        typescript = lang == "typescript" or any(kind == "typescript" for kind, _ in history)
        source = self.source_path(node_extension(prefix + code, typescript, as_module=as_module))
        marker = f"worksfr-{uuid.uuid4().hex}\n"
        marker_literal = json.dumps(marker)
        boundary = (
            f"globalThis.process.stdout.write({marker_literal}); "
            f"globalThis.process.stderr.write({marker_literal});\n"
        )
        self.write_source(source, prefix + boundary + code)
        returncode = self.capture.run_child(
            ["tsx" if typescript else "node", "--trace-deprecation", str(source)], self.workdir
        )
        out, _ = output_after_marker(self.capture.stdout, marker.encode("ascii"))
        err, marked = output_after_marker(self.capture.stderr, marker.encode("ascii"))
        stdout = limited_text(out, OUTPUT_LIMIT)
        stderr = limited_text(err, OUTPUT_LIMIT)
        records = node_warnings(stderr, source.name, current_start, filename) if marked else []
        error = None
        if returncode:
            _, full_stderr = self.capture.read()
            error = node_error(stderr, returncode, full_stderr)
            if not marked:
                # A CommonJS file cannot hold top-level await, so try this step once as a module.
                if not as_module and node_top_level_await(error):
                    return self.run_node(code, lang, filename, history, as_module=True)
                # The replayed history exited the process or redeclared a name. Run this step alone.
                if history and (
                    node_redeclaration(error) or (error["type"] == "ExitStatus" and returncode > 0)
                ):
                    return self.run_node_alone(code, lang, filename, history)
        elif marked:
            history.append((lang, code))
        else:
            if history:
                return self.run_node_alone(code, lang, filename, history)
            error = {
                "type": "ExitStatus",
                "message": "Node exited before the current step ran",
                "traceback": stderr,
            }
        return stdout, stderr, records, error, None

    def run_node_alone(
        self, code: str, lang: str, filename: str, history: list[tuple[str, str]]
    ) -> ChildOutcome:
        isolated_history: list[tuple[str, str]] = []
        outcome = self.run_node(code, lang, filename, isolated_history)
        if isolated_history:
            history[:] = isolated_history
        return outcome

    def run_native(
        self, code: str, lang: str, filename: str, history: list[tuple[str, bool]]
    ) -> ChildOutcome:
        compiler, standard, suffix = (
            ("gcc", "-std=c17", ".c") if lang == "c" else ("g++", "-std=c++20", ".cpp")
        )
        parts = []
        for index, (previous, has_main) in enumerate(history):
            # Keep definitions from earlier programs while reserving the entry point for this step.
            if has_main:
                previous = f"#define main worksfr_previous_main_{index}\n{previous}\n#undef main\n"
            parts.append(previous)
        prefix = "\n".join(parts)
        has_main = bool(C_MAIN.search(mask_comments_and_strings(code)))
        source = self.source_path(suffix)
        current = f'#line 1 "{source.name}"\n{code}\n'
        self.write_source(source, f"{prefix}\n{current}")
        target = source.with_suffix(".out" if has_main else ".o")
        args = [compiler, standard, "-Wall"]
        if not has_main:
            args.append("-c")
        returncode = self.capture.run_child([*args, str(source), "-o", str(target)], self.workdir)
        stdout, stderr = self.capture.read()
        # Wrapping can hide a conflict by moving the declaration into a local scope.
        redeclared = returncode != 0 and bool(history) and compiler_redeclaration(stderr)
        if returncode and not has_main and not redeclared:
            has_main = True
            source = self.source_path(suffix)
            target = source.with_suffix(".out")
            current = wrap_c_main(code, source.name)
            self.write_source(source, f"{prefix}\n{current}")
            returncode = self.capture.run_child(
                [compiler, standard, "-Wall", str(source), "-o", str(target)], self.workdir
            )
            stdout, stderr = self.capture.read()
            redeclared = returncode != 0 and bool(history) and compiler_redeclaration(stderr)
        if redeclared:
            isolated_history: list[tuple[str, bool]] = []
            result = self.run_native(code, lang, filename, isolated_history)
            if isolated_history:
                history[:] = isolated_history
            return result
        records = compiler_warnings(stderr, source.name, filename)
        if returncode:
            return stdout, stderr, records, compiler_error(stderr, returncode), None
        if has_main:
            returncode = self.capture.run_child([str(target)], self.workdir)
            run_stdout, run_stderr = self.capture.read()
            stdout = limited_text((stdout + run_stdout).encode("utf-8"), OUTPUT_LIMIT)
            stderr = limited_text((stderr + run_stderr).encode("utf-8"), OUTPUT_LIMIT)
            if error := native_run_error(returncode, stderr):
                return stdout, stderr, records, error, None
        history.append((current, has_main))
        # With no main() the step was only compiled. The backend labels that Can't verify.
        return stdout, stderr, records, None, None if has_main else COMPILED_RESULT

    def source_path(self, suffix: str) -> Path:
        return self.workdir / f"worksfr-{uuid.uuid4().hex}{suffix}"

    @staticmethod
    def write_source(path: Path, code: str) -> None:
        with path.open("x", encoding="utf-8", newline="\n") as source:
            source.write(code)


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
    if "nonce" in request and not isinstance(request["nonce"], str):
        raise ProtocolError("nonce must be a string")
    op = request.get("op")
    if op not in ("exec", "probe"):
        raise ProtocolError("Unknown operation; expected exec or probe")
    fields = ("code",) if op == "exec" else ("target", "name")
    for field in fields:
        if not isinstance(request.get(field), str):
            raise ProtocolError(f"{field} must be a string")
    if op == "exec" and request.get("lang", "python") not in EXEC_LANGUAGES:
        raise ProtocolError("lang must be python, javascript, typescript, c or cpp")
    return request


def encode_reply(reply: dict[str, Any], *, child: bool = False) -> bytes:
    data = (json.dumps(reply, ensure_ascii=True) + "\n").encode("utf-8")
    if not child or len(data) <= MAX_REPLY_BYTES:
        return data

    # JSON can expand control characters sixfold, and warning fields repeat parts of stderr.
    shortened = dict(reply)
    for field in ("stdout", "stderr"):
        if field in reply:
            shortened[field] = limited_text(reply[field].encode("utf-8"), OUTPUT_LIMIT // 2)
    if error := reply.get("error"):
        shortened["error"] = {
            field: limited_text(
                value.encode("utf-8", errors="replace"),
                OUTPUT_LIMIT // 2 if field == "traceback" else RESULT_LIMIT,
            )
            for field, value in error.items()
        }
    if records := reply.get("warnings"):
        first = records[0]
        shortened["warnings"] = [
            {**first, "message": limited_text(first["message"].encode("utf-8"), RESULT_LIMIT)}
        ]
    return (json.dumps(shortened, ensure_ascii=True) + "\n").encode("utf-8")


def send(protocol_fd: int, reply: dict[str, Any], *, child: bool = False) -> None:
    data = encode_reply(reply, child=child)
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
    children = ChildExecutor(Path.cwd(), capture)
    try:
        send(protocol_fd, {"op": "ready", "python": platform.python_version()})
        for line in requests:
            request: Any = None
            child = False
            try:
                try:
                    request = json.loads(line)
                except (ValueError, RecursionError) as exc:
                    raise ProtocolError(f"Invalid JSON: {exc}") from None
                validate(request)
                if request["op"] == "exec":
                    lang = request.get("lang", "python")
                    if lang == "python":
                        reply = execute(request, namespace, capture)
                    else:
                        child = True
                        reply = children.execute(request, lang)
                else:
                    reply = probe(request, namespace, capture)
            except BaseException as exc:
                reply = {
                    "id": request.get("id") if isinstance(request, dict) else None,
                    "status": "error",
                    "error": exception_details(exc),
                }
            if isinstance(request, dict) and isinstance(request.get("nonce"), str):
                reply["nonce"] = request["nonce"]
            send(protocol_fd, reply, child=child)
    finally:
        capture.close()
        os.close(protocol_fd)


if __name__ == "__main__":
    main()
