"""Pip output contributes only typed output blocks to live version evidence."""

import json
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from test_pipeline_video import FakeSandbox, PrivateCache, Sink, UnavailableProposer
from test_video import end_marker

from app.api import router
from app.config import Settings
from app.models import Block, Extraction, SourceFormat, SourceKind, StepKind
from app.pipeline import PipelineDeps, run_check

FIXTURES = Path(__file__).parent / "fixtures" / "video"


def test_saved_video_pip_output_reaches_environment_pins() -> None:
    replies = iter(
        [
            httpx.Response(
                200,
                json=json.loads((FIXTURES / f"CbpsDMwFG2g_{window}.json").read_text()),
            )
            for window in ("0-300", "300-600", "600-900")
        ]
        + [end_marker()]
    )
    config = Settings(
        _env_file=None,
        providers=[],
        video_reader_base_url="https://video.test/v1",
        video_reader_api_key="test-secret",
        video_reader_model="test-model",
    )
    sink = Sink()
    with httpx.Client(transport=httpx.MockTransport(lambda _: next(replies))) as http:
        deps = PipelineDeps(
            config, lambda _: FakeSandbox(), UnavailableProposer(), http, PrivateCache()
        )
        run_check("video-pins", "https://youtu.be/CbpsDMwFG2g", deps, sink)
    assert sink.events[-1].type == "done"
    environment = next(event for event in sink.events if event.type == "environment")
    openai_pins = [pin for pin in environment.install_pins if pin.package == "openai"]
    assert len(openai_pins) == 1
    pin = openai_pins[0]
    assert (pin.pinned, pin.used, pin.origin) == ("==0.27.8", "1.0", "pip_output")
    extracted = sink.events[0]
    assert extracted.type == "extracted"
    assert extracted.blocks[pin.step_idx].kind == StepKind.OUTPUT
    assert pin.line.startswith("Requirement already satisfied: openai in ")
    assert not {"yarl", "frozenlist"} & {pin.package for pin in environment.install_pins}


@pytest.mark.parametrize("kind", [StepKind.OUTPUT, StepKind.SHELL, StepKind.OTHER, StepKind.PYTHON])
def test_only_output_kind_is_read_for_pip_versions(
    monkeypatch: pytest.MonkeyPatch, kind: StepKind
) -> None:
    extraction = Extraction(
        source_kind=SourceKind.URL,
        source_format=SourceFormat.HTML,
        source="https://tutorial.test/example",
        packages=["pandas"],
        blocks=[
            Block(idx=0, kind=StepKind.PYTHON, code="x = 1", runnable_code="x = 1"),
            Block(idx=1, kind=kind, code="Successfully installed pandas-1.2.3"),
        ],
    )
    monkeypatch.setattr("app.pipeline.extract_url", lambda *_: extraction)
    sink = Sink()
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: pytest.fail("No HTTP expected"))
    ) as http:
        deps = PipelineDeps(
            Settings(_env_file=None, providers=[]),
            lambda _: FakeSandbox(),
            UnavailableProposer(),
            http,
            PrivateCache(),
        )
        run_check("typed-output", extraction.source, deps, sink)
    assert sink.events[-1].type == "done"
    pins = next(event.install_pins for event in sink.events if event.type == "environment")
    assert [(pin.package, pin.pinned, pin.origin) for pin in pins] == (
        [("pandas", "==1.2.3", "pip_output")] if kind == StepKind.OUTPUT else []
    )


def test_unpinned_install_name_allows_output_pin(monkeypatch: pytest.MonkeyPatch) -> None:
    extraction = Extraction(
        source_kind=SourceKind.URL,
        source_format=SourceFormat.HTML,
        source="https://tutorial.test/example",
        packages=[],
        blocks=[
            Block(idx=0, kind=StepKind.PYTHON, code="x = 1", runnable_code="x = 1"),
            Block(idx=1, kind=StepKind.SHELL, code="pip install Pillow"),
            Block(
                idx=2, kind=StepKind.OUTPUT, code="Successfully installed Pillow-9.0.0 yarl-1.9.2"
            ),
        ],
    )
    monkeypatch.setattr("app.pipeline.extract_url", lambda *_: extraction)
    sink = Sink()
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: pytest.fail("No HTTP expected"))
    ) as http:
        deps = PipelineDeps(
            Settings(_env_file=None, providers=[]),
            lambda _: FakeSandbox(),
            UnavailableProposer(),
            http,
            PrivateCache(),
        )
        run_check("install-name", extraction.source, deps, sink)
    assert sink.events[-1].type == "done"
    pins = next(event.install_pins for event in sink.events if event.type == "environment")
    assert [(pin.package, pin.pinned, pin.used, pin.origin) for pin in pins] == [
        ("pillow", "==9.0.0", None, "pip_output")
    ]


def test_openapi_install_pin_origin() -> None:
    app = FastAPI()
    app.include_router(router)
    field = app.openapi()["components"]["schemas"]["InstallPin"]["properties"]["origin"]
    assert field["enum"] == ["install_line", "pip_output"]
    assert field["default"] == "install_line"
