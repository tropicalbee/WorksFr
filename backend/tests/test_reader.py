"""Fallback page reader: only for pages that are empty because JavaScript renders them."""

import httpx
import pytest
from pydantic import SecretStr

from app.config import Settings
from app.extract import ExtractionError, extract_url
from app.models import Extraction
from app.pipeline import _redact

URL = "https://example.com/post"
SHELL = "<html><head><title>App</title></head><body><div id='root'></div></body></html>"
CODE_PAGE = "<html><body><pre><code>import numpy\n</code></pre></body></html>"
FIRECRAWL_KEY = "fc-secret-key-123"
JINA_KEY = "jina-secret-key-456"

# hand-made from docs: https://docs.firecrawl.dev/api-reference/endpoint/scrape
FIRECRAWL_OK = {
    "success": True,
    "data": {
        "markdown": "# Rendered tutorial\n\nInstall it.\n\n```python\nimport pandas\n```\n",
        "metadata": {"title": "Rendered tutorial", "sourceURL": URL, "url": URL, "statusCode": 200},
    },
}

# hand-made from docs: https://github.com/jina-ai/reader (Accept: application/json)
JINA_OK = {
    "code": 200,
    "status": 20000,
    "data": {
        "title": "Jina tutorial",
        "url": URL,
        "content": "# Jina tutorial\n\n```python\nimport requests\n```\n",
    },
}


