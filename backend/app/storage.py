"""SQLite persistence for reports and documentation evidence."""

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime

from pydantic import TypeAdapter

from app.events import FailedEvent
from app.models import (
    CheckReport,
    CheckStatus,
    DateSource,
    ErrorInfo,
    InstallPin,
    OutputCheck,
    RepairAttempt,
    Score,
    StepReport,
    WarningInfo,
)
from app.pins import find_install_pins

SCHEMA = """
CREATE TABLE IF NOT EXISTS checks (
    id TEXT PRIMARY KEY,
    source_kind TEXT NOT NULL,
    source TEXT NOT NULL,
    title TEXT,
    published TEXT,
    published_before TEXT,
    date_source TEXT NOT NULL,
    status TEXT NOT NULL,
    score_json TEXT,
    versions_json TEXT NOT NULL,
    failed_packages_json TEXT NOT NULL,
    error TEXT,
    created_at TEXT NOT NULL,
    finished_at TEXT
);
CREATE TABLE IF NOT EXISTS steps (
    id INTEGER PRIMARY KEY,
    check_id TEXT NOT NULL REFERENCES checks(id),
    idx INTEGER NOT NULL,
    kind TEXT NOT NULL,
    original_code TEXT NOT NULL,
    final_code TEXT NOT NULL,
    label TEXT,
    reason TEXT NOT NULL,
    verdict TEXT,
    repair_attempts_json TEXT NOT NULL,
    apis_changed_json TEXT NOT NULL,
    explanation TEXT NOT NULL,
    output TEXT NOT NULL,
    error_json TEXT,
    warnings_json TEXT NOT NULL,
    video_timestamp INTEGER,
    low_confidence INTEGER NOT NULL DEFAULT 0,
    read_agreement INTEGER,
    output_check_json TEXT,
    UNIQUE(check_id, idx)
);
CREATE TABLE IF NOT EXISTS doc_cache (
    key TEXT PRIMARY KEY,
    library TEXT NOT NULL,
    version TEXT,
    source TEXT NOT NULL,
    content TEXT NOT NULL,
    fetched_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    check_id TEXT NOT NULL REFERENCES checks(id),
    seq INTEGER NOT NULL,
    type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(check_id, seq)
);
"""
VERSIONS = TypeAdapter(dict[str, str])
WARNINGS = TypeAdapter(list[WarningInfo])
ATTEMPTS = TypeAdapter(list[RepairAttempt])
STRINGS = TypeAdapter(list[str])
PINS = TypeAdapter(list[InstallPin])


def _now() -> str:
    return datetime.now(UTC).isoformat()


@contextmanager
def _connection(db_path: str) -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        with connection:
            yield connection
    finally:
        connection.close()


def init_db(path: str) -> None:
    with _connection(path) as connection:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, 1):
            raise RuntimeError("Unsupported schema version; remove the old database file manually")
        connection.executescript(SCHEMA)
        check_columns = {row["name"] for row in connection.execute("PRAGMA table_info(checks)")}
        if "published_before" not in check_columns:
            connection.execute("ALTER TABLE checks ADD COLUMN published_before TEXT")
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(steps)")}
        for name, definition in (
            ("video_timestamp", "INTEGER"),
            ("low_confidence", "INTEGER NOT NULL DEFAULT 0"),
            ("read_agreement", "INTEGER"),
            ("output_check_json", "TEXT"),
        ):
            if name not in columns:
                connection.execute(f"ALTER TABLE steps ADD COLUMN {name} {definition}")
        connection.execute("PRAGMA user_version=1")


def mark_interrupted_checks_failed(db_path: str) -> None:
    """Finish interrupted reports and record a terminal event for reconnecting clients."""
    now = _now()
    event = FailedEvent(error="The check stopped because the server restarted. Run it again.")
    interrupted_statuses = (CheckStatus.RUNNING, "queued")
    with _connection(db_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """INSERT INTO events (check_id, seq, type, payload_json, created_at)
            SELECT checks.id,
                (SELECT COALESCE(MAX(seq), 0) + 1 FROM events WHERE check_id=checks.id),
                ?, ?, ?
            FROM checks WHERE status IN (?, ?)""",
            (event.type, event.model_dump_json(), now, *interrupted_statuses),
        )
        connection.execute(
            "UPDATE checks SET status=?, error=?, finished_at=? WHERE status IN (?, ?)",
            (CheckStatus.FAILED, event.error, now, *interrupted_statuses),
        )


