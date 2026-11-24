"""Try public code linked by a video's author before transcribing the video."""

import html
import json
import logging
import re
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, quote_plus, unquote, unquote_plus, urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup

from app.config import Settings
from app.extract import extract_url
from app.extract.errors import ExtractionError
from app.extract.fetch import MAX_DOWNLOAD_BYTES
from app.extract.routing import route_url
from app.models import Extraction, StepKind

_API = "https://www.googleapis.com/youtube/v3/"
LOGIN_NOTE = "The linked code is behind a login"
_URL = re.compile(r"https?://[^\s<>\"']+", re.I)
_SHORTENERS = {
    "bit.ly",
    "tinyurl.com",
    "t.co",
    "goo.gl",
    "ow.ly",
    "buff.ly",
    "is.gd",
    "cutt.ly",
    "rebrand.ly",
    "shorturl.at",
    "lnkd.in",
    "youtu.be",
}
_SOCIAL = {
    "youtube.com",
    "x.com",
    "twitter.com",
    "facebook.com",
    "instagram.com",
    "linkedin.com",
    "patreon.com",
    "discord.com",
    "discord.gg",
    "udemy.com",
    "coursera.org",
}
_GITHUB = {"github.com", "www.github.com"}


@dataclass(frozen=True)
class LinkedCodeResult:
    extraction: Extraction | None = None
    login_blocked: bool = False


class _KeyLogFilter(logging.Filter):
    def __init__(self, key: str) -> None:
        super().__init__()
        self._tokens = sorted({key, quote(key, safe=""), quote_plus(key)}, key=len, reverse=True)

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        for token in self._tokens:
            message = message.replace(token, "[redacted]")
        record.msg, record.args = message, ()
        return True


@contextmanager
def redact_api_key_logs(key: str) -> Iterator[None]:
    """Redact query credentials without changing logging levels or suppressing records."""
    if not key:
        yield
        return
    redactor = _KeyLogFilter(key)
    loggers = [
        logging.getLogger(name)
        for name in (
            "httpx",
            "httpcore",
            "httpcore.connection",
            "httpcore.http11",
            "httpcore.http2",
            "httpcore.proxy",
            "httpcore.socks",
        )
    ]
    for logger in loggers:
        logger.addFilter(redactor)
    try:
        yield
    finally:
        for logger in loggers:
            logger.removeFilter(redactor)


def _contains_key(text: str, key: str) -> bool:
    return any(
        token in decoded
        for decoded in (text, unquote(text), unquote_plus(text))
        for token in (key, json.dumps(key)[1:-1])
    )


def _json_without_key(response: httpx.Response, key: str) -> dict[str, Any] | None:
    if _contains_key(response.text, key):
        return None
    data = response.json()
    if _contains_key(json.dumps(data, ensure_ascii=False), key):
        return None
    return data if isinstance(data, dict) else None


