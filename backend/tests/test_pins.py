import json
from pathlib import Path

import pytest
from test_extract import FIXTURES, NAMES, _client

from app.extract import extract_url
from app.extract.libs import packages_from_pip_line, pins_from_pip_line
from app.extract.video import parse_reply
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


def test_pipeline_reports_and_persists_npm_versions_and_pins(tmp_path: Path) -> None:
    from unittest.mock import Mock

    import httpx
    from test_pipeline import Cache, Proposer

    from app.broker import Broker
    from app.config import Settings
    from app.events import EnvironmentEvent
    from app.extract import PastedCode
    from app.models import Environment, ExecResult
    from app.pipeline import PipelineDeps, run_check
    from app.recorder import RecorderSink
    from app.sandbox.interface import Sandbox
    from app.storage import list_events

    db = str(tmp_path / "npm.db")
    settings = Settings(_env_file=None, providers=[])
    init_db(db)
    create_check(
        db,
        CheckReport(
            id="npm", source_kind=SourceKind.PASTE, source="Pasted code", status=CheckStatus.RUNNING
        ),
    )
    sandbox = Mock(spec=Sandbox)
    sandbox.start.return_value = Environment(
        versions={"openai": "2.0.0"},
        failed_packages=["shared-missing"],
        npm_versions={"openai": "5.0.0", "express": "5.1.0"},
        failed_npm_packages=["shared-missing", "node-missing"],
    )
    sandbox.exec.return_value = ExecResult(id="s1", status="ok")
    source = PastedCode(
        "```bash\npip install openai==1.0.0\n"
        "npm i openai@4.0.0 express@4.17.0 node-missing@^1\n```\n"
        "```js\nconsole.log('ready');\n```"
    )
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: pytest.fail("Unexpected HTTP"))
    ) as http:
        deps = PipelineDeps(settings, lambda _: sandbox, Proposer(), http, Cache())
        run_check("npm", source, deps, RecorderSink(db, "npm", Broker()))

    [event_json] = [payload for _, kind, payload in list_events(db, "npm") if kind == "environment"]
    event = EnvironmentEvent.model_validate_json(event_json)
    assert event.versions == {"openai": "2.0.0", "npm:openai": "5.0.0", "express": "5.1.0"}
    assert set(event.failed_packages) == {"shared-missing", "npm:shared-missing", "node-missing"}
    assert [(p.ecosystem, p.package, p.pinned, p.used) for p in event.install_pins] == [
        ("pypi", "openai", "==1.0.0", "2.0.0"),
        ("npm", "openai", "4.0.0", "5.0.0"),
        ("npm", "express", "4.17.0", "5.1.0"),
        ("npm", "node-missing", "^1", None),
    ]

    report = get_report(db, "npm")
    assert report is not None and report.status == CheckStatus.DONE
    assert report.versions == event.versions
    assert report.failed_packages == event.failed_packages
    assert report.install_pins == event.install_pins


def test_saved_video_output_pins_only_tutorial_packages() -> None:
    reply = parse_reply(json.loads((FIXTURES / "video" / "CbpsDMwFG2g_300-600.json").read_text()))
    pins = find_install_pins(
        [],
        {"openai": "2.0"},
        output_blocks=(
            (idx, block.code) for idx, block in enumerate(reply.blocks) if block.kind == "output"
        ),
        packages=["openai", "pandas", "numpy", "python-dotenv"],
    )
    openai = next(pin for pin in pins if pin.package == "openai")
    assert (openai.pinned, openai.origin, openai.used, openai.step_idx) == (
        "==0.27.8",
        "pip_output",
        "2.0",
        3,
    )
    assert openai.line.startswith("Requirement already satisfied: openai in ")
    assert not {"yarl", "frozenlist"}.intersection(pin.package for pin in pins)


def test_successful_install_output_normalizes_dashed_names_and_versions() -> None:
    line = "  Successfully installed scikit-learn-1.2.2 Flask_Login-0.6.3  "
    pins = find_install_pins(
        [],
        {"Scikit_Learn": "1.7.0"},
        output_blocks=[(4, line)],
        packages=["Scikit_Learn", "flask-login"],
    )
    assert [(p.package, p.pinned, p.used, p.origin) for p in pins] == [
        ("scikit-learn", "==1.2.2", "1.7.0", "pip_output"),
        ("flask-login", "==0.6.3", None, "pip_output"),
    ]
    assert all(pin.step_idx == 4 and pin.line == line.strip() for pin in pins)


def test_output_pins_use_first_version_and_keep_install_line_constraints() -> None:
    pins = find_install_pins(
        [(5, "pip install numpy pandas<2")],
        {},
        output_blocks=[
            (3, "Successfully installed pandas-1.5.3 numpy-1.24.0"),
            (2, "Requirement already satisfied: pandas in /site-packages (1.4.0)"),
        ],
    )
    assert [(p.package, p.pinned, p.origin, p.step_idx) for p in pins] == [
        ("pandas", "<2", "install_line", 5),
        ("pandas", "==1.4.0", "pip_output", 2),
        ("numpy", "==1.24.0", "pip_output", 3),
    ]


@pytest.mark.parametrize(
    "line",
    [
        "Collecting pandas==1.5.3",
        "Requirement already satisfied: pandas==1.5.3",
        "Requirement already satisfied pandas in /site-packages (1.5.3)",
        "Requirement already satisfied: pandas in /site-packages (line 1)",
        "Requirement already satisfied: pandas- in /site-packages (1.5.3)",
        "Requirement already satisfied: pandas in /site-packages (1.5.3) trailing text",
        "The log says Successfully installed pandas-1.5.3",
        "Successfully installed pandas-1.5.3 is an example",
        "Successfully installed pandas-not-a-version",
        "Successfully installed",
    ],
)
def test_near_matches_are_not_pip_output_pins(line: str) -> None:
    assert find_install_pins([], {}, output_blocks=[(0, line)], packages=["pandas"]) == []


def test_pip_output_requires_explicit_output_blocks_and_known_names() -> None:
    output = "Successfully installed pandas-1.5.3"
    assert find_install_pins([(0, output)], {}, packages=["pandas"]) == []
    assert find_install_pins([], {}, output_blocks=[(0, output)]) == []


def test_requirement_version_is_last_parenthesized_group() -> None:
    line = (
        "Requirement already satisfied: openai in C:\\site-packages "
        "(from -r requirements.txt (line 1)) (0.27.8)"
    )
    pins = find_install_pins([], {}, output_blocks=[(1, line)], packages=["openai"])
    assert len(pins) == 1
    assert pins[0].pinned == "==0.27.8"


def test_install_line_origin_defaults_and_openapi_documents_output_origin() -> None:
    from app.config import Settings

    pin = find_install_pins([(0, "pip install pandas==1.0")], {})[0]
    assert pin.origin == "install_line"
    schemas = create_app(Settings(_env_file=None, providers=[])).openapi()["components"]["schemas"]
    origin = schemas["InstallPin"]["properties"]["origin"]
    assert origin["default"] == "install_line"
    assert origin["enum"] == ["install_line", "pip_output"]
