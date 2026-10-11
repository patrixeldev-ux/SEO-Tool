"""HTTP fetching for the crawler.

* One shared `httpx.AsyncClient` (HTTP/2 where the server offers it), our own User-Agent, 15 s timeout.
* Redirects are followed by hand so the full chain is recorded, loops are detected and every hop is checked
  against the allowed hosts and robots.txt. A redirect that leaves the allowed hosts is recorded but never
  followed: the crawler does not fetch anything outside the hosts it was given.
* Bodies are only downloaded for HTML (and for robots/sitemaps, which ask for it).
* A per-host rate limiter keeps us polite (default 2 requests per second per host).
* Unless `allow_private_hosts` is set (tests and the dev helper only), hosts that resolve to private,
  loopback or link-local addresses are refused, so a registered domain cannot be used to probe an internal
  network. (Best effort: resolution is checked right before each request.)
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
import time
import zlib
from dataclasses import dataclass, field
from typing import Literal
from urllib.parse import urljoin, urlsplit

import httpx

from searchos.crawl import ROBOTS_PRODUCT, USER_AGENT
from searchos.crawl.ratelimit import HostRateLimiter
from searchos.crawl.robots import RobotsRules, rules_from_response
from searchos.crawl.urls import InvalidUrlError, normalise_netloc, normalise_url

log = logging.getLogger(__name__)

REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
HTML_TYPES = ("text/html", "application/xhtml+xml")
ReadBody = Literal["html", "always", "never"]


@dataclass(frozen=True, slots=True)
class FetcherConfig:
    allowed_hosts: frozenset[str]
    user_agent: str = USER_AGENT
    robots_token: str = ROBOTS_PRODUCT
    timeout: float = 15.0
    max_redirects: int = 5
    rate_per_host: float = 2.0
    max_html_bytes: int = 5 * 1024 * 1024
    respect_robots: bool = True
    allow_private_hosts: bool = False
    max_crawl_delay: float = 10.0

    @classmethod
    def for_hosts(cls, hosts: list[str] | set[str] | frozenset[str], **kwargs: object) -> FetcherConfig:
        return cls(allowed_hosts=frozenset(normalise_netloc(h) for h in hosts), **kwargs)  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class Hop:
    url: str
    status: int
    location: str


@dataclass(slots=True)
class FetchResult:
    requested_url: str
    final_url: str
    hops: list[Hop] = field(default_factory=list)
    # Of the FINAL response (None when the chain ended in an error before a final response arrived).
    status_code: int | None = None
    headers: dict[str, str] = field(default_factory=dict)
    content_type: str | None = None
    ttfb_ms: int | None = None
    body: bytes | None = None
    truncated: bool = False
    # blocked_by_robots | outside_allowed_hosts | external_redirect | redirect_loop | too_many_redirects
    # | timeout | connect_error | dns_error | private_address | invalid_redirect
    error: str | None = None
    error_detail: str | None = None

    @property
    def redirected(self) -> bool:
        return bool(self.hops)

    @property
    def is_html(self) -> bool:
        return is_html_type(self.content_type)


def is_html_type(content_type: str | None) -> bool:
    return bool(content_type) and content_type.split(";")[0].strip().lower() in HTML_TYPES  # type: ignore[union-attr]


def gunzip_limited(data: bytes, limit: int) -> bytes:
    """Decompress gzip data, refusing to expand beyond `limit` bytes (zip-bomb guard)."""
    decompressor = zlib.decompressobj(wbits=31)
    out = decompressor.decompress(data, limit + 1)
    if len(out) > limit or decompressor.unconsumed_tail:
        raise ValueError("decompressed data too large")
    return out


class Fetcher:
    def __init__(
        self,
        config: FetcherConfig,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        limiter: HostRateLimiter | None = None,
    ) -> None:
        if not config.allowed_hosts:
            raise ValueError("at least one allowed host is required")
        self.config = config
        self.limiter = limiter or HostRateLimiter(config.rate_per_host)
        self._client = httpx.AsyncClient(
            transport=transport,
            http2=transport is None,
            timeout=httpx.Timeout(config.timeout),
            follow_redirects=False,
            headers={
                "User-Agent": config.user_agent,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.5",
                "Accept-Encoding": "gzip, deflate",
            },
        )
        self._robots: dict[str, RobotsRules] = {}
        self._robots_locks: dict[str, asyncio.Lock] = {}
        self._public_hosts: dict[str, bool] = {}

    async def __aenter__(self) -> Fetcher:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    # ---- allowed hosts / network safety ----------------------------------------------------------
    def is_allowed(self, url: str) -> bool:
        try:
            return urlsplit(normalise_url(url)).netloc in self.config.allowed_hosts
        except InvalidUrlError:
            return False

    async def _host_is_public(self, host: str) -> bool:
        cached = self._public_hosts.get(host)
        if cached is not None:
            return cached
        loop = asyncio.get_running_loop()
        infos = await loop.getaddrinfo(host, None, type=socket.SOCK_STREAM)  # OSError -> caller
        public = True
        for info in infos:
            ip = ipaddress.ip_address(str(info[4][0]).split("%")[0])
            if not ip.is_global:
                public = False
        self._public_hosts[host] = public
        return public

    # ---- robots.txt ------------------------------------------------------------------------------
    async def robots_for(self, url: str) -> RobotsRules:
        """Rules for the host of `url` (fetched once per host and cached)."""
        parts = urlsplit(normalise_url(url))
        key = f"{parts.scheme}://{parts.netloc}"
        lock = self._robots_locks.setdefault(key, asyncio.Lock())
        async with lock:
            cached = self._robots.get(key)
            if cached is not None:
                return cached
            result = await self.fetch(f"{key}/robots.txt", read_body="always", respect_robots=False)
            if result.error or result.status_code is None:
                rules = RobotsRules.error()
            else:
                rules = rules_from_response(result.status_code, result.body)
            delay = rules.crawl_delay(self.config.robots_token)
            if delay:
                self.limiter.set_min_interval(parts.netloc, min(delay, self.config.max_crawl_delay))
            self._robots[key] = rules
            return rules

    # ---- the fetch itself ------------------------------------------------------------------------
    async def fetch(
        self,
        url: str,
        *,
        read_body: ReadBody = "html",
        max_bytes: int | None = None,
        respect_robots: bool | None = None,
    ) -> FetchResult:
        check_robots = self.config.respect_robots if respect_robots is None else respect_robots
        limit = max_bytes or self.config.max_html_bytes
        current = normalise_url(url)
        result = FetchResult(requested_url=current, final_url=current)
        seen = {current}

        while True:
            if not self.is_allowed(current):
                result.error = "external_redirect" if result.hops else "outside_allowed_hosts"
                result.final_url = current
                return result
            if check_robots:
                rules = await self.robots_for(current)
                if not rules.can_fetch(current, self.config.robots_token):
                    result.error = "blocked_by_robots"
                    result.final_url = current
                    return result
            host = urlsplit(current).netloc
            if not self.config.allow_private_hosts:
                try:
                    public = await self._host_is_public(urlsplit(current).hostname or "")
                except OSError as exc:
                    result.error, result.error_detail, result.final_url = "dns_error", str(exc), current
                    return result
                if not public:
                    result.error, result.final_url = "private_address", current
                    return result

            await self.limiter.wait(host)
            started = time.perf_counter()
            try:
                async with self._client.stream("GET", current) as resp:
                    ttfb = round((time.perf_counter() - started) * 1000)
                    status = resp.status_code
                    location = resp.headers.get("location")
                    if status in REDIRECT_STATUSES and location:
                        try:
                            nxt = normalise_url(urljoin(current, location))
                        except InvalidUrlError:
                            result.error, result.final_url = "invalid_redirect", current
                            result.status_code = status
                            return result
                        result.hops.append(Hop(current, status, nxt))
                        if nxt in seen:
                            result.error, result.final_url = "redirect_loop", nxt
                            return result
                        if len(result.hops) > self.config.max_redirects:
                            result.error, result.final_url = "too_many_redirects", nxt
                            return result
                        seen.add(nxt)
                        current = nxt
                        continue

                    result.final_url = current
                    result.status_code = status
                    result.headers = {k.lower(): v for k, v in resp.headers.items()}
                    result.content_type = resp.headers.get("content-type")
                    result.ttfb_ms = ttfb
                    wants_body = read_body == "always" or (read_body == "html" and result.is_html)
                    if wants_body and 200 <= status < 300:
                        chunks: list[bytes] = []
                        size = 0
                        async for chunk in resp.aiter_bytes():
                            size += len(chunk)
                            if size > limit:
                                result.truncated = True
                                chunks.append(chunk[: len(chunk) - (size - limit)])
                                break
                            chunks.append(chunk)
                        result.body = b"".join(chunks)
                    return result
            except httpx.TimeoutException as exc:
                result.error, result.error_detail, result.final_url = "timeout", type(exc).__name__, current
                return result
            except httpx.HTTPError as exc:
                result.error, result.error_detail, result.final_url = "connect_error", str(exc)[:200], current
                return result
