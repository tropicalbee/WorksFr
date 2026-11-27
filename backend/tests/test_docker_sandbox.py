import json
import queue
import subprocess
from typing import Any

import pytest

from app.config import Settings
from app.models import ExecResult, StepKind
from app.sandbox.docker_sandbox import MAX_REPLY_BYTES, DockerSandbox
from app.sandbox.interface import SandboxError, SandboxTampering, StepTimeoutError


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
            response["nonce"] = request["nonce"]
            self.process.stdout.lines.put(json.dumps(response).encode() + b"\n")
        if self.process.extra_reply:
            self.process.stdout.lines.put(self.process.extra_reply)
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
        self.extra_reply: bytes | None = None
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
        self.npm_install_failures: set[tuple[str, ...]] = set()
        self.npm_versions: dict[str, str] = {}

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
        if args[5:7] == ["npm", "install"]:
            packages = tuple(args[args.index("--no-fund") + 1 :])
            status = int(packages in self.npm_install_failures)
        if args[5:7] == ["node", "-e"]:
            output = json.dumps(
                {name: self.npm_versions[name] for name in args[9:] if name in self.npm_versions}
            )
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
        "--memory-swap": "2g",
        "--ulimit": "fsize=268435456:268435456",
        "--tmpfs": "/tmp:rw,exec,nosuid,nodev,size=512m,mode=1777",
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


def test_storage_limits_use_settings(sandbox: DockerSandbox, commands: Commands) -> None:
    sandbox.settings.sandbox_memory = "1g"
    sandbox.settings.sandbox_tmpfs_mb = 320
    sandbox.settings.sandbox_file_size_mb = 128
    sandbox.start([])
    run = commands.calls[0]
    assert run[run.index("--memory") + 1] == run[run.index("--memory-swap") + 1] == "1g"
    assert run[run.index("--ulimit") + 1] == "fsize=134217728:134217728"
    assert run[run.index("--tmpfs") + 1] == "/tmp:rw,exec,nosuid,nodev,size=320m,mode=1777"
    sandbox.close()


@pytest.mark.parametrize(
    ("gpus", "gpu_image", "image", "argument"),
    [
        ("", "custom-gpu", "worksfr-sandbox", None),
        ("all", "", "worksfr-sandbox", "all"),
        ("all", "custom-gpu", "custom-gpu", "all"),
        ("device=0,2", "custom-gpu", "custom-gpu", '"device=0,2"'),
    ],
)
def test_gpu_container_arguments(
    commands: Commands, gpus: str, gpu_image: str, image: str, argument: str | None
) -> None:
    settings = Settings(_env_file=None, sandbox_gpus=gpus, sandbox_gpu_image=gpu_image)
    sandbox = DockerSandbox(settings, "gpu-test", run=commands)
    try:
        sandbox.start([])
        run = commands.calls[0]
        assert run[-3:] == [image, "sleep", "infinity"]
        if argument is None:
            assert "--gpus" not in run
        else:
            assert run[run.index("--gpus") + 1] == argument
    finally:
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


def test_npm_install_and_version_inspection_precede_disconnect(
    sandbox: DockerSandbox, commands: Commands
) -> None:
    commands.npm_versions = {"lodash": "4.17.21", "@scope/name": "1.0.0-rc.1", "other": "1.0.0"}
    env = sandbox.start(["pandas"], npm_packages=["lodash", "@scope/name"])
    assert env.versions == {"pandas": "2.2.3"}
    assert env.npm_versions == {"lodash": "4.17.21", "@scope/name": "1.0.0-rc.1"}
    assert env.failed_npm_packages == []
    install = next(i for i, args in enumerate(commands.calls) if args[5:7] == ["npm", "install"])
    inspect = next(i for i, args in enumerate(commands.calls) if args[5:7] == ["node", "-e"])
    disconnect = next(i for i, args in enumerate(commands.calls) if "disconnect" in args)
    assert install < inspect < disconnect < len(commands.calls) - 1
    assert commands.calls[install][1:] == [
        "exec",
        "-w",
        "/home/sandbox/work",
        "worksfr-check123-1",
        "npm",
        "install",
        "--ignore-scripts",
        "--no-audit",
        "--no-fund",
        "lodash",
        "@scope/name",
    ]
    sandbox.close()


def test_invalid_npm_names_and_install_fallback(sandbox: DockerSandbox, commands: Commands) -> None:
    invalid = [
        "--global",
        "./local",
        "../relative",
        "/absolute/path",
        "git+https://a/b",
        "file:./local",
        "name@1.0.0",
        "@scope/..",
        "@../name",
        "@scope/a/b",
        "name\n",
        "owner/repo",
        "x y",
        "x;y",
        r"some\path",
        "",
    ]
    commands.npm_install_failures = {("lodash", "missing"), ("missing",)}
    commands.npm_versions = {"lodash": "4.17.21"}
    env = sandbox.start([], npm_packages=["lodash", "missing", *invalid, "lodash"])
    assert env.npm_versions == {"lodash": "4.17.21"}
    assert env.failed_npm_packages == [*invalid, "missing"]
    installs = [args for args in commands.calls if args[5:7] == ["npm", "install"]]
    assert all("--ignore-scripts" in args for args in installs)
    assert [args[args.index("--no-fund") + 1 :] for args in installs] == [
        ["lodash", "missing"],
        ["lodash"],
        ["missing"],
    ]
    assert all(value not in args for args in commands.calls for value in invalid)
    sandbox.close()