def _save_step(connection: sqlite3.Connection, check_id: str, step: StepReport) -> None:
    connection.execute(
        """INSERT INTO steps (
            check_id, idx, kind, original_code, final_code, label, reason,
            verdict, repair_attempts_json, apis_changed_json,
            explanation, output, error_json, warnings_json, video_timestamp, low_confidence,
            read_agreement, output_check_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(check_id, idx) DO UPDATE SET
            kind=excluded.kind, original_code=excluded.original_code,
            final_code=excluded.final_code, label=excluded.label, reason=excluded.reason,
            verdict=excluded.verdict, repair_attempts_json=excluded.repair_attempts_json,
            apis_changed_json=excluded.apis_changed_json, explanation=excluded.explanation,
            output=excluded.output, error_json=excluded.error_json,
            warnings_json=excluded.warnings_json, video_timestamp=excluded.video_timestamp,
            low_confidence=excluded.low_confidence, read_agreement=excluded.read_agreement,
            output_check_json=excluded.output_check_json
        """,
        (
            check_id,
            step.idx,
            step.kind,
            step.original_code,
            step.final_code,
            step.label,
            step.reason,
            step.verdict,
            ATTEMPTS.dump_json(step.repair_attempts).decode(),
            STRINGS.dump_json(step.apis_changed).decode(),
            step.explanation,
            step.output,
            step.error.model_dump_json() if step.error is not None else None,
            WARNINGS.dump_json(step.warnings).decode(),
            step.video_timestamp,
            step.low_confidence,
            step.read_agreement,
            step.output_check.model_dump_json() if step.output_check is not None else None,
        ),
    )


