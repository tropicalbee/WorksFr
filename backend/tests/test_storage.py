import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.broker import Broker
from app.config import Settings
from app.events import EnvironmentEvent, FailedEvent
from app.main import create_app
from app.models import (
    CheckReport,
    CheckStatus,
    DateSource,
    ErrorInfo,
    ExecResult,
    InstallPin,
    Label,
    LabelDecision,
    PatchVerdict,
    RepairAttempt,
    Score,
    SourceKind,
    StepKind,
    StepReport,
    WarningInfo,
)
from app.recorder import RecorderSink
from app.storage import (
    SqliteDocCache,
    append_event,
    cache_get,
    cache_put,
    create_check,
    get_report,
    init_db,
    list_events,
    list_recent,
    save_step,
    update_check,
)


@pytest.fixture
def database(tmp_path: Path) -> str:
    path = str(tmp_path / "checks.db")
    init_db(path)
    return path


def report(check_id: str = "c1") -> CheckReport:
    return CheckReport(
        id=check_id,
        source_kind=SourceKind.URL,
        source="https://example.test/tutorial",
        title="Tutorial 'quoted'; DROP TABLE checks; --",
        published=date(2020, 1, 2),
        date_source=DateSource.PAGE,
        status=CheckStatus.DONE,
        score=Score(works=1, deprecated=2, broken=3, cant_verify=4, fixed=2, fixed_unverified=1),
        versions={"pandas": "2.2.3"},
        failed_packages=["missing"],
        error="check-level error",
        steps=[
            StepReport(
                idx=1,
                kind=StepKind.PYTHON,
                original_code="df.append(row)",
                final_code="patched",
                label=Label.BROKEN,
                reason="old API",
                verdict=PatchVerdict.FIXED,
                repair_attempts=[
                    RepairAttempt(
                        attempt=1,
                        patched_code="patched",
                        explanation="use concat",
                        apis_changed=["append"],
                        provider="test",
                        result=ExecResult(id="s1-a1", status="ok"),
                        decision=LabelDecision(label=Label.WORKS),
                        verdict=PatchVerdict.FIXED,
                    )
                ],
                apis_changed=["append"],
                explanation="use concat",
                output="hello\n\u20ac",
                error=ErrorInfo(
                    type="AttributeError", message="append missing", traceback="traceback text"
                ),
                warnings=[
                    WarningInfo(
                        category="FutureWarning",
                        message="old",
                        filename="<step-s1>",
                        lineno=2,
                        module="__step__",
                    )
                ],
            ),
            StepReport(
                idx=0, kind=StepKind.SHELL, original_code="pip install pandas", final_code=""
            ),
        ],
    )


def test_init_twice_and_full_round_trip(database: str) -> None:
    init_db(database)
    original = report()
    create_check(database, original)
    original.steps.sort(key=lambda step: step.idx)
    loaded = get_report(database, "c1")
    assert loaded is not None and loaded.created_at
    assert loaded == original.model_copy(update={"created_at": loaded.created_at})
    assert loaded.score.model_dump() == original.score.model_dump()
    with sqlite3.connect(database) as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        created, finished = conn.execute("SELECT created_at, finished_at FROM checks").fetchone()
    assert loaded.created_at == created
    assert datetime.fromisoformat(created).utcoffset().total_seconds() == 0
    assert datetime.fromisoformat(finished).utcoffset().total_seconds() == 0


def test_step_upsert_and_foreign_key(database: str) -> None:
    create_check(database, report())
    step = StepReport(idx=1, kind=StepKind.PYTHON, original_code="old", final_code="new")
    save_step(database, "c1", step)
    loaded = get_report(database, "c1")
    assert loaded is not None
    assert len(loaded.steps) == 2
    assert loaded.steps[1] == step
    with pytest.raises(sqlite3.IntegrityError):
        save_step(database, "unknown", step)


def test_video_metadata_round_trip_and_upsert(database: str) -> None:
    original = report()
    step = original.steps[0]
    step.video_timestamp = 750
    step.low_confidence = True
    create_check(database, original)
    loaded = get_report(database, "c1")
    assert loaded is not None
    assert loaded.steps[1] == step

    for timestamp, low_confidence, agreement in ((None, False, None), (0, True, 1), (3, True, 2)):
        step.video_timestamp = timestamp
        step.low_confidence = low_confidence
        step.read_agreement = agreement
        save_step(database, "c1", step)
        loaded = get_report(database, "c1")
        assert loaded is not None
        assert loaded.steps[1] == step