def test_exec_and_probe_round_trip(sandbox: DockerSandbox, commands: Commands) -> None:
    env = sandbox.start([])
    assert env.npm_versions == {}
    assert env.failed_npm_packages == []
    assert not any(args[5:7] in (["npm", "install"], ["node", "-e"]) for args in commands.calls)
    result = sandbox.exec("s1", "print('hello')")
    assert isinstance(result, ExecResult)
    assert result.stdout == "hello"
    assert sandbox.probe("p1", "json", "dump").target_found
    request = dict(commands.processes[0].requests[0])
    assert len(request.pop("nonce")) == 32
    assert request == {
        "op": "exec",
        "id": "s1",
        "code": "print('hello')",
        "lang": "python",
    }
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


@pytest.mark.parametrize("fault", ["extra", "wrong-id", "wrong-nonce", "missing-nonce", "json"])
def test_runner_rejects_invalid_reply_envelopes(
    sandbox: DockerSandbox, commands: Commands, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    monkeypatch.setattr("app.sandbox.docker_sandbox.token_hex", lambda _: "test-nonce")
    sandbox.start([])
    process = commands.processes[0]
    if fault == "extra":
        process.extra_reply = b"unexpected test stream record\n"
    elif fault == "json":
        process.reply = b"invalid json\n"
    else:
        envelope = {"status": "ok", "id": "different" if fault == "wrong-id" else "s1"}
        if fault != "missing-nonce":
            envelope["nonce"] = "different" if fault == "wrong-nonce" else "test-nonce"
        process.reply = json.dumps(envelope).encode() + b"\n"
    with pytest.raises(SandboxTampering):
        sandbox.exec("s1", "42")
    assert any(args[1] == "kill" for args in commands.calls)
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
    sandbox.restart([(StepKind.REPL, "x = 1"), (StepKind.JAVASCRIPT, "let x = 2;")])
    assert commands.processes[0].killed
    requests = commands.processes[1].requests
    assert [(request["lang"], request["code"]) for request in requests] == [
        ("python", "x = 1"),
        ("javascript", "let x = 2;"),
    ]
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


def test_restart_installs_npm_pins_and_skips_failed(
    sandbox: DockerSandbox, commands: Commands
) -> None:
    commands.npm_install_failures = {("@scope/name", "missing"), ("missing",)}
    commands.npm_versions = {"@scope/name": "1.0.0-rc.1+build.2"}
    first = sandbox.start([], npm_packages=["@scope/name", "missing"])
    commands.calls.clear()
    sandbox.restart([])
    [install] = [args for args in commands.calls if args[5:7] == ["npm", "install"]]
    assert "--ignore-scripts" in install
    assert install[install.index("--no-fund") + 1 :] == ["@scope/name@1.0.0-rc.1+build.2"]
    assert not any("missing" in args for args in commands.calls)
    assert first.failed_npm_packages == ["missing"]
    sandbox.close()


@pytest.mark.parametrize("installed", [{}, {"lodash": "4.17.20"}])
def test_restart_checks_npm_versions(
    sandbox: DockerSandbox, commands: Commands, installed: dict[str, str]
) -> None:
    commands.npm_versions = {"lodash": "4.17.21"}
    sandbox.start([], npm_packages=["lodash"])
    commands.npm_versions = installed
    with pytest.raises(SandboxError, match="environment could not be reproduced"):
        sandbox.restart([])
    assert commands.calls[-1][1:3] == ["rm", "-f"]


def test_invalid_npm_version_cannot_form_install_spec(
    sandbox: DockerSandbox, commands: Commands
) -> None:
    commands.npm_versions = {"lodash": "file:../other"}
    with pytest.raises(SandboxError, match="Invalid npm version metadata"):
        sandbox.start([], npm_packages=["lodash"])
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
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.sandbox.docker_sandbox.token_hex", lambda _: "test-nonce")
    sandbox.start([])
    commands.processes[0].reply = (
        json.dumps(
            {
                "id": "r1",
                "nonce": "test-nonce",
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


def test_expired_deadline_raises_time_limit_not_install_failure() -> None:
    import time

    from app.sandbox.interface import SandboxTimeLimit

    sandbox = DockerSandbox(Settings(_env_file=None), "c1", Commands())
    sandbox._deadline = time.monotonic() - 1
    with pytest.raises(SandboxTimeLimit):
        sandbox.start(["pandas"])
