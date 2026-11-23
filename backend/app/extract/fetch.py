"""Bounded downloads with public-address checks on each redirect hop."""

import ipaddress
import socket
from urllib.parse import urljoin, urlsplit

import httpx

from app.extract.errors import ExtractionError

MAX_DOWNLOAD_BYTES = 20 * 1024 * 1024


def resolve_host(host: str) -> list[str]:
    return sorted({item[4][0] for item in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)})


def check_url(url: str) -> None:
    try:
        parsed = urlsplit(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise ExtractionError("URL must have an http or https scheme and a host")
        if parsed.username is not None or parsed.password is not None:
            raise ExtractionError("URL must not contain user credentials")
        _ = parsed.port
        addresses = resolve_host(parsed.hostname)
        if not addresses:
            raise ExtractionError("URL host has no public addresses")
        for address in addresses:
            ip = ipaddress.ip_address(address)
            if not ip.is_global or ip.is_multicast or ip.is_reserved or ip.is_unspecified:
                raise ExtractionError("URL host must resolve only to public addresses")
    except (OSError, ValueError) as exc:
        raise ExtractionError("URL host could not be validated") from exc


def fetch_url(
    client: httpx.Client,
    url: str,
    *,
    max_bytes: int = MAX_DOWNLOAD_BYTES,
) -> tuple[int, bytes]:
    for hop in range(6):
        check_url(url)
        try:
            with client.stream("GET", url, follow_redirects=False) as response:
                if response.status_code in (301, 302, 303, 307, 308):
                    location = response.headers.get("location")
                    if not location:
                        raise ExtractionError("Redirect has no Location header")
                    if hop == 5:
                        raise ExtractionError("Too many redirects (maximum 5)")
                    url = urljoin(url, location)
                    continue
                data = bytearray()
                for chunk in response.iter_bytes():
                    if len(data) + len(chunk) > max_bytes:
                        raise ExtractionError(f"Download is larger than {max_bytes} bytes")
                    data.extend(chunk)
                return response.status_code, bytes(data)
        except httpx.HTTPError as exc:
            raise ExtractionError(f"Could not fetch URL: {type(exc).__name__}") from exc
    raise ExtractionError("Too many redirects")
