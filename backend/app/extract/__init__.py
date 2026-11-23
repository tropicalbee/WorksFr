"""Turn a tutorial URL or an uploaded notebook into an Extraction.

Tutorial content is untrusted data: it is parsed here, never executed or imported.
"""

import httpx

from app.extract.blocks import build_blocks
from app.extract.errors import ExtractionError
from app.extract.html_page import page_metadata, parse_html_blocks
from app.extract.libs import IMPORT_TO_PYPI, detect_packages
from app.extract.markdown_doc import parse_markdown
from app.extract.notebook import parse_notebook
from app.extract.routing import Route, route_url
from app.extract.stackexchange import extract_stackoverflow
from app.models import DateSource, Extraction, SourceFormat, SourceKind

__all__ = [
    "IMPORT_TO_PYPI",
    "ExtractionError",
    "Route",
    "detect_packages",
    "extract_notebook",
    "extract_url",
    "route_url",
]

MAX_DOWNLOAD_BYTES = 20 * 1024 * 1024


def _fetch(client: httpx.Client, urls: tuple[str, ...]) -> tuple[str, bytes]:
    """Download the first URL that answers 200; (url, body)."""
    last_error = "no URL to fetch"
    for url in urls:
        try:
            response = client.get(url, follow_redirects=True)
        except httpx.HTTPError as exc:
            raise ExtractionError(f"Could not fetch {url}: {exc}") from exc
        if response.status_code == 404 and len(urls) > 1:
            last_error = f"{url} not found"
            continue
        if response.status_code >= 400:
            raise ExtractionError(f"Fetching {url} returned HTTP {response.status_code}")
        if len(response.content) > MAX_DOWNLOAD_BYTES:
            raise ExtractionError(f"{url} is larger than {MAX_DOWNLOAD_BYTES // (1024 * 1024)} MB")
        return url, response.content
    raise ExtractionError(f"No README found: {last_error}")


def extract_notebook(filename: str, data: bytes) -> Extraction:
    """An uploaded notebook. `filename` becomes the extraction's source."""
    return parse_notebook(filename, data, source_kind=SourceKind.NOTEBOOK)


def extract_url(url: str, client: httpx.Client) -> Extraction:
    """Fetch and parse a tutorial URL. Raises ExtractionError when that is not possible."""
    route = route_url(url)

    if route.kind == "stackoverflow":
        return extract_stackoverflow(
            url, client, question_id=route.question_id, answer_id=route.answer_id
        )

    _, content = _fetch(client, route.fetch_urls)

    if route.kind == "notebook":
        return parse_notebook(url, content, source_kind=SourceKind.URL)

    if route.kind == "github_readme":
        title, raws = parse_markdown(content.decode("utf-8", errors="replace"))
        blocks = build_blocks(raws)
        return Extraction(
            source_kind=SourceKind.URL,
            source_format=SourceFormat.GITHUB_README,
            source=url,
            title=title,
            blocks=blocks,
            packages=detect_packages(blocks),
        )

    title, published = page_metadata(content, url)
    blocks = build_blocks(parse_html_blocks(content))
    return Extraction(
        source_kind=SourceKind.URL,
        source_format=SourceFormat.HTML,
        source=url,
        title=title,
        published=published,
        date_source=DateSource.PAGE if published else DateSource.UNKNOWN,
        blocks=blocks,
        packages=detect_packages(blocks),
    )