def create_check(db_path: str, report: CheckReport) -> None:
    now = _now()
    finished = now if report.status in (CheckStatus.DONE, CheckStatus.FAILED) else None
    with _connection(db_path) as connection:
        connection.execute(
            """INSERT INTO checks (
                id, source_kind, source, title, published, published_before, date_source, status,
                score_json, versions_json, failed_packages_json, error, created_at, finished_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                report.id,
                report.source_kind,
                report.source,
                report.title,
                report.published.isoformat() if report.published else None,
                report.published_before.isoformat() if report.published_before else None,
                report.date_source,
                report.status,
                report.score.model_dump_json() if report.score is not None else None,
                VERSIONS.dump_json(report.versions).decode(),
                STRINGS.dump_json(report.failed_packages).decode(),
                report.error,
                now,
                finished,
            ),
        )
        for step in report.steps:
            _save_step(connection, report.id, step)


def update_check(
    db_path: str,
    check_id: str,
    *,
    status: CheckStatus | None = None,
    score: Score | None = None,
    versions: dict[str, str] | None = None,
    failed_packages: list[str] | None = None,
    title: str | None = None,
    published: date | None = None,
    published_before: date | None = None,
    date_source: DateSource | None = None,
    error: str | None = None,
) -> None:
    """Update supplied non-None fields; leave the remaining fields unchanged."""
    values: dict[str, str | None] = {}
    if status is not None:
        values["status"] = status
        if status in (CheckStatus.DONE, CheckStatus.FAILED):
            values["finished_at"] = _now()
    if score is not None:
        values["score_json"] = score.model_dump_json()
    if versions is not None:
        values["versions_json"] = VERSIONS.dump_json(versions).decode()
    if failed_packages is not None:
        values["failed_packages_json"] = STRINGS.dump_json(failed_packages).decode()
    if title is not None:
        values["title"] = title
    if published is not None:
        values["published"] = published.isoformat()
    if published_before is not None:
        values["published_before"] = published_before.isoformat()
    if date_source is not None:
        values["date_source"] = date_source
    if error is not None:
        values["error"] = error
    if not values:
        return
    # Identifiers come exclusively from the fixed column names above.
    assignments = ", ".join(f"{column}=?" for column in values)
    with _connection(db_path) as connection:
        connection.execute(
            f"UPDATE checks SET {assignments} WHERE id=?",
            [*values.values(), check_id],
        )


def save_step(db_path: str, check_id: str, step: StepReport) -> None:
    with _connection(db_path) as connection:
        _save_step(connection, check_id, step)


def _step(row: sqlite3.Row) -> StepReport:
    return StepReport(
        idx=row["idx"],
        kind=row["kind"],
        original_code=row["original_code"],
        final_code=row["final_code"],
        label=row["label"],
        reason=row["reason"],
        verdict=row["verdict"],
        repair_attempts=ATTEMPTS.validate_json(row["repair_attempts_json"]),
        apis_changed=STRINGS.validate_json(row["apis_changed_json"]),
        explanation=row["explanation"],
        output=row["output"],
        error=ErrorInfo.model_validate_json(row["error_json"]) if row["error_json"] else None,
        warnings=WARNINGS.validate_json(row["warnings_json"]),
        video_timestamp=row["video_timestamp"],
        low_confidence=row["low_confidence"],
        read_agreement=row["read_agreement"],
        output_check=(
            OutputCheck.model_validate_json(row["output_check_json"])
            if row["output_check_json"]
            else None
        ),
    )


def _stored_pins(connection: sqlite3.Connection, check_id: str) -> list[InstallPin] | None:
    """Pins from the last stored environment event, or None for checks that predate them."""
    row = connection.execute(
        "SELECT payload_json FROM events WHERE check_id=? AND type='environment' "
        "ORDER BY seq DESC LIMIT 1",
        (check_id,),
    ).fetchone()
    pins = json.loads(row["payload_json"]).get("install_pins") if row is not None else None
    return PINS.validate_python(pins) if pins is not None else None


def _report(
    row: sqlite3.Row, steps: list[StepReport], pins: list[InstallPin] | None = None
) -> CheckReport:
    return CheckReport(
        id=row["id"],
        source_kind=row["source_kind"],
        source=row["source"],
        title=row["title"],
        published=row["published"],
        published_before=row["published_before"],
        date_source=row["date_source"],
        status=row["status"],
        created_at=row["created_at"],
        score=Score.model_validate(json.loads(row["score_json"])) if row["score_json"] else None,
        versions=VERSIONS.validate_json(row["versions_json"]),
        failed_packages=STRINGS.validate_json(row["failed_packages_json"]),
        error=row["error"],
        steps=steps,
        install_pins=pins
        if pins is not None
        else find_install_pins(
            ((step.idx, step.original_code) for step in steps),
            VERSIONS.validate_json(row["versions_json"]),
        ),
    )


def get_report(db_path: str, check_id: str) -> CheckReport | None:
    with _connection(db_path) as connection:
        row = connection.execute("SELECT * FROM checks WHERE id=?", (check_id,)).fetchone()
        if row is None:
            return None
        steps = connection.execute(
            "SELECT * FROM steps WHERE check_id=? ORDER BY idx",
            (check_id,),
        ).fetchall()
        return _report(row, [_step(step) for step in steps], _stored_pins(connection, check_id))


def list_recent(db_path: str, limit: int = 20) -> list[CheckReport]:
    if limit < 0:
        raise ValueError("limit must not be negative")
    with _connection(db_path) as connection:
        rows = connection.execute(
            "SELECT * FROM checks ORDER BY created_at DESC, rowid DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [_report(row, []) for row in rows]


def cache_get(db_path: str, key: str) -> str | None:
    with _connection(db_path) as connection:
        row = connection.execute("SELECT content FROM doc_cache WHERE key=?", (key,)).fetchone()
        return row["content"] if row is not None else None


def cache_put(
    db_path: str,
    key: str,
    library: str,
    version: str | None,
    source: str,
    content: str,
) -> None:
    with _connection(db_path) as connection:
        connection.execute(
            """INSERT INTO doc_cache (key, library, version, source, content, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET library=excluded.library, version=excluded.version,
                source=excluded.source, content=excluded.content, fetched_at=excluded.fetched_at""",
            (key, library, version, source, content, _now()),
        )


def append_event(db_path: str, check_id: str, type: str, payload_json: str) -> int:
    with _connection(db_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        seq = connection.execute(
            "SELECT COALESCE(MAX(seq), 0) + 1 FROM events WHERE check_id=?", (check_id,)
        ).fetchone()[0]
        connection.execute(
            "INSERT INTO events (check_id, seq, type, payload_json, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (check_id, seq, type, payload_json, _now()),
        )
        return seq


def list_events(db_path: str, check_id: str, after_seq: int = 0) -> list[tuple[int, str, str]]:
    with _connection(db_path) as connection:
        rows = connection.execute(
            "SELECT seq, type, payload_json FROM events WHERE check_id=? AND seq>? ORDER BY seq",
            (check_id, after_seq),
        ).fetchall()
        return [(row[0], row[1], row[2]) for row in rows]


class SqliteDocCache:
    def __init__(self, db_path: str, max_age_seconds: int | None = 7 * 24 * 3600) -> None:
        self.db_path = db_path
        self.max_age_seconds = max_age_seconds

    def get(self, key: str) -> str | None:
        with _connection(self.db_path) as connection:
            row = connection.execute(
                "SELECT content, fetched_at FROM doc_cache WHERE key=?", (key,)
            ).fetchone()
            if row is None:
                return None
            if self.max_age_seconds is not None:
                age = (
                    datetime.now(UTC) - datetime.fromisoformat(row["fetched_at"])
                ).total_seconds()
                if age > self.max_age_seconds:
                    return None
            return row["content"]

    def put(self, key: str, library: str, version: str | None, source: str, content: str) -> None:
        cache_put(self.db_path, key, library, version, source, content)
