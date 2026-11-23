"""Docker CLI adapter; tutorial code is sent only to the container runner."""

import json
import queue
import re
import subprocess
import threading
import time
from types import TracebackType
from typing import BinaryIO, Protocol, Self, TypeVar

from pydantic import ValidationError

from app.config import Settings
from app.models import Environment, ExecResult, ProbeResult
from app.sandbox.interface import SandboxError, StepTimeoutError

MAX_REPLY_BYTES = 1024 * 1024
PACKAGE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*", re.ASCII)
VERSION_CODE = (
    "import importlib.metadata as m, json; "
    "print(json.dumps({d.metadata['Name']: d.version for d in m.distributions() "
    "if d.metadata['Name']}))"
)
Result = TypeVar("Result", ExecResult, ProbeResult)


def _normalized_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


class RunnerProcess(Protocol):
    stdin: BinaryIO | None
    stdout: BinaryIO | None

    def kill(self) -> None: ...
    def wait(self, timeout: float) -> int: ...


class CommandRunner(Protocol):
    def run(self, args: list[str], timeout: float) -> subprocess.CompletedProcess[str]: ...
    def popen(self, args: list[str]) -> RunnerProcess: ...


class SubprocessCommands:
    def run(self, args: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            args,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )

    def popen(self, args: list[str]) -> subprocess.Popen[bytes]:
        return subprocess.Popen(
            args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )


