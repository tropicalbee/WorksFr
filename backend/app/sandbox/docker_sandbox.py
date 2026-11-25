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
from app.models import Environment, ExecResult, ProbeResult, StepKind
from app.packages import normalize
from app.sandbox.interface import SandboxError, SandboxTimeLimit, StepTimeoutError

MAX_REPLY_BYTES = 1024 * 1024
PACKAGE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*", re.ASCII)
NPM_NAME_PART = r"[A-Za-z0-9][A-Za-z0-9._~-]*"
NPM_PACKAGE_NAME = re.compile(rf"(?:@{NPM_NAME_PART}/)?{NPM_NAME_PART}", re.ASCII)
NPM_VERSION = re.compile(
    r"(?:0|[1-9]\d*)(?:\.(?:0|[1-9]\d*)){2}"
    r"(?:-[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*)?"
    r"(?:\+[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*)?",
    re.ASCII,
)
WORK_DIR = "/home/sandbox/work"
VERSION_CODE = (
    "import importlib.metadata as m, json; "
    "print(json.dumps({d.metadata['Name']: d.version for d in m.distributions() "
    "if d.metadata['Name']}))"
)
NPM_VERSION_CODE = """
const fs = require('node:fs');
const versions = [];
for (const name of process.argv.slice(1)) {
    try {
        const metadata = JSON.parse(fs.readFileSync(`node_modules/${name}/package.json`, 'utf8'));
        versions.push([name, metadata.version]);
    } catch (error) {
        if (error.code !== 'ENOENT') throw error;
    }
}
console.log(JSON.stringify(Object.fromEntries(versions)));
"""
Result = TypeVar("Result", ExecResult, ProbeResult)


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
        self._npm_packages: list[str] = []
        self._deadline: float | None = None
        self._usable = False
        self._initial_environment: Environment | None = None
        self._pinned_restart = False

    def _deadline_passed(self) -> bool:
        return self._deadline is not None and time.monotonic() >= self._deadline

    def _time_left(self, timeout: float) -> float:
        if self._deadline is None:
            return timeout
        remaining = self._deadline - time.monotonic()
        if remaining <= 0:
            raise SandboxTimeLimit("Whole-check timeout exceeded")
        return min(timeout, remaining)

    def _run(self, args: list[str], timeout: float = 60) -> subprocess.CompletedProcess[str]:
        try:
            return self._commands.run(args, timeout=self._time_left(timeout))
        except (OSError, subprocess.SubprocessError) as exc:
            if isinstance(exc, subprocess.TimeoutExpired) and self._deadline_passed():
                raise SandboxTimeLimit("Whole-check timeout exceeded") from None
            raise SandboxError(f"Docker command failed: {type(exc).__name__}") from None

    def _require_success(self, result: subprocess.CompletedProcess[str], action: str) -> None:
        if result.returncode:
            raise SandboxError(f"{action} failed (exit {result.returncode})")

    def _install(self, args: list[str]) -> bool:
        try:
            result = self._commands.run(args, timeout=self._time_left(300))
        except subprocess.TimeoutExpired:
            if self._deadline_passed():
                raise SandboxTimeLimit("Whole-check timeout exceeded") from None
            return False
        return result.returncode == 0

    def _failed_installs(self, command: list[str], packages: list[str]) -> list[str]:
        if not packages or self._install([*command, *packages]):
            return []
        return [package for package in packages if not self._install([*command, package])]

    def _prepare_npm(
        self, name: str, packages: list[str], baseline: Environment | None
    ) -> tuple[dict[str, str], list[str]]:
        command = ["docker", "exec", "-w", WORK_DIR, name]
        install = [*command, "npm", "install", "--ignore-scripts", "--no-audit", "--no-fund"]
        if baseline is None:
            valid, failed = [], []
            for package in dict.fromkeys(packages):
                (valid if NPM_PACKAGE_NAME.fullmatch(package) else failed).append(package)
            failed.extend(self._failed_installs(install, valid))
            requested = [package for package in valid if package not in failed]
        else:
            requested = list(baseline.npm_versions)
            failed = list(baseline.failed_npm_packages)
            pins = [f"{package}@{baseline.npm_versions[package]}" for package in requested]
            if pins and not self._install([*install, *pins]):
                raise SandboxError("environment could not be reproduced: pinned npm install failed")

        if not requested:
            return {}, failed
        metadata = self._run([*command, "node", "-e", NPM_VERSION_CODE, "--", *requested])
        self._require_success(metadata, "npm version inspection")
        versions = json.loads(metadata.stdout)
        if not isinstance(versions, dict) or not all(
            package in requested and isinstance(version, str) and NPM_VERSION.fullmatch(version)
            for package, version in versions.items()
        ):
            raise SandboxError("Invalid npm version metadata")
        if baseline is not None and versions != baseline.npm_versions:
            raise SandboxError("environment could not be reproduced: installed npm versions differ")
        failed.extend(package for package in requested if package not in versions)
        return versions, failed

    def start(self, packages: list[str], npm_packages: list[str] | None = None) -> Environment:
        if self._container is not None:
            raise SandboxError("Sandbox already started")
        self._packages = list(packages)
        self._npm_packages = list(npm_packages or ())
        if self._deadline is None:
            self._deadline = time.monotonic() + self.settings.check_timeout_seconds
        valid, failed = [], []
        for package in dict.fromkeys(packages):
            (valid if PACKAGE_NAME.fullmatch(package) else failed).append(package)
        self._generation += 1
        self._container = f"worksfr-{self._check_id}-{self._generation}"
        name = self._container
        file_size_limit = self.settings.sandbox_file_size_mb * 1024 * 1024
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
                        "--memory-swap",
                        self.settings.sandbox_memory,
                        "--ulimit",
                        f"fsize={file_size_limit}:{file_size_limit}",
                        "--tmpfs",
                        f"/tmp:rw,exec,nosuid,nodev,size={self.settings.sandbox_tmpfs_mb}m,mode=1777",
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
            baseline = self._initial_environment if self._pinned_restart else None
            pip = ["docker", "exec", name, "pip", "install"]
            if baseline is not None:
                pins = [
                    f"{package}=={version}"
                    for package, version in baseline.versions.items()
                    if package not in baseline.failed_packages
                ]
                if pins and not self._install([*pip, *pins]):
                    raise SandboxError("environment could not be reproduced: pinned install failed")
                failed = list(baseline.failed_packages)
            else:
                failed.extend(self._failed_installs([*pip, "--upgrade"], valid))
            versions = self._run(["docker", "exec", name, "python", "-c", VERSION_CODE])
            self._require_success(versions, "Version inspection")
            installed = json.loads(versions.stdout)
            if not isinstance(installed, dict) or not all(
                isinstance(key, str) and isinstance(value, str) for key, value in installed.items()
            ):
                raise SandboxError("Invalid installed-version metadata")
            npm_versions, failed_npm = self._prepare_npm(name, self._npm_packages, baseline)
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
            normalized = {normalize(key): value for key, value in installed.items()}
            requested_versions = {
                package: normalized[normalize(package)]
                for package in valid
                if normalize(package) in normalized
            }
            if baseline is not None:
                if any(
                    requested_versions.get(key) != value for key, value in baseline.versions.items()
                ):
                    raise SandboxError(
                        "environment could not be reproduced: installed versions differ"
                    )
                return baseline.model_copy(deep=True)
            environment = Environment(
                versions=requested_versions,
                failed_packages=failed,
                npm_versions=npm_versions,
                failed_npm_packages=failed_npm,
            )
            if self._initial_environment is None:
                self._initial_environment = environment.model_copy(deep=True)
            return environment
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

    def exec(self, step_id: str, code: str, *, lang: str = "python") -> ExecResult:
        if lang == StepKind.REPL:
            lang = StepKind.PYTHON
        return self._request({"op": "exec", "id": step_id, "code": code, "lang": lang}, ExecResult)

    def probe(self, probe_id: str, target: str, name: str) -> ProbeResult:
        return self._request(
            {"op": "probe", "id": probe_id, "target": target, "name": name},
            ProbeResult,
        )

    def restart(self, replay: list[tuple[StepKind, str]]) -> None:
        packages = list(self._packages)
        npm_packages = list(self._npm_packages)
        self.close()
        self._pinned_restart = True
        try:
            self.start(packages, npm_packages)
        finally:
            self._pinned_restart = False
        for index, (kind, code) in enumerate(replay):
            self.exec(f"replay-{index}", code, lang=kind)

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
