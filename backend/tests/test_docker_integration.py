"""Real-container checks; skipped when the Docker engine or sandbox image is absent."""

import builtins
import json
import socket
import subprocess
import time
from collections.abc import Iterator
from uuid import uuid4

import pytest

from app.config import Settings
from app.models import Block, Label, StepKind
from app.sandbox.docker_sandbox import DockerSandbox
from app.sandbox.interface import StepTimeoutError
from app.session import CheckSession
from app.step import Step


def docker(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )


@pytest.fixture(scope="module", autouse=True)
def require_docker() -> None:
    for arguments, reason in (
        (("info",), "Docker engine unavailable (docker info failed)"),
        (("image", "inspect", "worksfr-sandbox"), "Docker image worksfr-sandbox is unavailable"),
    ):
        try:
            available = docker(*arguments).returncode == 0
        except (OSError, subprocess.SubprocessError):
            available = False
        if not available:
            pytest.skip(reason)


@pytest.fixture
def sandbox(monkeypatch: pytest.MonkeyPatch) -> Iterator[DockerSandbox]:
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)
    with DockerSandbox(Settings(_env_file=None), f"integration-{uuid4().hex}") as instance:
        yield instance


@pytest.mark.docker
def test_steps_share_namespace(sandbox: DockerSandbox) -> None:
    sandbox.start([])
    first = sandbox.exec("s1", "x = 41")
    second = sandbox.exec("s2", "print(x + 1)")
    assert first.status == "ok"
    assert second.status == "ok"
    assert second.stdout.strip() == "42"


@pytest.mark.docker
def test_probe_finds_close_dataframe_method(sandbox: DockerSandbox) -> None:
    sandbox.start([])
    assert sandbox.exec("s1", "import pandas as pd").status == "ok"
    result = sandbox.probe("p1", "pandas.DataFrame", "to_dikt")
    assert result.status == "ok"
    assert result.target_found is True
    assert result.exists is False
    assert "to_dict" in result.close_matches


@pytest.mark.docker
def test_network_and_dns_are_disconnected(sandbox: DockerSandbox) -> None:
    sandbox.start([])
    connection = sandbox.exec(
        "s1", "import socket\nsocket.create_connection(('1.1.1.1', 53), timeout=5)"
    )
    dns = sandbox.exec("s2", "socket.getaddrinfo('pypi.org', 443)")
    for result in (connection, dns):
        assert result.status == "error"
        assert result.error is not None
        error_class = getattr(socket, result.error.type, getattr(builtins, result.error.type, None))
        assert isinstance(error_class, type) and issubclass(error_class, OSError), result.error


@pytest.mark.docker
def test_timeout_kills_step_and_restart_replays_namespace(sandbox: DockerSandbox) -> None:
    sandbox.settings.step_timeout_seconds = 3
    sandbox.start([])
    started = time.monotonic()
    with pytest.raises(StepTimeoutError):
        sandbox.exec("s1", "import time\ntime.sleep(30)")
    assert time.monotonic() - started < 10
    sandbox.restart([(StepKind.PYTHON, "x = 1")])
    result = sandbox.exec("s3", "print(x)")
    assert result.status == "ok"
    assert result.stdout.strip() == "1"


@pytest.mark.docker
def test_container_applies_limits_and_non_root_user(sandbox: DockerSandbox) -> None:
    sandbox.start([])
    assert sandbox._container is not None
    inspected = docker("inspect", sandbox._container)
    assert inspected.returncode == 0, inspected.stderr
    container = json.loads(inspected.stdout)[0]
    limits = container["HostConfig"]
    settings = sandbox.settings
    assert limits["Memory"] == int(settings.sandbox_memory.removesuffix("g")) * 1024**3
    assert limits["NanoCpus"] == int(settings.sandbox_cpus * 1_000_000_000)
    assert limits["PidsLimit"] == settings.sandbox_pids_limit
    assert container["Config"]["User"] == "sandbox"
    assert [cap.upper() for cap in limits["CapDrop"]] == ["ALL"]
    assert "no-new-privileges" in limits["SecurityOpt"]
    result = sandbox.exec("s1", "import os\nprint(os.getuid())")
    assert result.status == "ok"
    assert int(result.stdout.strip()) != 0


@pytest.mark.docker
def test_close_removes_container_and_is_idempotent(sandbox: DockerSandbox) -> None:
    sandbox.start([])
    name = sandbox._container
    assert name is not None
    sandbox.close()
    remaining = docker("ps", "-a", "--filter", f"name=^/{name}$", "--format", "{{.Names}}")
    assert remaining.returncode == 0, remaining.stderr
    assert remaining.stdout.strip() == ""
    sandbox.close()


@pytest.mark.docker
def test_javascript_steps_share_state(sandbox: DockerSandbox) -> None:
    sandbox.start([])
    first = sandbox.exec("s1", "const x = 41; console.log(x);", lang="javascript")
    second = sandbox.exec("s2", "console.log(x + 1);", lang="javascript")
    assert first.status == "ok", first.error
    assert first.stdout.strip() == "41"
    assert second.status == "ok", second.error
    assert second.stdout.strip() == "42"


