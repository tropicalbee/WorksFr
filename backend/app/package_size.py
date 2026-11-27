"""Best-effort wheel sizes for the sandbox's CPython 3.12 Linux environment."""

import json
from collections.abc import Iterable
from typing import Any
from urllib.parse import unquote, urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup
from packaging.utils import InvalidWheelFilename, parse_wheel_filename
from packaging.version import Version

from app.extract.errors import ExtractionError
from app.extract.fetch import fetch_response
from app.packages import normalize
from app.repair.evidence import FETCH_TIMEOUT, DocCache, _fetch, pypi_metadata


def _rank(filename: str, package: str, custom: bool) -> tuple[Version, int] | None:
    try:
        name, version, _, tags = parse_wheel_filename(filename)
    except InvalidWheelFilename:
        return None
    if name != package or version.is_prerelease:
        return None
    if any(
        tag.interpreter == "cp312"
        and tag.platform.endswith("_x86_64")
        and (tag.platform.startswith("manylinux") or (custom and tag.platform == "linux_x86_64"))
        for tag in tags
    ):
        return version, 2
    if any(
        tag.interpreter == "py3" and tag.abi == "none" and tag.platform == "any" for tag in tags
    ):
        return version, 1
    return None


def _choose(files: Iterable[Any], package: str, custom: bool) -> dict[str, Any] | None:
    candidates = []
    for file in files:
        if not isinstance(file, dict) or file.get("yanked", False) is not False:
            continue
        filename = file.get("filename")
        if isinstance(filename, str) and (rank := _rank(filename, package, custom)) is not None:
            candidates.append((rank, file))
    return max(candidates, key=lambda entry: entry[0])[1] if candidates else None


def _index_files(text: str, page: str) -> Iterable[Any]:
    try:
        document = json.loads(text)
    except ValueError:
        for link in BeautifulSoup(text, "lxml").find_all("a", href=True):
            href = urljoin(page, str(link["href"]))
            filename = unquote(urlsplit(href).path.rsplit("/", 1)[-1])
            yield {"filename": filename, "url": href, "yanked": link.has_attr("data-yanked")}
        return
    files = document.get("files") if isinstance(document, dict) else None
    if isinstance(files, list):
        yield from files


def package_size(
    package: str, client: httpx.Client, cache: DocCache, index_url: str | None = None
) -> int | None:
    """Return the selected wheel size in bytes, or None when it cannot be measured."""
    package = normalize(package)
    try:
        if index_url:
            page = f"{index_url.rstrip('/')}/{package}/"
            listing = _fetch(
                page,
                client,
                cache,
                package,
                None,
                "pypi",
                headers={"Accept": "application/vnd.pypi.simple.v1+json, text/html;q=0.5"},
            )
            chosen = _choose(_index_files(listing, page), package, True) if listing else None
        else:
            metadata = pypi_metadata(package, client, cache, None)
            releases = metadata.get("releases")
            files = (
                (
                    file
                    for entries in releases.values()
                    if isinstance(entries, list)
                    for file in entries
                )
                if isinstance(releases, dict)
                else ()
            )
            chosen = _choose(files, package, False)
            page = ""
        if chosen is None:
            return None
        size = chosen.get("size")
        if type(size) is int and size >= 0:
            return size
        if index_url and isinstance(href := chosen.get("url"), str):
            response = fetch_response(
                client, urljoin(page, href), max_bytes=0, timeout=FETCH_TIMEOUT, head=True
            )
            if response.is_success:
                length = response.headers.get("content-length", "")
                return int(length) if length.isascii() and length.isdecimal() else None
    except (ExtractionError, httpx.HTTPError, ValueError):
        return None
    return None
