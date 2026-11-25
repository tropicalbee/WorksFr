from unittest.mock import Mock

import httpx
import pytest

from app.extract import ExtractionError, check_url, extract_url, fetch


@pytest.mark.parametrize(
    "url",
    [
        "file:///x",
        "ftp://public.test/x",
        "javascript:alert(1)",
        "http://127.0.0.1",
        "http://localhost",
        "http://10.0.0.5",
        "http://169.254.169.254",
        "http://[::1]",
        "https://user:pass@public.test",
        "https://private.test",
    ],
)
def test_rejected_urls(url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fetch, "resolve_host", lambda host: ["127.0.0.1"])
    with pytest.raises(ExtractionError):
        check_url(url)


def test_redirects_are_checked(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        fetch,
        "resolve_host",
        lambda host: ["93.184.216.34"] if host == "public.test" else ["10.0.0.1"],
    )
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(302, headers={"location": "http://private.test/x"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ExtractionError, match="public"):
            extract_url("https://public.test/x", client)
    assert len(calls) == 1


def test_redirect_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fetch, "resolve_host", lambda host: ["93.184.216.34"])
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(302, headers={"location": "/again"}))
    ) as client:
        with pytest.raises(ExtractionError, match="redirect"):
            extract_url("https://public.test/x", client)


def test_stream_cap_stops_reading(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fetch, "resolve_host", lambda host: ["93.184.216.34"])
    consumed = []

    class Body(httpx.SyncByteStream):
        def __iter__(self):
            for _ in range(10):
                consumed.append(1)
                yield b"x" * 16

    extensions = {"network_stream": Mock(get_extra_info=lambda _: ("93.184.216.34", 443))}
    transport = httpx.MockTransport(
        lambda _: httpx.Response(200, stream=Body(), extensions=extensions)
    )
    with httpx.Client(transport=transport) as client:
        with pytest.raises(ExtractionError, match="larger"):
            fetch.fetch_url(client, "https://public.test", max_bytes=20)
    assert len(consumed) == 2


@pytest.mark.parametrize("peer", [("10.0.0.1", 443), ("1.1.1.1", 443), None])
def test_fetch_checks_peer_before_reading_body(
    peer: tuple[str, int] | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(fetch, "resolve_host", lambda _: ["93.184.216.34"])
    read = Mock(return_value=iter([b"content"]))

    class Body(httpx.SyncByteStream):
        def __iter__(self):
            return read()

    extensions = {"network_stream": Mock(get_extra_info=lambda _: peer)}
    transport = httpx.MockTransport(
        lambda _: httpx.Response(200, stream=Body(), extensions=extensions)
    )
    with httpx.Client(transport=transport) as client:
        with pytest.raises(ExtractionError, match="peer address"):
            fetch.fetch_url(client, "https://public.test")
    read.assert_not_called()