@pytest.fixture(autouse=True)
def public_resolver(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.extract.fetch.resolve_host", lambda host: ["93.184.216.34"])


def _settings(**kwargs: object) -> Settings:
    defaults: dict[str, object] = {
        "firecrawl_api_key": SecretStr(FIRECRAWL_KEY),
        "jina_api_key": SecretStr(JINA_KEY),
    }
    return Settings(_env_file=None, **{**defaults, **kwargs})  # type: ignore[arg-type]


def _run(
    page: httpx.Response,
    settings: Settings,
    firecrawl: httpx.Response | None = None,
    jina: httpx.Response | None = None,
) -> tuple[object, list[str]]:
    """Run extract_url; returns (result or exception, hosts the reader was asked)."""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if host == "api.firecrawl.dev":
            calls.append("firecrawl")
            assert request.headers["authorization"] == f"Bearer {FIRECRAWL_KEY}"
            return firecrawl or httpx.Response(500)
        if host == "r.jina.ai":
            calls.append("jina")
            return jina or httpx.Response(500)
        assert host == "example.com"
        return page

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        try:
            return extract_url(URL, client, settings), calls
        except ExtractionError as exc:
            return exc, calls


def test_js_page_with_no_code_is_read_through_firecrawl() -> None:
    result, calls = _run(
        httpx.Response(200, text=SHELL), _settings(), httpx.Response(200, json=FIRECRAWL_OK)
    )
    assert calls == ["firecrawl"]
    assert result.fetched_via == "firecrawl"  # type: ignore[attr-defined]
    assert result.source == URL  # type: ignore[attr-defined]
    assert result.title == "Rendered tutorial"  # type: ignore[attr-defined]
    assert [b.code for b in result.blocks] == ["import pandas"]  # type: ignore[attr-defined]
    assert result.packages == ["pandas"]  # type: ignore[attr-defined]


@pytest.mark.parametrize("status", [401, 403, 429])
def test_blocked_page_never_goes_to_a_reader(status: int) -> None:
    result, calls = _run(
        httpx.Response(status),
        _settings(),
        httpx.Response(200, json=FIRECRAWL_OK),
        httpx.Response(200, json=JINA_OK),
    )
    assert calls == []
    assert str(result) == (
        "example.com blocks automated reading, so WorksFr can't check this page. "
        "Paste the code or upload the notebook instead."
    )


def test_no_reader_call_when_the_page_has_code() -> None:
    result, calls = _run(httpx.Response(200, text=CODE_PAGE), _settings())
    assert calls == []
    assert result.fetched_via is None  # type: ignore[attr-defined]


def test_providers_are_tried_in_the_configured_order() -> None:
    page = httpx.Response(200, text=SHELL)
    jina_first = _settings(reader_providers="jina,firecrawl")
    result, calls = _run(page, jina_first, httpx.Response(200, json=FIRECRAWL_OK))
    assert calls == ["jina", "firecrawl"]
    assert result.fetched_via == "firecrawl"  # type: ignore[attr-defined]

    result, calls = _run(
        httpx.Response(200, text=SHELL),
        _settings(),
        httpx.Response(500),
        httpx.Response(200, json=JINA_OK),
    )
    assert calls == ["firecrawl", "jina"]
    assert result.fetched_via == "jina"  # type: ignore[attr-defined]
    assert result.title == "Jina tutorial"  # type: ignore[attr-defined]


def test_missing_firecrawl_key_skips_to_jina() -> None:
    result, calls = _run(
        httpx.Response(200, text=SHELL),
        _settings(firecrawl_api_key=SecretStr("")),
        jina=httpx.Response(200, json=JINA_OK),
    )
    assert calls == ["jina"]
    assert result.fetched_via == "jina"  # type: ignore[attr-defined]


def test_jina_works_without_a_key() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "r.jina.ai":
            seen.append(request)
            return httpx.Response(200, json=JINA_OK)
        return httpx.Response(200, text=SHELL)

    settings = _settings(reader_providers="jina", jina_api_key=SecretStr(""))
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = extract_url(URL, client, settings)
    assert result.fetched_via == "jina"
    assert "authorization" not in seen[0].headers
    assert str(seen[0].url) == f"https://r.jina.ai/{URL}"
    assert seen[0].headers["accept"] == "application/json"


def test_reader_login_page_gives_the_login_message() -> None:
    login = {
        "success": True,
        "data": {
            "markdown": "# Sign in\n\nEmail\n\nPassword\n\nLog in to continue.\n",
            "metadata": {"title": "Sign in", "url": "https://example.com/login", "statusCode": 200},
        },
    }
    result, calls = _run(
        httpx.Response(200, text=SHELL), _settings(), httpx.Response(200, json=login)
    )
    assert calls == ["firecrawl"]
    assert isinstance(result, ExtractionError)
    assert "behind a login" in str(result)


def test_reader_that_found_a_blocked_target_is_not_a_result() -> None:
    blocked = {"success": True, "data": {"markdown": "", "metadata": {"statusCode": 403}}}
    result, _ = _run(
        httpx.Response(200, text=SHELL), _settings(), httpx.Response(200, json=blocked)
    )
    assert isinstance(result, ExtractionError)
    assert "blocks automated reading" in str(result)


def test_every_reader_failing_keeps_the_empty_page_result() -> None:
    result, calls = _run(httpx.Response(200, text=SHELL), _settings())
    assert calls == ["firecrawl", "jina"]
    assert result.blocks == []  # type: ignore[attr-defined]
    assert result.fetched_via is None  # type: ignore[attr-defined]


def test_reader_that_returns_no_code_leaves_the_page_empty() -> None:
    empty = {"success": True, "data": {"markdown": "# Just prose\n", "metadata": {}}}
    result, _ = _run(
        httpx.Response(200, text=SHELL),
        _settings(reader_providers="firecrawl"),
        httpx.Response(200, json=empty),
    )
    assert result.blocks == []  # type: ignore[attr-defined]
    assert result.fetched_via is None  # type: ignore[attr-defined]


def test_no_settings_means_no_reader() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "example.com"
        return httpx.Response(200, text=SHELL)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert extract_url(URL, client).blocks == []


def test_private_target_is_never_sent_to_a_reader(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.extract import reader

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("reader must not be called")

    monkeypatch.setattr("app.extract.fetch.resolve_host", lambda host: ["10.0.0.5"])
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert reader.read_page("https://internal.test/x", _settings(), client) is None


def test_keys_never_appear_in_errors_and_are_redacted() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "example.com":
            return httpx.Response(200, text=SHELL)
        raise httpx.ConnectError(f"failed {FIRECRAWL_KEY} {JINA_KEY}")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = extract_url(URL, client, _settings())
    assert FIRECRAWL_KEY not in repr(result)
    assert JINA_KEY not in repr(result)
    text = _redact(f"x {FIRECRAWL_KEY} y {JINA_KEY}", _settings())
    assert FIRECRAWL_KEY not in text
    assert JINA_KEY not in text


def test_reader_settings_defaults() -> None:
    settings = Settings(_env_file=None)
    assert settings.reader_providers == ["firecrawl", "jina"]
    assert settings.firecrawl_base_url == "https://api.firecrawl.dev"
    assert settings.jina_reader_base_url == "https://r.jina.ai/"
    assert settings.firecrawl_api_key.get_secret_value() == ""


def _extract_firecrawl_markdown(markdown: str) -> Extraction:
    result, calls = _run(
        httpx.Response(200, text=SHELL),
        _settings(reader_providers="firecrawl"),
        httpx.Response(200, json={"success": True, "data": {"markdown": markdown}}),
    )
    assert calls == ["firecrawl"]
    assert isinstance(result, Extraction)
    return result


def test_reader_joins_split_python_fences() -> None:
    # Copies the shape of the real GeeksforGeeks Firecrawl reply from 2026-11-25.
    result = _extract_firecrawl_markdown(
        "# Pandas append\n\n"
        "Append one dataframe to another.\n\n"
        "```python\nimport pandas as pd\n```\n"
        "```python\n\u200b\n```\n\n"
        "```python\na = pd.DataFrame({'item': [1]})\n```\n\n"
        "```python\n\u200b\n```\n"
        "```python\nres = a.append(b)\n```\n"
    )

    assert result.fetched_via == "firecrawl"
    assert result.title == "Pandas append"
    assert [block.code for block in result.blocks] == [
        "import pandas as pd\na = pd.DataFrame({'item': [1]})\nres = a.append(b)"
    ]
    assert result.blocks[0].context == "# Pandas append\nAppend one dataframe to another."


def test_reader_removes_invisible_characters_and_keeps_blank_lines() -> None:
    result = _extract_firecrawl_markdown(
        "```python\n\ufeffim\u200bport pandas as pd\n  \u2060 \n"
        "a = pd.Data\u200cFrame()\u200d\n```\n"
    )

    assert [block.code for block in result.blocks] == ["import pandas as pd\n\na = pd.DataFrame()"]


def test_reader_keeps_fences_separated_by_prose() -> None:
    result = _extract_firecrawl_markdown(
        "```python\nimport pandas as pd\n```\n\n"
        "Create a dataframe.\n\n"
        "```python\na = pd.DataFrame()\n```\n"
    )

    assert [block.code for block in result.blocks] == ["import pandas as pd", "a = pd.DataFrame()"]
    assert result.blocks[1].context == "Create a dataframe."


def test_reader_keeps_fences_with_different_hints_separate() -> None:
    result = _extract_firecrawl_markdown(
        "```bash\npip install pandas\n```\n```python\nimport pandas as pd\n```\n"
    )

    assert [block.code for block in result.blocks] == ["pip install pandas", "import pandas as pd"]


def test_reader_joins_untyped_fences_across_an_image_and_blank_lines() -> None:
    result = _extract_firecrawl_markdown(
        "```\nimport pandas as pd\n```\n\n"
        "![Example](example.png)\n\n"
        "```\na = pd.DataFrame()\n```\n"
        "```python\nprint(a)\n```\n"
    )

    assert [block.code for block in result.blocks] == [
        "import pandas as pd\na = pd.DataFrame()",
        "print(a)",
    ]


def test_reader_treats_invisible_only_fences_as_no_code() -> None:
    result = _extract_firecrawl_markdown(
        "# Just prose\n\n```python\n\u200b\n```\n\n```python\n \u200c\u200d\u2060\ufeff\n```\n"
    )

    assert result.blocks == []
    assert result.fetched_via is None


def test_reader_keeps_prose_before_an_invisible_only_fence() -> None:
    result = _extract_firecrawl_markdown(
        "```python\nimport pandas as pd\n```\n\n"
        "Output:\n\n"
        "```python\n\u200b\n```\n"
        "```python\na = pd.DataFrame()\n```\n"
    )

    assert [block.code for block in result.blocks] == ["import pandas as pd", "a = pd.DataFrame()"]
    assert [block.context for block in result.blocks] == ["", "Output:"]


def test_reader_preserves_prose_order_across_multiple_dropped_fences() -> None:
    result = _extract_firecrawl_markdown(
        "Use pandas.\n\n"
        "```python\n\u200b\n```\n\n"
        "Create a dataframe.\n\n"
        "```python\n\u2060\n```\n\n"
        "Start with one item.\n\n"
        "```python\nimport pandas as pd\n```\n"
        "```python\na = pd.DataFrame({'item': [1]})\n```\n"
    )

    assert [block.code for block in result.blocks] == [
        "import pandas as pd\na = pd.DataFrame({'item': [1]})"
    ]
    assert result.blocks[0].context == "Use pandas.\nCreate a dataframe.\nStart with one item."
