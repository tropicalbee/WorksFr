"""Connect tutorial fetches directly to validated public IP addresses."""

import ipaddress
from collections.abc import Callable, Iterable

import httpcore
import httpx

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
SocketOption = tuple[int, int, int | bytes | bytearray] | tuple[int, int, None, int]


def is_public_address(address: IPAddress) -> bool:
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return address.is_global and not (
        address.is_multicast or address.is_reserved or address.is_unspecified
    )


class PublicNetworkBackend(httpcore.NetworkBackend):
    def __init__(
        self,
        *,
        resolver: Callable[[str], list[str]] | None = None,
        inner: httpcore.NetworkBackend | None = None,
    ) -> None:
        from app.extract.fetch import resolve_host

        self._resolve = resolver if resolver is not None else resolve_host
        self._inner = inner if inner is not None else httpcore.SyncBackend()

    def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[SocketOption] | None = None,
    ) -> httpcore.NetworkStream:
        try:
            try:
                literal = ipaddress.ip_address(host)
            except ValueError:
                addresses = [ipaddress.ip_address(value) for value in self._resolve(host)]
            else:
                addresses = [literal]
        except (OSError, ValueError):
            raise httpcore.ConnectError("Could not resolve a public address") from None
        if not addresses or not all(map(is_public_address, addresses)):
            raise httpcore.ConnectError("URL host must resolve only to public addresses")
        return self._inner.connect_tcp(
            str(addresses[0]),
            port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,
        )


class _PublicTransport(httpx.HTTPTransport):
    def __init__(self, network: PublicNetworkBackend) -> None:
        # Reuse HTTPTransport's request/response adapters and exception mapping.
        self._pool = httpcore.ConnectionPool(
            ssl_context=httpx.create_ssl_context(trust_env=False),
            network_backend=network,
            max_connections=100,
            max_keepalive_connections=20,
            keepalive_expiry=5,
        )


def make_safe_client(
    *,
    timeout: float = 30,
    resolver: Callable[[str], list[str]] | None = None,
    inner: httpcore.NetworkBackend | None = None,
) -> httpx.Client:
    network = PublicNetworkBackend(resolver=resolver, inner=inner)
    return httpx.Client(transport=_PublicTransport(network), timeout=timeout, trust_env=False)
