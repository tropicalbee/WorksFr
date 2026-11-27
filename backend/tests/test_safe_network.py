from unittest.mock import Mock

import httpcore
import pytest

from app.extract.errors import ExtractionError
from app.extract.fetch import check_url, fetch_response
from app.extract.safe_network import PublicNetworkBackend, make_safe_client


@pytest.mark.parametrize(
    "addresses",
    [
        [],
        ["127.0.0.1"],
        ["10.0.0.5"],
        ["169.254.169.254"],
        ["192.168.1.1"],
        ["224.0.0.1"],
        ["::1"],
        ["fe80::1"],
        ["fc00::1"],
        ["::ffff:127.0.0.1"],
        ["93.184.216.34", "10.0.0.5"],
    ],
)
def test_non_public_addresses_do_not_reach_the_inner_backend(addresses: list[str]) -> None:
    inner = Mock(spec=httpcore.NetworkBackend)
    network = PublicNetworkBackend(resolver=lambda _: addresses, inner=inner)
    with pytest.raises(httpcore.ConnectError):
        network.connect_tcp("tutorial.example", 443)
    inner.connect_tcp.assert_not_called()


def test_validated_address_is_the_actual_connect_target() -> None:
    inner = Mock(spec=httpcore.NetworkBackend)
    resolve = Mock(return_value=["93.184.216.34", "8.8.8.8"])
    network = PublicNetworkBackend(resolver=resolve, inner=inner)
    assert network.connect_tcp("tutorial.example", 443, timeout=2) is inner.connect_tcp.return_value
    assert inner.connect_tcp.call_args.args == ("93.184.216.34", 443)
    with pytest.raises(httpcore.ConnectError):
        network.connect_tcp("127.0.0.1", 443)
    assert resolve.call_count == 1


def test_fetch_turns_public_preflight_and_private_connect_into_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = "https://tutorial.example/"
    monkeypatch.setattr("app.extract.fetch.resolve_host", lambda _: ["93.184.216.34"])
    check_url(url)
    inner = Mock(spec=httpcore.NetworkBackend)
    with make_safe_client(resolver=lambda _: ["::ffff:127.0.0.1"], inner=inner) as client:
        with pytest.raises(ExtractionError, match="ConnectError"):
            fetch_response(client, url, max_bytes=10)
    inner.connect_tcp.assert_not_called()
