from datetime import date
from pathlib import Path

import httpx
import pytest
from test_evidence import EVIDENCE_DIR, PANDAS_BASE, MemCache, PagesNet
from test_pipeline import Cache, Proposer
from test_pipeline_output_check import OutputSandbox

from app.broker import Broker
from app.config import Settings
from app.models import (
    Block,
    CheckReport,
    DateSource,
    Extraction,
    SourceFormat,
    SourceKind,
    StepKind,
)
from app.pipeline import PipelineDeps, run_check
from app.recorder import RecorderSink
from app.release_date import release_date
from app.repair.evidence import Evidence, EvidenceItem, changelog_evidence
from app.storage import create_check, get_report, init_db

FIXTURE = Path(__file__).parent / "fixtures/pypi/pandas_dates.json"
URL = "https://example.org/tutorial"


@pytest.fixture(autouse=True)
def public_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.extract.fetch.resolve_host", lambda _: ["93.184.216.34"])


def test_saved_pandas_removal_date() -> None:
    index = (EVIDENCE_DIR / "pandas_whatsnew_index.html").read_text(encoding="utf-8")
    page = (EVIDENCE_DIR / "pandas_whatsnew_v2.0.0.html").read_text(encoding="utf-8")
    net = PagesNet(index, {PANDAS_BASE + "v2.0.0.html": page})
    with net.client() as client:
        items = changelog_evidence(
            "pandas", "3.1.1", ["DataFrame.append", "append"], client, MemCache()
        )
    assert {item.removed_in for item in items if item.removed_in} == {("pandas", "2.0.0")}
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, content=FIXTURE.read_bytes()))
    ) as client:
        assert release_date("pandas", "2.0.0", client, Cache()) == date(2023, 4, 3)
        assert release_date("pandas", "9.0.0", client, Cache()) is None
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, text="invalid"))
    ) as client:
        assert release_date("pandas", "2.0.0", client, Cache()) is None


@pytest.mark.parametrize("published", [None, date(2020, 1, 1)])
def test_pipeline_persists_earliest_upper_bound(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, published: date | None
) -> None:
    blocks = [
        Block(idx=i, kind=StepKind.PYTHON, code="int('bad')", runnable_code="int('bad')")
        for i in range(2)
    ]
    source = Extraction(
        source_kind=SourceKind.URL,
        source_format=SourceFormat.HTML,
        source=URL,
        packages=[],
        blocks=blocks,
        published=published,
        date_source=DateSource.PAGE if published else DateSource.UNKNOWN,
    )
    monkeypatch.setattr("app.pipeline.extract_url", lambda *args: source)
    versions = iter(["2.1.0", "2.0.0"])
    monkeypatch.setattr(
        "app.repair.loop.gather",
        lambda *args, **kwargs: Evidence(
            items=[
                EvidenceItem(
                    source="changelog",
                    title="Removal",
                    content="Removed old API",
                    removed_in=("pandas", next(versions)),
                )
            ]
        ),
    )
    db = str(tmp_path / "date.db")
    init_db(db)
    create_check(
        db, CheckReport(id="date", source_kind=SourceKind.URL, source=URL, status="running")
    )
    lookups = []

    def respond(request: httpx.Request) -> httpx.Response:
        lookups.append(request.url.path)
        return httpx.Response(200, content=FIXTURE.read_bytes())

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        deps = PipelineDeps(
            Settings(_env_file=None, providers=[]),
            lambda _: OutputSandbox(),
            Proposer(),
            client,
            Cache(),
        )
        run_check("date", URL, deps, RecorderSink(db, "date", Broker()))
    report = get_report(db, "date")
    assert report is not None and report.status == "done"
    assert report.published == published
    if published is None:
        assert report.published_before == date(2023, 4, 3)
        assert report.date_source == DateSource.ESTIMATE
        assert lookups
    else:
        assert report.published_before is None
        assert report.date_source == DateSource.PAGE
        assert not lookups