class DockerSandbox:
    def __init__(self, settings: Settings, check_id: str, run: CommandRunner | None = None) -> None:
        if not PACKAGE_NAME.fullmatch(check_id):
            raise SandboxError("Invalid check identifier")
        self.settings = settings
        self._check_id = check_id
        self._commands = run if run is not None else SubprocessCommands()
        self._generation = 0
        self._container: str | None = None
        self._process: RunnerProcess | None = None
        self._packages: list[str] = []
        self._deadline: float | None = None
        self._usable = False

    def _time_left(self, timeout: float) -> float:
        if self._deadline is None:
            return timeout
        remaining = self._deadline - time.monotonic()
        if remaining <= 0:
            raise SandboxError("Whole-check timeout exceeded")
        return min(timeout, remaining)

    def _run(self, args: list[str], timeout: float = 60) -> subprocess.CompletedProcess[str]:
        try:
            return self._commands.run(args, timeout=self._time_left(timeout))
        except (OSError, subprocess.SubprocessError) as exc:
            raise SandboxError(f"Docker command failed: {type(exc).__name__}") from None

    def _require_success(self, result: subprocess.CompletedProcess[str], action: str) -> None:
        if result.returncode:
            raise SandboxError(f"{action} failed (exit {result.returncode})")

    def _install(self, name: str, packages: list[str]) -> bool:
        try:
            result = self._commands.run(
                ["docker", "exec", name, "pip", "install", "--upgrade", *packages],
                timeout=self._time_left(300),
            )
        except subprocess.TimeoutExpired:
            return False
        return result.returncode == 0

    def start(self, packages: list[str]) -> Environment:
        if self._container is not None:
            raise SandboxError("Sandbox already started")
        self._packages = list(packages)
        if self._deadline is None:
            self._deadline = time.monotonic() + self.settings.check_timeout_seconds
        valid, failed = [], []
        for package in dict.fromkeys(packages):
            (valid if PACKAGE_NAME.fullmatch(package) else failed).append(package)
        self._generation += 1
        self._container = f"worksfr-{self._check_id}-{self._generation}"
        name = self._container
        try:
            self._require_success(
                self._run(
                    [
                        "docker",
                        "run",
                        "-d",
                        "--name",
                        name,
                        "--label",
                        "worksfr=1",
                        "--memory",
                        self.settings.sandbox_memory,
                        "--cpus",
                        str(self.settings.sandbox_cpus),
                        "--pids-limit",
                        str(self.settings.sandbox_pids_limit),
                        "--cap-drop",
                        "ALL",
                        "--security-opt",
                        "no-new-privileges",
                        "--user",
                        "sandbox",
                        "--network",
                        "bridge",
                        self.settings.sandbox_image,
                        "sleep",
                        "infinity",
                    ]
                ),
                "Container startup",
            )
            if valid:
                if not self._install(name, valid):
                    for package in valid:
                        if not self._install(name, [package]):
                            failed.append(package)
            versions = self._run(["docker", "exec", name, "python", "-c", VERSION_CODE])
            self._require_success(versions, "Version inspection")
            installed = json.loads(versions.stdout)
            if not isinstance(installed, dict) or not all(
                isinstance(key, str) and isinstance(value, str) for key, value in installed.items()
            ):
                raise SandboxError("Invalid installed-version metadata")
            disconnected = self._run(["docker", "network", "disconnect", "bridge", name])
            self._require_success(disconnected, "Network disconnect")
            self._process = self._commands.popen(
                [
                    "docker",
                    "exec",
                    "-i",
                    name,
                    "python",
                    "-u",
                    "/opt/worksfr/runner.py",
                ]
            )
            ready = json.loads(
                self._exchange(None, self._time_left(self.settings.step_timeout_seconds))
            )
            if (
                not isinstance(ready, dict)
                or ready.get("op") != "ready"
                or not isinstance(ready.get("python"), str)
            ):
                raise SandboxError("Runner did not send a valid ready message")
            self._usable = True
            normalized = {_normalized_name(key): value for key, value in installed.items()}
            requested_versions = {
                package: normalized[_normalized_name(package)]
                for package in valid
                if _normalized_name(package) in normalized
            }
            return Environment(versions=requested_versions, failed_packages=failed)
        except Exception as exc:
            self.close()
            if isinstance(exc, SandboxError):
                raise
            raise SandboxError(f"Sandbox preparation failed: {type(exc).__name__}") from None

    def _exchange(self, request: dict[str, str] | None, timeout: float) -> bytes:
        process = self._process
        if process is None or process.stdin is None or process.stdout is None:
            raise SandboxError("Runner is not running")
        replies: queue.Queue[bytes | Exception] = queue.Queue(maxsize=1)

        def communicate() -> None:
            try:
                if request is not None:
                    data = (json.dumps(request) + "\n").encode("utf-8")
                    process.stdin.write(data)
                    process.stdin.flush()
                replies.put(process.stdout.readline(MAX_REPLY_BYTES + 1))
            except Exception as exc:
                replies.put(exc)

        threading.Thread(target=communicate, daemon=True).start()
        try:
            line = replies.get(timeout=timeout)
        except queue.Empty:
            self._kill_container()
            raise StepTimeoutError("Sandbox runner exceeded the step timeout") from None
        if isinstance(line, Exception):
            raise SandboxError(f"Runner communication failed: {type(line).__name__}") from None
        if not line:
            raise SandboxError("Runner exited without a reply")
        if len(line) > MAX_REPLY_BYTES or not line.endswith(b"\n"):
            raise SandboxError("Runner reply exceeds 1 MB or is not a complete line")
        return line

    def _request(self, request: dict[str, str], schema: type[Result]) -> Result:
        if not self._usable:
            raise SandboxError("Runner is not ready; start or restart the sandbox")
        try:
            line = self._exchange(request, self._time_left(self.settings.step_timeout_seconds))
            result = schema.model_validate_json(line)
            if result.id != request["id"]:
                raise SandboxError("Runner reply id does not match the request")
            if result.error is not None and result.error.type == "ProtocolError":
                raise SandboxError("Runner reported a protocol error")
            return result
        except (ValidationError, ValueError):
            self._kill_container()
            raise SandboxError("Runner sent an invalid reply") from None
        except SandboxError:
            self._kill_container()
            raise

    def exec(self, step_id: str, code: str) -> ExecResult:
        return self._request({"op": "exec", "id": step_id, "code": code}, ExecResult)

    def probe(self, probe_id: str, target: str, name: str) -> ProbeResult:
        return self._request(
            {"op": "probe", "id": probe_id, "target": target, "name": name},
            ProbeResult,
        )

    def restart(self, replay: list[str]) -> None:
        packages = list(self._packages)
        self.close()
        self.start(packages)
        for index, code in enumerate(replay):
            self.exec(f"replay-{index}", code)

    def _kill_container(self) -> None:
        self._usable = False
        if self._container is not None:
            try:
                self._commands.run(["docker", "kill", self._container], timeout=10)
            except Exception:
                pass

    def close(self) -> None:
        self._usable = False
        process, self._process = self._process, None
        name, self._container = self._container, None
        if process is not None:
            try:
                process.kill()
                process.wait(timeout=5)
            except Exception:
                pass
            for stream in (process.stdin, process.stdout):
                if stream is not None:
                    try:
                        stream.close()
                    except Exception:
                        pass
        if name is not None:
            try:
                self._commands.run(["docker", "rm", "-f", name], timeout=10)
            except Exception:
                pass

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()
