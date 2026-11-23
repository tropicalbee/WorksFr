"""Extraction tests against the saved fixtures. No live network: httpx.MockTransport serves them."""

import html
import json
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.extract import ExtractionError, extract_notebook, extract_url
from app.extract.blocks import first_line
from app.models import DateSource, SourceFormat, SourceKind, StepKind

FIXTURES = Path(__file__).parent / "fixtures"
OBSOLETE = {"openai_so"}
NAMES = sorted(
    p.name.removesuffix(".expected.json")
    for p in FIXTURES.glob("*.expected.json")
    if p.name.removesuffix(".expected.json") not in OBSOLETE
)

# Documented exception: openai_tds does `from sk import my_sk`, a local module. Offline the
# extractor cannot tell it from a PyPI package, so ARCHITECTURE.md says to list it.
EXTRA_PACKAGES = {"openai_tds": ["sk"]}


def _fixture_file(name: str) -> Path:
    matches = [p for p in FIXTURES.glob(f"{name}.*") if not p.name.endswith(".expected.json")]
    assert len(matches) == 1, f"expected one data file for {name}, found {matches}"
    return matches[0]


def _client(name: str, expected: dict[str, Any]) -> httpx.Client:
    """A client whose only reachable pages are this fixture's."""
    data = _fixture_file(name).read_bytes()
    source = expected["source_url"]

    def handler(request: httpx.Request) -> httpx.Response:
        url = request.url
        if url.host == "api.stackexchange.com":
            if url.path.startswith("/2.3/answers/"):
                return httpx.Response(
                    200, content=data, headers={"content-type": "application/json"}
                )
            if url.path.startswith("/2.3/questions/"):
                question = {"items": [{"title": html.escape(expected["title"])}]}
                return httpx.Response(200, json=question)
        if str(url) == source:
            return httpx.Response(200, content=data)
        return httpx.Response(404, text=f"unexpected request: {url}")

    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.mark.parametrize("name", NAMES)
def test_fixture_matches_expected(name: str) -> None:
    expected = json.loads((FIXTURES / f"{name}.expected.json").read_text(encoding="utf-8"))
    with _client(name, expected) as client:
        result = extract_url(expected["source_url"], client)

    steps = expected["steps"]
    assert len(result.blocks) == len(steps)
    assert [b.kind.value for b in result.blocks] == [s["kind"] for s in steps]
    for block, step in zip(result.blocks, steps, strict=True):
        assert first_line(block.code) == step["starts_with"], f"block {block.idx}"

    assert result.packages == sorted([*expected["packages"], *EXTRA_PACKAGES.get(name, [])])
    assert result.title == expected["title"]
    want_date = date.fromisoformat(expected["published"]) if expected["published"] else None
    assert result.published == want_date
    assert result.date_source == (DateSource.PAGE if want_date else DateSource.UNKNOWN)
    assert result.source_format.value == expected["format"]
    assert result.source == expected["source_url"]
    assert [b.idx for b in result.blocks] == list(range(len(result.blocks)))


@pytest.mark.parametrize("name", NAMES)
def test_runnable_code_follows_kind(name: str) -> None:
    expected = json.loads((FIXTURES / f"{name}.expected.json").read_text(encoding="utf-8"))
    with _client(name, expected) as client:
        result = extract_url(expected["source_url"], client)
    for block in result.blocks:
        if block.kind in (StepKind.SHELL, StepKind.OUTPUT, StepKind.OTHER):
            assert block.runnable_code is None
        else:
            assert block.runnable_code
            assert block.runnable_code.strip()


def test_sklearn_output_is_not_runnable() -> None:
    """Output pasted after the code (marked with <em>) stays in `code` but is not run."""
    expected = json.loads((FIXTURES / "sklearn_boston.expected.json").read_text(encoding="utf-8"))
    with _client("sklearn_boston", expected) as client:
        result = extract_url(expected["source_url"], client)
    block = result.blocks[2]
    assert "(506, 14)" in block.code
    assert block.runnable_code == "print(X.shape)"


def test_openai_tds_joins_line_spans() -> None:
    expected = json.loads((FIXTURES / "openai_tds.expected.json").read_text(encoding="utf-8"))
    with _client("openai_tds", expected) as client:
        result = extract_url(expected["source_url"], client)
    lines = result.blocks[0].code.split("\n")
    assert lines[0] == "import openai"
    assert lines[1].startswith("from sk import my_sk")
    assert result.blocks[1].kind is StepKind.OUTPUT


def test_keras_notebook_magics_and_context() -> None:
    expected = json.loads((FIXTURES / "keras_notebook.expected.json").read_text(encoding="utf-8"))
    with _client("keras_notebook", expected) as client:
        result = extract_url(expected["source_url"], client)
    assert result.source_kind is SourceKind.URL
    assert result.source_format is SourceFormat.NOTEBOOK
    first = result.blocks[0]
    assert "!pip install pillow" in first.code
    assert first.runnable_code is not None
    assert "!" not in first.runnable_code
    assert "Data loading" in first.context
    assert result.blocks[1].context == ""  # no Markdown cell between cells 7 and 8


def test_upload_notebook_uses_filename() -> None:
    data = (FIXTURES / "keras_notebook.ipynb").read_bytes()
    result = extract_notebook("my_notebook.ipynb", data)
    assert result.source_kind is SourceKind.NOTEBOOK
    assert result.source == "my_notebook.ipynb"
    assert result.published is None
    assert result.date_source is DateSource.UNKNOWN


def test_invalid_notebook_raises() -> None:
    with pytest.raises(ExtractionError):
        extract_notebook("bad.ipynb", b"not json at all")