@pytest.mark.parametrize("version", [0, 1])
def test_existing_database_gains_video_fields_without_losing_rows(
    tmp_path: Path, version: int
) -> None:
    path = str(tmp_path / "legacy.db")
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE checks (
                id TEXT PRIMARY KEY, source_kind TEXT NOT NULL, source TEXT NOT NULL,
                title TEXT, published TEXT, date_source TEXT NOT NULL, status TEXT NOT NULL,
                score_json TEXT, versions_json TEXT NOT NULL, failed_packages_json TEXT NOT NULL,
                error TEXT, created_at TEXT NOT NULL, finished_at TEXT
            );
            CREATE TABLE steps (
                id INTEGER PRIMARY KEY, check_id TEXT NOT NULL REFERENCES checks(id),
                idx INTEGER NOT NULL, kind TEXT NOT NULL, original_code TEXT NOT NULL,
                final_code TEXT NOT NULL, label TEXT, reason TEXT NOT NULL, verdict TEXT,
                repair_attempts_json TEXT NOT NULL, apis_changed_json TEXT NOT NULL,
                explanation TEXT NOT NULL, output TEXT NOT NULL, error_json TEXT,
                warnings_json TEXT NOT NULL, UNIQUE(check_id, idx)
            );
            INSERT INTO checks VALUES (
                'legacy', 'url', 'https://example.test/tutorial', 'Existing tutorial', NULL,
                'unknown', 'done', NULL, '{}', '[]', NULL, '2026-11-24T00:00:00+00:00', NULL
            );
            INSERT INTO steps VALUES (
                42, 'legacy', 0, 'python', 'x = 1', 'x = 1', 'works', '', NULL,
                '[]', '[]', '', 'existing output', NULL, '[]'
            );
            """
        )
        conn.execute(f"PRAGMA user_version={version}")
        before = conn.execute("SELECT * FROM steps").fetchone()
        rootpage = conn.execute("SELECT rootpage FROM sqlite_master WHERE name='steps'").fetchone()

    init_db(path)
    init_db(path)

    loaded = get_report(path, "legacy")
    assert loaded is not None
    assert loaded.title == "Existing tutorial"
    assert loaded.steps[0].original_code == "x = 1"
    assert loaded.steps[0].output == "existing output"
    assert loaded.steps[0].video_timestamp is None
    assert loaded.steps[0].low_confidence is False
    assert loaded.steps[0].read_agreement is None
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT * FROM steps").fetchone()[: len(before)] == before
        assert conn.execute("SELECT rootpage FROM sqlite_master WHERE name='steps'").fetchone() == (
            rootpage
        )
        columns = {row[1] for row in conn.execute("PRAGMA table_info(steps)")}
        assert {"video_timestamp", "low_confidence", "read_agreement"} <= columns

    loaded.steps[0].video_timestamp = 12
    loaded.steps[0].low_confidence = True
    save_step(path, "legacy", loaded.steps[0])
    assert get_report(path, "legacy") == loaded


@pytest.mark.parametrize("status", [CheckStatus.DONE, CheckStatus.FAILED])
def test_update_and_finished_timestamp(database: str, status: CheckStatus) -> None:
    original = report()
    original.status = CheckStatus.RUNNING
    create_check(database, original)
    with sqlite3.connect(database) as conn:
        assert conn.execute("SELECT finished_at FROM checks").fetchone()[0] is None
    update_check(
        database,
        "c1",
        status=status,
        title="new title",
        published=date(2022, 2, 3),
        date_source=DateSource.ESTIMATE,
        failed_packages=["other"],
        score=Score(),
        versions={},
        error="failed",
    )
    loaded = get_report(database, "c1")
    assert loaded is not None
    assert loaded.status == status
    assert loaded.title == "new title"
    assert loaded.published == date(2022, 2, 3)
    assert loaded.date_source == DateSource.ESTIMATE
    assert loaded.score == Score()
    assert loaded.versions == {}
    assert loaded.error == "failed"
    assert loaded.failed_packages == ["other"]
    with sqlite3.connect(database) as conn:
        finished = conn.execute("SELECT finished_at FROM checks").fetchone()[0]
    assert datetime.fromisoformat(finished).utcoffset().total_seconds() == 0
    update_check(database, "c1")
    assert get_report(database, "c1") == loaded


def test_startup_records_failure_for_interrupted_checks(database: str) -> None:
    for check_id in ("running", "queued"):
        create_check(database, report(check_id).model_copy(update={"status": CheckStatus.RUNNING}))
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE checks SET status='queued' WHERE id='queued'")

    running = get_report(database, "running")
    assert running is not None
    environment = EnvironmentEvent(
        versions=running.versions, failed_packages=running.failed_packages
    )
    append_event(database, "running", environment.type, environment.model_dump_json())
    for status in (CheckStatus.DONE, CheckStatus.FAILED):
        create_check(database, report(status).model_copy(update={"status": status}))
    finished = {status: get_report(database, status) for status in ("done", "failed")}

    error = "The check stopped because the server restarted. Run it again."
    app = create_app(Settings(_env_file=None, database_path=database, providers=[]))
    with TestClient(app) as client:
        for check_id in ("running", "queued"):
            response = client.get(f"/api/checks/{check_id}")
            assert response.status_code == 200
            recovered = CheckReport.model_validate(response.json())
            assert recovered.status == CheckStatus.FAILED
            assert recovered.error == error
            assert recovered.steps == running.steps
        replay = client.get("/api/checks/running/events", headers={"Last-Event-ID": "1"})
        assert replay.text == (
            f"id: 2\nevent: failed\ndata: {FailedEvent(error=error).model_dump_json()}\n\n"
        )

    assert {status: get_report(database, status) for status in finished} == finished
    with TestClient(app):
        running_events = [kind for _, kind, _ in list_events(database, "running")]
        assert running_events == ["environment", "failed"]
        assert [kind for _, kind, _ in list_events(database, "queued")] == ["failed"]
    with sqlite3.connect(database) as connection:
        recovered_count = connection.execute(
            "SELECT COUNT(*) FROM checks WHERE id IN ('running', 'queued') "
            "AND finished_at IS NOT NULL"
        ).fetchone()[0]
    assert recovered_count == 2


def test_unknown_and_recent_order_limit(database: str) -> None:
    assert get_report(database, "missing") is None
    assert list_recent(database) == []
    for index in range(4):
        create_check(database, report(str(index)))
    recent = list_recent(database, limit=2)
    assert [item.id for item in recent] == ["3", "2"]
    assert all(item.steps == [] for item in recent)
    assert list_recent(database, limit=0) == []
    with pytest.raises(ValueError):
        list_recent(database, limit=-1)


def test_cache_round_trip_and_overwrite(database: str) -> None:
    assert cache_get(database, "key") is None
    cache_put(database, "key", "pandas", "2", "changelog", "first 'quoted'")
    assert cache_get(database, "key") == "first 'quoted'"
    cache_put(database, "key", "pandas", "3", "search", "updated")
    assert cache_get(database, "key") == "updated"
    with sqlite3.connect(database) as conn:
        row = conn.execute("SELECT library, version, source, fetched_at FROM doc_cache").fetchone()
    assert row[:3] == ("pandas", "3", "search")
    assert datetime.fromisoformat(row[3]).utcoffset().total_seconds() == 0


def test_events_are_sequenced_atomically(database: str) -> None:
    create_check(database, report())
    with ThreadPoolExecutor(max_workers=2) as pool:
        seqs = list(pool.map(lambda n: append_event(database, "c1", "test", str(n)), range(2)))
    assert sorted(seqs) == [1, 2]
    events = list_events(database, "c1")
    assert [row[0] for row in events] == [1, 2]
    assert list_events(database, "c1", after_seq=1) == events[1:]
    assert list_events(database, "missing") == []


def test_cache_adapter_expiry(database: str) -> None:
    cache = SqliteDocCache(database)
    cache.put("key", "pkg", None, "search", "content")
    assert cache.get("key") == "content"
    with sqlite3.connect(database) as conn:
        conn.execute("UPDATE doc_cache SET fetched_at=?", ("2000-01-01T00:00:00+00:00",))
    assert cache.get("key") is None
    assert SqliteDocCache(database, max_age_seconds=None).get("key") == "content"


def test_schema_version_guard(database: str) -> None:
    with sqlite3.connect(database) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
        conn.execute("PRAGMA user_version=99")
    with pytest.raises(RuntimeError, match="remove.*database"):
        init_db(database)
    with sqlite3.connect(database) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 99


def _pin(origin: str) -> InstallPin:
    return InstallPin(
        step_idx=1,
        line="Requirement already satisfied: openai in x (0.27.8)",
        package="openai",
        pinned="==0.27.8",
        used="1.0.0",
        origin=origin,
    )


def test_report_uses_pins_from_stored_environment_event(database: str) -> None:
    create_check(database, report())
    event = EnvironmentEvent(
        versions={"pandas": "2.2.3"},
        failed_packages=[],
        install_pins=[_pin("install_line"), _pin("pip_output")],
    )
    RecorderSink(database, "c1", Broker()).emit(event)
    loaded = get_report(database, "c1")
    assert loaded is not None
    assert loaded.install_pins == event.install_pins
    assert {pin.origin for pin in loaded.install_pins} == {"install_line", "pip_output"}


def test_report_without_environment_event_recomputes_install_line_pins(database: str) -> None:
    create_check(database, report())
    save_step(
        database,
        "c1",
        StepReport(
            idx=0, kind=StepKind.SHELL, original_code="pip install pandas==1.0", final_code=""
        ),
    )
    loaded = get_report(database, "c1")
    assert loaded is not None
    assert [(pin.package, pin.pinned) for pin in loaded.install_pins] == [("pandas", "==1.0")]
