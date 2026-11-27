import json
from pathlib import Path

import httpx
import pytest
from test_docker_sandbox import Commands
from test_pipeline import Cache

from app.config import Settings
from app.labels import label_step
from app.models import ErrorInfo, ExecResult, Label
from app.package_size import package_size
from app.sandbox.docker_sandbox import DockerSandbox


@pytest.fixture(autouse=True)
def public_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.extract.fetch.resolve_host", lambda _: ["93.184.216.34"])


@pytest.mark.parametrize("sdist_only", [False, True])
def test_wheel_selection(sdist_only: bool) -> None:
    fixture = Path(__file__).parent / "fixtures/pypi/example_wheels.json"
    data = json.loads(fixture.read_bytes())
    if sdist_only:
        data["releases"]["2.0"] = [data["releases"]["2.0"][2]]
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=data))
    ) as http:
        assert package_size("example", http, Cache()) == (None if sdist_only else 312)


def test_configured_simple_html_uses_wheel_size() -> None:
    filename = "example-1.0-cp312-cp312-linux_x86_64.whl"

    def respond(request: httpx.Request) -> httpx.Response:
        if request.method == "HEAD":
            assert request.url.path == f"/{filename}"
            return httpx.Response(200, headers={"content-length": "1234"})
        assert request.url.path == "/cpu/example/"
        return httpx.Response(200, text=f'<a href="/{filename}">{filename}</a>')

    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        assert package_size("example", http, Cache(), "https://wheels.example/cpu") == 1234


def test_indexes_and_skips_use_normalized_names(caplog: pytest.LogCaptureFixture) -> None:
    settings = Settings(
        _env_file=None,
        sandbox_max_package_mb=400,
        sandbox_package_indexes="Pandas=https://wheels.example/cpu,malformed",
    )
    assert settings.sandbox_package_indexes == {"pandas": "https://wheels.example/cpu"}
    assert "Ignoring malformed sandbox package index entry" in caplog.text
    assert Settings(_env_file=None, sandbox_package_indexes="").sandbox_package_indexes == {}
    commands = Commands()
    sizes = {"big": 401 * 1024**2, "pandas": 50 * 1024**2}
    sandbox = DockerSandbox(
        settings,
        "package-size",
        run=commands,
        measure_package=lambda package, _: sizes.get(package),
    )
    try:
        env = sandbox.start(["big", "pandas", "unknown"])
        assert env.failed_packages == ["big"]
        assert env.skipped_packages == {"big": 401}
        calls = [args for args in commands.calls if "pip" in args]
        assert len(calls) == 2
        assert any(args[-1] == "unknown" and "--index-url" not in args for args in calls)
        indexed = next(args for args in calls if args[-1] == "pandas")
        assert indexed[indexed.index("--index-url") + 1] == "https://wheels.example/cpu"
        assert "--extra-index-url" in indexed
        sandbox.restart([])
        pins = next(args for args in commands.calls if args[-1] == "pandas==2.2.3")
        assert "--index-url" in pins
    finally:
        sandbox.close()

    result = ExecResult(
        id="1",
        status="error",
        error=ErrorInfo(type="ModuleNotFoundError", message="No module named 'big'"),
    )
    label = label_step(
        result, failed_packages=env.failed_packages, skipped_packages=env.skipped_packages
    )
    assert label.label == Label.CANT_VERIFY
    assert label.reason == "needs big, which is too large for the sandbox (401 MB)"
