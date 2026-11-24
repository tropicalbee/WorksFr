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
from app.sandbox.docker_sandbox import DockerSandbox
from app.sandbox.interface import StepTimeoutError


def docker(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )


for arguments, reason in (
    (("info",), "Docker engine unavailable (docker info failed)"),
    (("image", "inspect", "worksfr-sandbox"), "Docker image worksfr-sandbox is unavailable"),
):
    try:
        available = docker(*arguments).returncode == 0
    except (OSError, subprocess.SubprocessError):
        available = False
    if not available:
        pytest.skip(reason, allow_module_level=True)


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
    sandbox.restart(["x = 1"])
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
