from pathlib import Path

import httpx
import pytest
from test_pipeline import Cache, Proposer, Sink

from app.config import Settings
from app.events import ExtractedEvent
from app.extract.video import VideoReader
from app.extract.youtube_links import video_title
from app.models import Block, Extraction, SourceFormat, SourceKind, StepKind
from app.pipeline import PipelineDeps, run_check

URL = "https://www.youtube.com/watch?v=_zN8QziXjd8"
FIXTURE = Path(__file__).parent / "fixtures/youtube/oembed_zN8QziXjd8.json"


@pytest.fixture(autouse=True)
def public_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.extract.fetch.resolve_host", lambda _: ["8.8.8.8"])


def oembed(request: httpx.Request) -> httpx.Response:
    assert request.url.host == "www.youtube.com"
    assert request.url.path == "/oembed"
    assert dict(request.url.params) == {"url": URL, "format": "json"}
    assert request.extensions["timeout"]["read"] == 10
    return httpx.Response(200, content=FIXTURE.read_bytes())


def test_oembed_title() -> None:
    with httpx.Client(transport=httpx.MockTransport(oembed)) as client:
        assert video_title(URL, Settings(_env_file=None), client) == "Python Tutorial for Beginners"


def test_snippet_title_wins() -> None:
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: pytest.fail("Unexpected request"))
    ) as client:
        assert (
            video_title(URL, Settings(_env_file=None), client, {"title": "  Snippet  "})
            == "Snippet"
        )


@pytest.mark.parametrize(
    ("status", "body"), [(404, "{}"), (500, "{}"), (200, "invalid"), (200, "{}"), (302, "{}")]
)
def test_unavailable_oembed(status: int, body: str) -> None:
    def respond(_: httpx.Request) -> httpx.Response:
        return httpx.Response(status, text=body, headers={"Location": "https://example.com"})

    with httpx.Client(transport=httpx.MockTransport(respond), follow_redirects=True) as client:
        assert video_title(URL, Settings(_env_file=None), client) is None


def test_oembed_timeout() -> None:
    def respond(_: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("Read timed out")

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        assert video_title(URL, Settings(_env_file=None), client) is None


def test_oembed_title_length() -> None:
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"title": "x" * 300}))
    ) as client:
        assert video_title(URL, Settings(_env_file=None), client) == "x" * 200


def test_video_title_in_pipeline(monkeypatch: pytest.MonkeyPatch) -> None:
    def extract(self: VideoReader, url: str, **kwargs: object) -> Extraction:
        return Extraction(
            source=url,
            source_kind=SourceKind.URL,
            source_format=SourceFormat.VIDEO,
            packages=[],
            blocks=[Block(idx=0, kind=StepKind.PYTHON, code="x = 1", runnable_code="x = 1")],
        )

    monkeypatch.setattr(VideoReader, "extract", extract)
    sink = Sink()

    def stop_after_extraction(_: str) -> None:
        from app.sandbox.interface import SandboxError

        raise SandboxError("No sandbox needed for extraction test")

    with httpx.Client(transport=httpx.MockTransport(oembed)) as client:
        deps = PipelineDeps(
            settings=Settings(_env_file=None, providers=[]),
            sandbox_factory=stop_after_extraction,
            proposer=Proposer(),
            http_client=client,
            doc_cache=Cache(),
        )
        run_check("video-title", URL, deps, sink)
    event = sink.events[0]
    assert isinstance(event, ExtractedEvent)
    assert event.title == "Python Tutorial for Beginners"
