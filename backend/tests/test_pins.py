from pathlib import Path

import pytest
from test_extract import FIXTURES, NAMES, _client

from app.extract import extract_url
from app.extract.libs import packages_from_pip_line, pins_from_pip_line
from app.main import create_app
from app.models import CheckReport, CheckStatus, SourceKind, StepKind, StepReport
from app.pins import find_install_pins
from app.storage import create_check, get_report, init_db


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("pip install pandas==0.24.2 matplotlib", [("pandas", "==0.24.2")]),
        (
            '!pip install -q "scikit-learn<1.2" numpy>=1.20,<2',
            [("scikit-learn", "<1.2"), ("numpy", ">=1.20,<2")],
        ),
        ("python -m pip install --upgrade pandas[excel]==2.0.3", [("pandas", "==2.0.3")]),
        (
            "%pip install \"Flask_Login>=1.0,<1.5; python_version < '3.12'\"",
            [("flask-login", ">=1.0,<1.5")],
        ),
        ("pip install -r requirements.txt", []),
        ("pip install ./pkg", []),
        ("pip install git+https://example.com/pkg.git", []),
        ("pip install pandas @ https://example.com/pkg.whl", []),
        ("pip install --index-url https://example.com/simple --timeout 30 numpy", []),
        ("# pip install pandas==1", []),
        ("pip install pandas==1 && pip install numpy<2", [("pandas", "==1"), ("numpy", "<2")]),
    ],
)
def test_pip_pins(line: str, expected: list[tuple[str, str]]) -> None:
    assert pins_from_pip_line(line) == expected
    assert all(name in packages_from_pip_line(line) for name, _ in expected)


def test_lines_continuations_order_and_versions() -> None:
    blocks = [
        (2, 'x = 1\n%pip install MISSING_pkg<2\n!pip install "pandas<2"'),
        (0, "pip install \\\n  pandas==1.5 numpy>=1,<2"),
    ]
    pins = find_install_pins(blocks, {"Pandas": "2.2.3", "numpy": "2.1.3"})
    assert [(p.step_idx, p.package, p.pinned, p.used) for p in pins] == [
        (0, "pandas", "==1.5", "2.2.3"),
        (0, "numpy", ">=1,<2", "2.1.3"),
        (2, "missing-pkg", "<2", None),
        (2, "pandas", "<2", "2.2.3"),
    ]
    assert pins[0].line == "pip install    pandas==1.5 numpy>=1,<2"
    assert pins[2].line == "%pip install MISSING_pkg<2"


def test_report_pins_round_trip(tmp_path: Path) -> None:
    db = str(tmp_path / "pins.db")
    init_db(db)
    code = "pip install pandas==0.24.2"
    create_check(
        db,
        CheckReport(
            id="pins",
            source_kind=SourceKind.URL,
            source="https://example.com",
            status=CheckStatus.DONE,
            versions={"pandas": "2.2.3"},
            steps=[StepReport(idx=0, kind=StepKind.SHELL, original_code=code, final_code=code)],
        ),
    )
    loaded = get_report(db, "pins")
    assert loaded is not None
    assert loaded.install_pins == find_install_pins([(0, code)], loaded.versions)
    assert loaded.install_pins[0].used == "2.2.3"


@pytest.mark.parametrize("name", NAMES)
def test_saved_fixtures_have_no_false_pins(name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    import json

    monkeypatch.setattr("app.extract.fetch.resolve_host", lambda _: ["93.184.216.34"])
    expected = json.loads((FIXTURES / f"{name}.expected.json").read_text(encoding="utf-8"))
    with _client(name, expected) as client:
        extraction = extract_url(expected["source_url"], client)
    assert find_install_pins(((b.idx, b.code) for b in extraction.blocks), {}) == []


def test_openapi_exposes_pins() -> None:
    from app.config import Settings

    schemas = create_app(Settings(_env_file=None, providers=[])).openapi()["components"]["schemas"]
    assert "InstallPin" in schemas
    assert "install_pins" in schemas["CheckReport"]["properties"]
    assert "install_pins" in schemas["EnvironmentEvent"]["properties"]


def test_pipeline_emits_install_pins() -> None:
    import httpx
    import nbformat
    from support.local_sandbox import LocalRunnerSandbox
    from test_pipeline import ROOT, Cache, Proposer, Sink

    from app.config import Settings
    from app.pipeline import PipelineDeps, run_check

    settings = Settings(_env_file=None, providers=[])
    data = nbformat.writes(
        nbformat.v4.new_notebook(
            cells=[
                nbformat.v4.new_code_cell("!pip install nbformat==1.0\nx = 1"),
            ]
        )
    ).encode()
    sink = Sink()
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(404))) as http:
        deps = PipelineDeps(
            settings,
            lambda _: LocalRunnerSandbox(settings, runner_path=ROOT / "sandbox/runner.py"),
            Proposer(),
            http,
            Cache(),
        )
        run_check("pins", ("pins.ipynb", data), deps, sink)
    assert sink.events[-1].type == "done"
    environment = next(event for event in sink.events if event.type == "environment")
    assert len(environment.install_pins) == 1
    pin = environment.install_pins[0]
    assert (pin.step_idx, pin.package, pin.pinned) == (0, "nbformat", "==1.0")
    assert pin.used == environment.versions["nbformat"]
