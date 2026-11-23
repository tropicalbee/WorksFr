import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from pathlib import Path

import pytest

from app.models import (
    CheckReport,
    CheckStatus,
    DateSource,
    ErrorInfo,
    ExecResult,
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
    assert get_report(database, "c1") == original
    loaded = get_report(database, "c1")
    assert loaded.score.model_dump() == original.score.model_dump()
    with sqlite3.connect(database) as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        created, finished = conn.execute("SELECT created_at, finished_at FROM checks").fetchone()
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
