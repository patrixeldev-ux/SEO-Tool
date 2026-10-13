"""Reading a live page the way a crawler without JavaScript sees it.

Used for three things: the ownership file check, the "before" snapshot taken right before a deploy, and the
verification after one. Every hop is checked against the private-address guard (unless the development flag
`DEPLOY_ALLOW_PRIVATE_HOSTS` is on) so a site record can never be used to probe the server's own network.
"""

from __future__ import annotations

import asyncio
import ipaddress
import secrets
import socket
from dataclasses import dataclass, field
from typing import Protocol
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import httpx

from searchos.core.config import get_settings
from searchos.crawl import USER_AGENT

MAX_BYTES = 3_000_000
MAX_REDIRECTS = 5
TIMEOUT = 15.0


@dataclass(slots=True)
class LiveResponse:
    url: str
    status: int | None = None
    text: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    error: str | None = None


class PageFetcher(Protocol):
    async def get(self, url: str, *, no_cache: bool = False) -> LiveResponse: ...


def cache_busted(url: str) -> str:
    """The same URL with a throw-away query parameter, so a CDN cannot answer from a stored copy."""
    parts = urlsplit(url)
    query = [*parse_qsl(parts.query, keep_blank_values=True), ("searchos_verify", secrets.token_hex(4))]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))


async def _host_is_public(host: str) -> bool:
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except OSError:
        return False
    for info in infos:
        ip = ipaddress.ip_address(str(info[4][0]).split("%")[0])
        if not ip.is_global:
            return False
    return True


class HttpPageFetcher:
    """Plain `httpx` GET (no JavaScript), manual redirects, size cap, private-address guard."""

    def __init__(self, *, allow_private_hosts: bool | None = None, timeout: float = TIMEOUT) -> None:
        self.allow_private = (
            get_settings().deploy_allow_private_hosts if allow_private_hosts is None else allow_private_hosts
        )
        self.timeout = timeout

    async def get(self, url: str, *, no_cache: bool = False) -> LiveResponse:
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.5",
        }
        target = cache_busted(url) if no_cache else url
        if no_cache:
            headers["Cache-Control"] = "no-cache"
            headers["Pragma"] = "no-cache"
        current = target
        try:
            async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=False) as client:
                for _hop in range(MAX_REDIRECTS + 1):
                    parts = urlsplit(current)
                    if parts.scheme not in ("http", "https") or not parts.hostname:
                        return LiveResponse(url=current, error="not an http(s) address")
                    if not self.allow_private and not await _host_is_public(parts.hostname):
                        return LiveResponse(url=current, error="the address is private or does not resolve")
                    resp = await client.get(current, headers=headers)
                    if resp.status_code in (301, 302, 303, 307, 308) and resp.headers.get("location"):
                        current = urljoin(current, resp.headers["location"])
                        continue
                    body = resp.content[:MAX_BYTES]
                    return LiveResponse(
                        url=current,
                        status=resp.status_code,
                        text=body.decode(resp.encoding or "utf-8", errors="replace"),
                        headers={k.lower(): v for k, v in resp.headers.items()},
                    )
        except httpx.HTTPError as exc:
            return LiveResponse(url=current, error=f"{type(exc).__name__}: could not fetch the page")
        return LiveResponse(url=current, error="too many redirects")


def default_fetcher() -> PageFetcher:
    return HttpPageFetcher()
