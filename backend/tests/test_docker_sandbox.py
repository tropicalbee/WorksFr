import json
import queue
import subprocess
from typing import Any

import pytest

from app.config import Settings
from app.models import ExecResult
from app.sandbox.docker_sandbox import MAX_REPLY_BYTES, DockerSandbox
from app.sandbox.interface import SandboxError, StepTimeoutError


class Output:
    def __init__(self) -> None:
        self.lines: queue.Queue[bytes] = queue.Queue()
        self.lines.put(b'{"op":"ready","python":"3.12.0"}\n')

    def readline(self, limit: int) -> bytes:
        return self.lines.get()[:limit]

    def close(self) -> None:
        self.lines.put(b"")


class Input:
    def __init__(self, process: "Process") -> None:
        self.process = process

    def write(self, line: bytes) -> int:
        request = json.loads(line)
        self.process.requests.append(request)
        if self.process.reply is not None:
            reply = self.process.reply
            self.process.stdout.lines.put(reply)
        elif not self.process.silent:
            if request["op"] == "exec":
                response = {"id": request["id"], "status": "ok", "stdout": "hello"}
            else:
                response = {"id": request["id"], "status": "ok", "target_found": True}
            self.process.stdout.lines.put(json.dumps(response).encode() + b"\n")
        return len(line)

    def flush(self) -> None:
        pass

    def close(self) -> None:
        pass


class Process:
    def __init__(self) -> None:
        self.stdout = Output()
        self.stdin = Input(self)
        self.requests: list[dict[str, Any]] = []
        self.reply: bytes | None = None
        self.silent = False
        self.killed = False

    def kill(self) -> None:
        self.killed = True
        self.stdout.close()

    def wait(self, timeout: float) -> int:
        return 0


class Commands:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.processes: list[Process] = []
        self.disconnect_fails = False
        self.install_failures: set[tuple[str, ...]] = set()

    def run(self, args: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
        self.calls.append(args)
        status, output = 0, ""
        if args[1:3] == ["network", "disconnect"] and self.disconnect_fails:
            status = 1
        if "pip" in args:
            packages = tuple(args[args.index("install") + 1 :])
            if packages and packages[0] == "--upgrade":
                packages = packages[1:]
            status = int(packages in self.install_failures)
        if "-c" in args:
            output = '{"pandas":"2.2.3"}'
        return subprocess.CompletedProcess(args, status, output, "")

    def popen(self, args: list[str]) -> Process:
        self.calls.append(args)
        process = Process()
        self.processes.append(process)
        return process


@pytest.fixture
def commands() -> Commands:
    return Commands()


@pytest.fixture
def sandbox(commands: Commands, monkeypatch: pytest.MonkeyPatch) -> DockerSandbox:
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)
    return DockerSandbox(Settings(_env_file=None), "check123", run=commands)


def test_limits_and_preparation_order(sandbox: DockerSandbox, commands: Commands) -> None:
    env = sandbox.start(["pandas"])
    assert env.versions == {"pandas": "2.2.3"}
    run = commands.calls[0]
    for flag, value in {
        "--memory": "2g",
        "--cpus": "2.0",
        "--pids-limit": "256",
        "--cap-drop": "ALL",
        "--security-opt": "no-new-privileges",
        "--user": "sandbox",
        "--label": "worksfr=1",
    }.items():
        assert run[run.index(flag) + 1] == value
    assert run[-3:] == ["worksfr-sandbox", "sleep", "infinity"]
    install = next(i for i, args in enumerate(commands.calls) if "pip" in args)
    disconnect = next(i for i, args in enumerate(commands.calls) if "disconnect" in args)
    assert install < disconnect < len(commands.calls) - 1
    assert commands.calls[-1][1:3] == ["exec", "-i"]
    sandbox.close()


def test_disconnect_failure_cleans_up(sandbox: DockerSandbox, commands: Commands) -> None:
    commands.disconnect_fails = True
    with pytest.raises(SandboxError):
        sandbox.start([])
    assert not commands.processes
    assert any(args[1:3] == ["rm", "-f"] for args in commands.calls)


def test_invalid_packages_and_install_fallback(sandbox: DockerSandbox, commands: Commands) -> None:
    invalid = ["--evil", "a;bad", "name==1", "x y", "", "a\n"]
    commands.install_failures = {("pandas", "missing"), ("missing",)}
    env = sandbox.start(["pandas", "missing", *invalid])
    assert env.failed_packages == [*invalid, "missing"]
    pip_calls = [args for args in commands.calls if "pip" in args]
    assert len(pip_calls) == 3
    for args in commands.calls:
        assert not any(value in args for value in invalid)
    sandbox.close()


def test_exec_and_probe_round_trip(sandbox: DockerSandbox, commands: Commands) -> None:
    sandbox.start([])
    result = sandbox.exec("s1", "print('hello')")
    assert isinstance(result, ExecResult)
    assert result.stdout == "hello"
    assert sandbox.probe("p1", "json", "dump").target_found
    assert commands.processes[0].requests[0] == {"op": "exec", "id": "s1", "code": "print('hello')"}
    sandbox.close()


