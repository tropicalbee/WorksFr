"""Bounded downloads with public-address checks on each redirect hop."""

import ipaddress
import socket
from urllib.parse import urljoin, urlsplit

import httpx

from app.extract.errors import ExtractionError
from app.extract.safe_network import is_public_address

MAX_DOWNLOAD_BYTES = 20 * 1024 * 1024


def resolve_host(host: str) -> list[str]:
    return sorted({item[4][0] for item in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)})


def _checked_addresses(url: str) -> set[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    try:
        parsed = urlsplit(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise ExtractionError("URL must have an http or https scheme and a host")
        if parsed.username is not None or parsed.password is not None:
            raise ExtractionError("URL must not contain user credentials")
        _ = parsed.port
        addresses = {ipaddress.ip_address(address) for address in resolve_host(parsed.hostname)}
        if not addresses:
            raise ExtractionError("URL host has no public addresses")
        for ip in addresses:
            if not is_public_address(ip):
                raise ExtractionError("URL host must resolve only to public addresses")
        return addresses
    except (OSError, ValueError) as exc:
        raise ExtractionError("URL host could not be validated") from exc


def check_url(url: str) -> None:
    _checked_addresses(url)


def _check_peer(
    response: httpx.Response, addresses: set[ipaddress.IPv4Address | ipaddress.IPv6Address]
) -> None:
    stream = response.extensions.get("network_stream")
    if stream is None and response.is_stream_consumed:
        # An in-process transport can return already-buffered content without an open socket.
        return
    try:
        peer = stream.get_extra_info("server_addr") if stream is not None else None
        if peer and ipaddress.ip_address(peer[0]) in addresses:
            return
    except (AttributeError, OSError, TypeError, ValueError):
        pass
    raise ExtractionError("The connection peer address did not match the validated URL.")


def fetch_url(
    client: httpx.Client,
    url: str,
    *,
    max_bytes: int = MAX_DOWNLOAD_BYTES,
) -> tuple[int, bytes]:
    response = fetch_response(client, url, max_bytes=max_bytes)
    return response.status_code, response.content


def fetch_response(
    client: httpx.Client,
    url: str,
    *,
    max_bytes: int,
    headers: dict[str, str] | None = None,
    timeout: float | None = None,
    head: bool = False,
) -> httpx.Response:
    """Read a bounded response after validating the URL and connected peer at each hop."""
    for hop in range(6):
        addresses = _checked_addresses(url)
        try:
            with client.stream(
                "HEAD" if head else "GET",
                url,
                follow_redirects=False,
                headers=headers,
                timeout=timeout if timeout is not None else httpx.USE_CLIENT_DEFAULT,
            ) as response:
                _check_peer(response, addresses)
                if response.status_code in (301, 302, 303, 307, 308):
                    location = response.headers.get("location")
                    if not location:
                        raise ExtractionError("Redirect has no Location header")
                    if hop == 5:
                        raise ExtractionError("Too many redirects (maximum 5)")
                    url = urljoin(url, location)
                    continue
                if head:
                    return httpx.Response(response.status_code, headers=response.headers)
                data = bytearray()
                for chunk in response.iter_bytes():
                    if len(data) + len(chunk) > max_bytes:
                        raise ExtractionError(f"Download is larger than {max_bytes} bytes")
                    data.extend(chunk)
                response_headers = response.headers.copy()
                # iter_bytes has already decoded the body.
                for header in ("content-encoding", "content-length", "transfer-encoding"):
                    response_headers.pop(header, None)
                return httpx.Response(
                    response.status_code,
                    headers=response_headers,
                    content=bytes(data),
                    request=response.request,
                    default_encoding=response.default_encoding,
                )
        except httpx.HTTPError as exc:
            raise ExtractionError(f"Could not fetch URL: {type(exc).__name__}") from exc
    raise ExtractionError("Too many redirects")
