"""Fallback page reader for tutorials whose code is loaded by JavaScript.

Used only when a direct fetch succeeded but found no code. It is never a way past a site that
refuses us: a blocked page, a paywall and a login page are all reported, not retried. WorksFr
sends no credentials to any site, and asks each reader for its default scrape.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import unquote, urlsplit

import httpx

from app.config import Settings
from app.extract.blocks import RawBlock
from app.extract.errors import ExtractionError
from app.extract.fetch import check_url
from app.extract.markdown_doc import parse_markdown

READER_TIMEOUT_SECONDS = 60
LOGIN_MESSAGE = (
    "This page is behind a login, and WorksFr never logs in. "
    "Paste the code or upload the notebook instead."
)
_BLOCKED_STATUSES = (401, 403, 429)
_LOGIN_PATH_TOKENS = ("login", "signin", "sign_in", "sign-in")
_LOGIN_WORDS = re.compile(r"\b(?:sign|log)[ -]?in\b", re.I)


@dataclass
class ReaderPage:
    provider: str
    title: str | None
    blocks: list[RawBlock]


@dataclass
class _Reply:
    markdown: str
    title: str | None
    final_url: str | None
    target_status: int | None


def _text(value: Any) -> str | None:
    if isinstance(value, list):
        value = value[0] if value else None
    return value.strip() or None if isinstance(value, str) else None


def _firecrawl(url: str, settings: Settings, client: httpx.Client) -> _Reply | None:
    key = settings.firecrawl_api_key.get_secret_value()
    if not key:
        return None
    response = client.post(
        settings.firecrawl_base_url.rstrip("/") + "/v2/scrape",
        headers={"Authorization": f"Bearer {key}"},
        json={
            "url": url,
            "formats": ["markdown"],
            "onlyMainContent": True,
            "timeout": READER_TIMEOUT_SECONDS * 1000,
        },
        timeout=READER_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    body = response.json()
    data = body["data"] if body.get("success") else None
    if not isinstance(data, dict):
        return None
    metadata = data.get("metadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    status = metadata.get("statusCode")
    return _Reply(
        markdown=data.get("markdown") or "",
        title=_text(metadata.get("title")),
        final_url=_text(metadata.get("url")),
        target_status=status if isinstance(status, int) else None,
    )


def _jina(url: str, settings: Settings, client: httpx.Client) -> _Reply | None:
    headers = {"Accept": "application/json", "X-Respond-With": "markdown"}
    key = settings.jina_api_key.get_secret_value()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    response = client.get(
        settings.jina_reader_base_url.rstrip("/") + "/" + url,
        headers=headers,
        timeout=READER_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    data = response.json().get("data")
    if not isinstance(data, dict):
        return None
    return _Reply(
        markdown=data.get("content") or "",
        title=_text(data.get("title")),
        final_url=_text(data.get("url")),
        target_status=None,
    )


_PROVIDERS: dict[str, Callable[[str, Settings, httpx.Client], _Reply | None]] = {
    "firecrawl": _firecrawl,
    "jina": _jina,
}


def _login_page(reply: _Reply) -> bool:
    if reply.final_url:
        parts = urlsplit(reply.final_url)
        path = unquote(parts.path).lower()
        if (parts.hostname or "").lower() == "accounts.google.com" or any(
            token in path for token in _LOGIN_PATH_TOKENS
        ):
            return True
    return "password" in reply.markdown.lower() and bool(_LOGIN_WORDS.search(reply.markdown))


def _clean_blocks(blocks: list[RawBlock]) -> list[RawBlock]:
    invisible = str.maketrans("", "", "\u200b\u200c\u200d\u2060\ufeff")
    cleaned: list[RawBlock] = []
    pending_context: list[str] = []
    for block in blocks:
        if block.context:
            pending_context.append(block.context)
        text = block.text.translate(invisible)
        if not text.strip():
            continue
        block.context = "\n".join(pending_context)
        pending_context.clear()
        text = "\n".join(line if line.strip() else "" for line in text.split("\n"))
        if cleaned and not block.context and cleaned[-1].hint == block.hint:
            cleaned[-1].text += "\n" + text
        else:
            block.text = text
            cleaned.append(block)
    return cleaned


def read_page(url: str, settings: Settings, client: httpx.Client) -> ReaderPage | None:
    """Code blocks of a JavaScript-rendered page via the first reader that finds any.

    Returns None when no reader is configured or none finds code. Raises ExtractionError when
    a reader reports a login page or a page that refuses automated reading.
    """
    try:
        check_url(url)
    except ExtractionError:
        return None
    for name in settings.reader_providers:
        provider = _PROVIDERS.get(name.lower())
        if provider is None:
            continue
        try:
            reply = provider(url, settings, client)
        except (httpx.HTTPError, ValueError, KeyError, AttributeError):
            continue
        if reply is None:
            continue
        if reply.target_status in _BLOCKED_STATUSES:
            raise ExtractionError(
                f"{urlsplit(url).hostname} blocks automated reading, so WorksFr can't check "
                "this page. Paste the code or upload the notebook instead."
            )
        title, blocks = parse_markdown(reply.markdown)
        blocks = _clean_blocks(blocks)
        if blocks:
            return ReaderPage(name, title or reply.title, blocks)
        if _login_page(reply):
            raise ExtractionError(LOGIN_MESSAGE)
    return None
