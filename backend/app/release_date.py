"""Resolve a documented API change to its earliest PyPI upload date."""

from datetime import UTC, date, datetime

import httpx

from app.packages import normalize
from app.repair.evidence import DocCache, pypi_metadata


def release_date(package: str, version: str, client: httpx.Client, cache: DocCache) -> date | None:
    releases = pypi_metadata(normalize(package), client, cache, version).get("releases")
    files = releases.get(version) if isinstance(releases, dict) else None
    if not isinstance(files, list):
        return None
    dates: list[date] = []
    for file in files:
        timestamp = file.get("upload_time_iso_8601") if isinstance(file, dict) else None
        if not isinstance(timestamp, str):
            continue
        try:
            value = datetime.fromisoformat(timestamp)
        except ValueError:
            continue
        dates.append(value.astimezone(UTC).date() if value.tzinfo else value.date())
    return min(dates, default=None)
