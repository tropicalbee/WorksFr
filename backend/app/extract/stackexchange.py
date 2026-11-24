"""Stack Overflow answers through the Stack Exchange API (ADR-005).

stackoverflow.com pages sit behind a Cloudflare check, so the answer is fetched from
api.stackexchange.com. Code comes from the answer's `body` HTML, the date from
`creation_date`, and the title from the question.
"""

import html
import json
from datetime import UTC, date, datetime
from typing import Any

import httpx

from app.extract.blocks import build_blocks
from app.extract.errors import ExtractionError
from app.extract.fetch import fetch_url
from app.extract.html_page import parse_html_blocks
from app.extract.libs import library_fields
from app.models import DateSource, Extraction, SourceFormat, SourceKind

API_BASE = "https://api.stackexchange.com/2.3"
_SITE = "stackoverflow"


def _api_get(client: httpx.Client, path: str, **params: str) -> list[dict[str, Any]]:
    url = f"{API_BASE}{path}"
    try:
        request = client.build_request("GET", url, params={"site": _SITE, **params})
        status, data = fetch_url(client, str(request.url))
        if status >= 400:
            raise ExtractionError(f"Stack Exchange API returned HTTP {status} for {path}")
        payload = json.loads(data)
    except httpx.HTTPStatusError as exc:
        raise ExtractionError(
            f"Stack Exchange API returned HTTP {exc.response.status_code} for {path}"
        ) from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise ExtractionError(f"Could not reach the Stack Exchange API for {path}: {exc}") from exc
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        raise ExtractionError(f"Unexpected Stack Exchange API reply for {path}")
    return items


def _pick_answer(items: list[dict[str, Any]]) -> dict[str, Any]:
    """The accepted answer, else the top-voted one."""
    for item in items:
        if item.get("is_accepted"):
            return item
    return max(items, key=lambda item: item.get("score", 0))


def _question_title(client: httpx.Client, question_id: int) -> str | None:
    try:
        items = _api_get(client, f"/questions/{question_id}")
    except ExtractionError:
        return None
    if not items or not isinstance(items[0].get("title"), str):
        return None
    return html.unescape(items[0]["title"]).strip() or None


def extract_stackoverflow(
    url: str, client: httpx.Client, *, question_id: int | None, answer_id: int | None
) -> Extraction:
    """The answer a link points at, else the question's accepted or top-voted answer."""
    if answer_id is not None:
        items = _api_get(client, f"/answers/{answer_id}", filter="withbody")
    elif question_id is not None:
        items = _api_get(
            client,
            f"/questions/{question_id}/answers",
            filter="withbody",
            sort="votes",
            order="desc",
        )
    else:
        raise ExtractionError(f"No Stack Overflow question or answer id in {url}")
    if not items:
        raise ExtractionError(f"Stack Overflow has no answer for {url}")
    answer = items[0] if answer_id is not None else _pick_answer(items)

    body = answer.get("body")
    if not isinstance(body, str):
        raise ExtractionError("Stack Exchange API reply has no answer body")
    blocks = build_blocks(parse_html_blocks(body))

    question = question_id if question_id is not None else answer.get("question_id")
    title = _question_title(client, question) if isinstance(question, int) else None

    published: date | None = None
    created = answer.get("creation_date")
    if isinstance(created, int):
        published = datetime.fromtimestamp(created, tz=UTC).date()

    return Extraction(
        source_kind=SourceKind.URL,
        source_format=SourceFormat.STACKOVERFLOW,
        source=url,
        title=title,
        published=published,
        date_source=DateSource.PAGE if published else DateSource.UNKNOWN,
        blocks=blocks,
        **library_fields(blocks),
    )