@pytest.mark.parametrize(
    "reply",
    [
        b"x" * (MAX_REPLY_BYTES + 1),
        b"not json\n",
        b'{"id":"wrong","status":"ok"}\n',
        b'{"id":"s1","status":"invalid"}\n',
        b"",
    ],
)
def test_bad_runner_replies(sandbox: DockerSandbox, commands: Commands, reply: bytes) -> None:
    sandbox.start([])
    commands.processes[0].reply = reply
    with pytest.raises(SandboxError):
        sandbox.exec("s1", "42")
    sandbox.close()


def test_step_timeout_kills_container(sandbox: DockerSandbox, commands: Commands) -> None:
    sandbox.settings.step_timeout_seconds = 0.02
    sandbox.start([])
    commands.processes[0].silent = True
    with pytest.raises(StepTimeoutError):
        sandbox.exec("s1", "while True: pass")
    assert any(args[1] == "kill" for args in commands.calls)
    sandbox.close()


def test_restart_replays_and_close_is_idempotent(
    sandbox: DockerSandbox, commands: Commands
) -> None:
    sandbox.start(["pandas"])
    sandbox.restart(["x = 1", "x + 1"])
    assert commands.processes[0].killed
    assert [request["code"] for request in commands.processes[1].requests] == ["x = 1", "x + 1"]
    names = [args[args.index("--name") + 1] for args in commands.calls if "--name" in args]
    assert len(set(names)) == 2
    sandbox.close()
    count = len(commands.calls)
    sandbox.close()
    assert len(commands.calls) == count


def test_context_manager_closes(sandbox: DockerSandbox, commands: Commands) -> None:
    with sandbox:
        sandbox.start([])
    assert commands.processes[0].killed


def test_restart_installs_pins_and_skips_failed(sandbox: DockerSandbox, commands: Commands) -> None:
    commands.install_failures = {("pandas", "missing"), ("missing",)}
    first = sandbox.start(["pandas", "missing"])
    commands.calls.clear()
    sandbox.restart([])
    installs = [args for args in commands.calls if "pip" in args]
    assert len(installs) == 1
    assert installs[0][-1] == "pandas==2.2.3"
    assert "--upgrade" not in installs[0]
    assert not any("missing" in args for args in commands.calls)
    assert first.failed_packages == ["missing"]
    sandbox.close()


def test_failed_pin_cleans_up(sandbox: DockerSandbox, commands: Commands) -> None:
    sandbox.start(["pandas"])
    commands.install_failures.add(("pandas==2.2.3",))
    with pytest.raises(SandboxError, match="environment could not be reproduced"):
        sandbox.restart([])
    assert commands.calls[-1][1:3] == ["rm", "-f"]


def test_versions_only_include_requested_normalized_names(
    sandbox: DockerSandbox,
    commands: Commands,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = commands.run

    def metadata(args: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
        result = original(args, timeout)
        if "-c" in args:
            result.stdout = '{"Scikit_Learn":"1.7.0","pip":"25.0","other":"9"}'
        return result

    monkeypatch.setattr(commands, "run", metadata)
    env = sandbox.start(["scikit-learn", "absent"])
    assert env.versions == {"scikit-learn": "1.7.0"}
    sandbox.close()


@pytest.mark.parametrize("operation", ["exec", "probe"])
def test_protocol_errors_are_infrastructure_failures(
    sandbox: DockerSandbox,
    commands: Commands,
    operation: str,
) -> None:
    sandbox.start([])
    commands.processes[0].reply = (
        json.dumps(
            {
                "id": "r1",
                "status": "error",
                "error": {"type": "ProtocolError", "message": "bad request"},
            }
        ).encode()
        + b"\n"
    )
    with pytest.raises(SandboxError, match="protocol"):
        if operation == "exec":
            sandbox.exec("r1", "42")
        else:
            sandbox.probe("r1", "json", "dump")
    assert any(args[1] == "kill" for args in commands.calls)
    sandbox.close()


def test_install_timeout_falls_back(
    sandbox: DockerSandbox, commands: Commands, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = commands.run

    def timed(args: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
        if "pip" in args and args[-2:] == ["pandas", "missing"]:
            raise subprocess.TimeoutExpired(args, timeout)
        return original(args, timeout)

    monkeypatch.setattr(commands, "run", timed)
    commands.install_failures.add(("missing",))
    assert sandbox.start(["pandas", "missing"]).failed_packages == ["missing"]
    sandbox.close()


def test_start_failure_cleans_up(
    sandbox: DockerSandbox, commands: Commands, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(args: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
        commands.calls.append(args)
        raise OSError("Docker missing")

    monkeypatch.setattr(commands, "run", fail)
    with pytest.raises(SandboxError, match="Docker command failed"):
        sandbox.start([])
    assert commands.calls[-1][1:3] == ["rm", "-f"]
    sandbox.close()