def fetch_metadata(
    url: str, key: str, client: httpx.Client
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """Fetch raw snippets; any failure except disabled comments skips source one."""
    if not key.strip():
        return None
    try:
        route = route_url(url)
        if route.kind != "video":
            return None
        identifier = route.fetch_urls[0].rsplit("=", 1)[-1]
        with redact_api_key_logs(key):
            response = client.get(
                _API + "videos",
                params={"part": "snippet", "id": identifier, "key": key},
                timeout=15,
                follow_redirects=False,
            )
            if response.status_code != 200:
                return None
            videos = _json_without_key(response, key)
            if videos is None:
                return None
            response = client.get(
                _API + "commentThreads",
                params={
                    "part": "snippet",
                    "videoId": identifier,
                    "maxResults": 50,
                    "order": "relevance",
                    "key": key,
                },
                timeout=15,
                follow_redirects=False,
            )
            comments = _json_without_key(response, key)
            if comments is None:
                return None
            if response.status_code == 403 and any(
                item.get("reason") == "commentsDisabled"
                for item in comments.get("error", {}).get("errors", [])
                if isinstance(item, dict)
            ):
                return videos, {}
            return (videos, comments) if response.status_code == 200 else None
    except (httpx.HTTPError, ValueError, TypeError, AttributeError):
        return None


def _host_in(host: str, names: set[str]) -> bool:
    host = host.lower().rstrip(".")
    return any(host == name or host.endswith("." + name) for name in names)


def _code_link(url: str) -> str | None:
    try:
        parts = urlsplit(url)
        host, path = (parts.hostname or "").lower(), unquote(parts.path)
        if (
            parts.scheme not in ("http", "https")
            or parts.username is not None
            or parts.password is not None
        ):
            return None
        if parts.port is not None or _host_in(host, _SHORTENERS | _SOCIAL):
            return None
        if host == "colab.research.google.com" and path.startswith("/github/"):
            url = "https://github.com/" + path.removeprefix("/github/")
        elif host in {"nbviewer.org", "nbviewer.jupyter.org"}:
            if path.startswith("/github/"):
                url = "https://github.com/" + path.removeprefix("/github/")
            elif path.startswith("/gist/"):
                url = "https://gist.github.com/" + path.removeprefix("/gist/")
            elif path.startswith("/url/"):
                return _code_link("https://" + path.removeprefix("/url/"))
            else:
                return None
        route = route_url(url)
        parts = urlsplit(url)
        if route.kind in {"github_readme", "notebook"}:
            return url
        segments = [part for part in parts.path.split("/") if part]
        if parts.hostname in _GITHUB and len(segments) >= 5 and segments[2] == "blob":
            return url
        if parts.hostname == "gist.github.com" and len(segments) >= 2:
            return url
    except ValueError:
        return None
    return None


def candidate_links(texts: Iterable[str]) -> list[str]:
    """Keep at most five distinct code links, preserving description/comment order."""
    links: list[str] = []
    seen: set[str] = set()
    for text in texts:
        for match in _URL.finditer(html.unescape(text)):
            link = _code_link(match.group().rstrip(").,;!?]}"))
            if link is not None and link not in seen:
                seen.add(link)
                links.append(link)
                if len(links) == 5:
                    return links
    return links


def _login_url(url: str) -> bool:
    parts = urlsplit(url)
    path = unquote(parts.path).lower()
    return (parts.hostname or "").lower().rstrip(".") == "accounts.google.com" or any(
        token in path for token in ("login", "signin", "sign_in")
    )


class _LoginBlocked(ExtractionError):
    pass


class _LinkedTransport(httpx.BaseTransport):
    """Inspect each hop before the standard extractor follows redirects or parses it."""

    def __init__(self, client: httpx.Client) -> None:
        self._client = client

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        if _login_url(str(request.url)):
            raise _LoginBlocked(LOGIN_NOTE)
        if _host_in(request.url.host, _SHORTENERS):
            raise ExtractionError("Code links through URL shorteners are skipped")
        for header in ("cookie", "authorization", "proxy-authorization"):
            request.headers.pop(header, None)
        response = self._client.send(request, stream=True, follow_redirects=False, auth=None)
        try:
            if response.status_code in (401, 403):
                raise _LoginBlocked(LOGIN_NOTE)
            if response.headers.get("Content-Type", "").lower().startswith("video/"):
                raise ExtractionError("Linked video downloads are skipped")
            if response.is_redirect:
                target = urljoin(str(request.url), response.headers.get("Location", ""))
                if _login_url(target):
                    raise _LoginBlocked(LOGIN_NOTE)
                if _host_in((urlsplit(target).hostname or "").lower(), _SHORTENERS):
                    raise ExtractionError("Code links through URL shorteners are skipped")
                return httpx.Response(
                    response.status_code,
                    headers={"Location": response.headers.get("Location", "")},
                    request=request,
                )
            data = bytearray()
            for chunk in response.iter_bytes():
                if len(data) + len(chunk) > MAX_DOWNLOAD_BYTES:
                    raise ExtractionError(f"Download is larger than {MAX_DOWNLOAD_BYTES} bytes")
                data.extend(chunk)
            if b"<input" in data.lower() and BeautifulSoup(bytes(data), "lxml").find(
                "input", attrs={"type": re.compile(r"^password$", re.I)}
            ):
                raise _LoginBlocked("The linked code is behind a login")
            headers = {
                name: value
                for name, value in response.headers.items()
                if name.lower() not in {"content-encoding", "content-length", "set-cookie"}
            }
            return httpx.Response(response.status_code, headers=headers, content=bytes(data))
        finally:
            response.close()


def extract_youtube_links(url: str, settings: Settings, client: httpx.Client) -> LinkedCodeResult:
    key = settings.youtube_api_key.get_secret_value()
    metadata = fetch_metadata(url, key, client)
    if metadata is None:
        return LinkedCodeResult()
    videos, comments = metadata
    try:
        snippet = videos["items"][0]["snippet"]
        texts = [snippet.get("description", "")]
        author = snippet["channelId"]
        if not isinstance(author, str) or not author:
            return LinkedCodeResult()
        for item in comments.get("items", []):
            comment = item.get("snippet", {}).get("topLevelComment", {}).get("snippet", {})
            if comment.get("authorChannelId", {}).get("value") == author:
                texts.append(comment.get("textOriginal") or comment.get("textDisplay", ""))
        links = candidate_links(text for text in texts if isinstance(text, str))
    except (KeyError, IndexError, TypeError, AttributeError):
        return LinkedCodeResult()
    login_blocked = False
    with redact_api_key_logs(key), httpx.Client(transport=_LinkedTransport(client)) as linked:
        for link in links:
            try:
                extraction = extract_url(link, linked)
            except _LoginBlocked:
                login_blocked = True
                continue
            except (ExtractionError, httpx.HTTPError, ValueError):
                continue
            if not any(
                block.kind in (StepKind.PYTHON, StepKind.REPL) and block.runnable_code
                for block in extraction.blocks
            ):
                continue
            if _contains_key(
                json.dumps(extraction.model_dump(mode="json"), ensure_ascii=False), key
            ):
                continue
            extraction.source = route_url(url).fetch_urls[0]
            for block in extraction.blocks:
                block.low_confidence = False
                block.video_timestamp = None
            return LinkedCodeResult(extraction, login_blocked)
    return LinkedCodeResult(login_blocked=login_blocked)
