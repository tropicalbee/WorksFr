"""Decide how a URL is fetched and parsed. Pure string work, no network."""

import re
from dataclasses import dataclass
from typing import Literal
from urllib.parse import unquote, urlsplit

RouteKind = Literal["github_readme", "stackoverflow", "notebook", "html"]

_RAW_HOST = "https://raw.githubusercontent.com"
_GITHUB_HOSTS = {"github.com", "www.github.com"}
_STACKOVERFLOW_HOSTS = {"stackoverflow.com", "www.stackoverflow.com"}
_README_NAMES = ("README.md", "readme.md", "Readme.md")
_MARKDOWN_SUFFIXES = (".md", ".markdown")
_GITHUB_NON_REPO_OWNERS = {
    "features", "topics", "orgs", "marketplace", "settings", "sponsors", "about", "pricing",
    "explore", "login", "collections", "trending", "events", "notifications", "enterprise",
    "apps", "search", "gist", "codespaces", "issues", "pulls", "new",
}  # fmt: skip


@dataclass(frozen=True)
class Route:
    kind: RouteKind
    fetch_urls: tuple[str, ...] = ()
    """URLs to try in order (README candidates, the raw notebook, or the page itself)."""
    question_id: int | None = None
    answer_id: int | None = None


def _raw_url(owner: str, repo: str, ref: str, path: str) -> str:
    return f"{_RAW_HOST}/{owner}/{repo}/{ref}/{path}".rstrip("/")


def _github_route(segments: list[str]) -> Route | None:
    if len(segments) < 2 or segments[0].lower() in _GITHUB_NON_REPO_OWNERS:
        return None
    owner, repo = segments[0], segments[1].removesuffix(".git")
    rest = segments[2:]
    if not rest:
        return Route(
            "github_readme", tuple(_raw_url(owner, repo, "HEAD", n) for n in _README_NAMES)
        )
    if rest[0] in ("blob", "raw") and len(rest) >= 3:
        ref, path = rest[1], "/".join(rest[2:])
        if path.lower().endswith(".ipynb"):
            return Route("notebook", (_raw_url(owner, repo, ref, path),))
        if path.lower().endswith(_MARKDOWN_SUFFIXES):
            return Route("github_readme", (_raw_url(owner, repo, ref, path),))
        return None
    if rest[0] == "tree" and len(rest) >= 2:
        ref, folder = rest[1], "/".join(rest[2:])
        prefix = f"{folder}/" if folder else ""
        return Route(
            "github_readme", tuple(_raw_url(owner, repo, ref, prefix + n) for n in _README_NAMES)
        )
    return None


def _stackoverflow_route(path: str, fragment: str) -> Route | None:
    answer = re.fullmatch(r"/a/(\d+)(?:/\d+)?/?", path)
    if answer:
        return Route("stackoverflow", answer_id=int(answer.group(1)))
    short_question = re.fullmatch(r"/q/(\d+)(?:/\d+)?/?", path)
    if short_question:
        return Route("stackoverflow", question_id=int(short_question.group(1)))
    question = re.fullmatch(r"/questions/(\d+)(?:/[^/]*)?(?:/(\d+))?/?", path)
    if question:
        answer_id = question.group(2) or (fragment if fragment.isdigit() else None)
        return Route(
            "stackoverflow",
            question_id=int(question.group(1)),
            answer_id=int(answer_id) if answer_id else None,
        )
    return None


def route_url(url: str) -> Route:
    """Pick the extraction path for a URL. Anything unrecognised is parsed as HTML."""
    parts = urlsplit(url.strip())
    host = (parts.hostname or "").lower()
    path = unquote(parts.path)
    segments = [s for s in path.split("/") if s]

    if host in _STACKOVERFLOW_HOSTS:
        route = _stackoverflow_route(path, parts.fragment)
        if route is not None:
            return route
    elif host in _GITHUB_HOSTS:
        route = _github_route(segments)
        if route is not None:
            return route
    elif host == "raw.githubusercontent.com":
        if path.lower().endswith(_MARKDOWN_SUFFIXES):
            return Route("github_readme", (url,))
    if path.lower().endswith(".ipynb"):
        return Route("notebook", (url,))
    return Route("html", (url,))