@pytest.mark.docker
def test_javascript_full_file_can_repeat_const_declarations(sandbox: DockerSandbox) -> None:
    declarations = "const settings = {base: 40};\nconst app = {value: settings.base + 2};"
    steps = (
        (declarations, ""),
        (f"{declarations}\nconsole.log(app.value);", "42"),
        ("console.log(app.value + 1);", "43"),
    )
    with CheckSession(sandbox, sandbox.settings) as session:
        session.start([])
        for idx, (code, stdout) in enumerate(steps):
            step = Step(Block(idx=idx, kind=StepKind.JAVASCRIPT, code=code, runnable_code=code))
            assert session.run(step).label == Label.WORKS, step.result
            assert step.result is not None
            assert step.result.stdout.strip() == stdout


@pytest.mark.docker
def test_typescript_runs_type_annotations(sandbox: DockerSandbox) -> None:
    sandbox.start([])
    result = sandbox.exec("s1", "const x: number = 42; console.log(x);", lang="typescript")
    assert result.status == "ok", result.error
    assert result.stdout.strip() == "42"


@pytest.mark.docker
def test_javascript_imports_installed_npm_package(sandbox: DockerSandbox) -> None:
    environment = sandbox.start([], npm_packages=["lodash"])
    assert "lodash" in environment.npm_versions
    assert environment.failed_npm_packages == []
    result = sandbox.exec(
        "s1", "import _ from 'lodash'; console.log(_.sum([20, 22]));", lang="javascript"
    )
    assert result.status == "ok", result.error
    assert result.stdout.strip() == "42"


@pytest.mark.docker
def test_removed_node_api_fails(sandbox: DockerSandbox) -> None:
    sandbox.start([])
    result = sandbox.exec("s1", "require('node:util').isBoolean(true);", lang="javascript")
    assert result.status == "error"
    assert result.error is not None
    assert result.error.type == "TypeError"
    assert "isBoolean" in result.error.message


@pytest.mark.docker
def test_c_compiles_and_runs_main(sandbox: DockerSandbox) -> None:
    sandbox.start([])
    result = sandbox.exec(
        "s1", '#include <stdio.h>\nint main(void) { puts("hello"); return 0; }', lang="c"
    )
    assert result.status == "ok", result.error
    assert result.stdout.strip() == "hello"


@pytest.mark.docker
def test_c_full_program_can_repeat_function_definition(sandbox: DockerSandbox) -> None:
    definition = "int add(int a, int b) { return a + b; }"
    main = 'int main(void) { printf("%d\\n", add(20, 22)); }'
    program = f"#include <stdio.h>\n{definition}\n{main}"
    # A definition with no main() only compiles, so it is Can't verify (ARCHITECTURE, Languages).
    steps = (
        (definition, "", Label.CANT_VERIFY),
        (program, "42", Label.WORKS),
        ('int main(void) { printf("%d\\n", add(20, 23)); }', "43", Label.WORKS),
    )
    with CheckSession(sandbox, sandbox.settings) as session:
        session.start([])
        for idx, (code, stdout, label) in enumerate(steps):
            step = Step(Block(idx=idx, kind=StepKind.C, code=code, runnable_code=code))
            assert session.run(step).label == label, step.result
            assert step.result is not None
            assert step.result.stdout.strip() == stdout


@pytest.mark.docker
def test_cpp_step_can_call_function_from_earlier_step(sandbox: DockerSandbox) -> None:
    sandbox.start([])
    first = sandbox.exec("s1", "int answer() { return 42; }", lang="cpp")
    second = sandbox.exec(
        "s2", '#include <iostream>\nint main() { std::cout << answer() << "\\n"; }', lang="cpp"
    )
    assert first.status == "ok", first.error
    assert second.status == "ok", second.error
    assert second.stdout.strip() == "42"


@pytest.mark.docker
def test_c_wraps_bare_statement(sandbox: DockerSandbox) -> None:
    sandbox.start([])
    first = sandbox.exec("s1", "#include <stdio.h>", lang="c")
    second = sandbox.exec("s2", 'printf("%d\\n", 1 + 1);', lang="c")
    assert first.status == "ok", first.error
    assert second.status == "ok", second.error
    assert second.stdout.strip() == "2"


@pytest.mark.docker
def test_c_system_header_is_skipped_before_running(sandbox: DockerSandbox) -> None:
    code = "#include <curl/curl.h>\nint main(void) { return 0; }"
    step = Step(Block(idx=0, kind=StepKind.C, code=code, runnable_code=code))
    with CheckSession(sandbox, sandbox.settings, c_system_headers=["curl/curl.h"]) as session:
        session.start([])
        decision = session.run(step)
        assert session.ledger == []
    assert decision.label == Label.CANT_VERIFY
    assert decision.reason == "needs the system library curl/curl.h"
    assert step.result is None
