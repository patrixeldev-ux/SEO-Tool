"""Breadth-first crawler.

Takes an explicit start URL and the hosts it may touch; knows nothing about Sites or the database. Pages
are handed to a `CrawlSink` as they are fetched, so a long crawl is persisted (and visible) incrementally.

Order: lowest discovery depth first. URLs that only appear in a sitemap are fetched after every
link-reachable page (they only use the page budget that is left over).
"""

from __future__ import annotations

import asyncio
import heapq
import itertools
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import httpx

from searchos.crawl import USER_AGENT
from searchos.crawl.fetcher import Fetcher, FetcherConfig, FetchResult
from searchos.crawl.graph import GraphResult, compute_graph, resolve_redirect
from searchos.crawl.parser import decode_html, parse_page
from searchos.crawl.records import CrawlSink, LinkRecord, PageRecord
from searchos.crawl.robots import RobotsRules
from searchos.crawl.sitemap import SitemapResult, discover_sitemaps
from searchos.crawl.urls import normalise_netloc, normalise_url

log = logging.getLogger(__name__)

HARD_PAGE_CAP = 10_000
SITEMAP_ONLY_PRIORITY = 1000
MAX_STORED_IMAGES = 200
MAX_STORED_TEXT_CHARS = 30_000


class CrawlAborted(Exception):
    """The crawl cannot proceed (e.g. robots.txt is unreachable). The message is shown to the owner."""


@dataclass(slots=True)
class CrawlConfig:
    start_url: str
    allowed_hosts: frozenset[str]
    page_limit: int = 500
    rate_per_host: float = 2.0
    concurrency: int = 4
    timeout: float = 15.0
    max_redirects: int = 5
    user_agent: str = USER_AGENT
    allow_private_hosts: bool = False
    respect_robots: bool = True
    cancel_check_interval: float = 0.5  # seconds between cancellation checks

    def __post_init__(self) -> None:
        self.page_limit = max(1, min(int(self.page_limit), HARD_PAGE_CAP))
        self.allowed_hosts = frozenset(normalise_netloc(h) for h in self.allowed_hosts)
        self.start_url = normalise_url(self.start_url)

    def fetcher_config(self) -> FetcherConfig:
        return FetcherConfig(
            allowed_hosts=self.allowed_hosts,
            user_agent=self.user_agent,
            timeout=self.timeout,
            max_redirects=self.max_redirects,
            rate_per_host=self.rate_per_host,
            respect_robots=self.respect_robots,
            allow_private_hosts=self.allow_private_hosts,
        )


@dataclass(slots=True)
class CrawlOutcome:
    start_url: str
    robots: RobotsRules
    sitemap: SitemapResult
    pages_recorded: int = 0
    urls_discovered: int = 0
    stop_reason: str = "complete"  # complete | page_limit | cancelled
    urls_not_crawled: int = 0
    blocked_by_robots: int = 0
    edges: dict[str, set[str]] = field(default_factory=dict)
    redirects: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    llms_txt_present: bool | None = None  # None = could not be determined
    _page_urls: set[str] = field(default_factory=set)

    @property
    def homepage_url(self) -> str:
        """Where the start URL ends up after redirects (e.g. http -> https, bare domain -> www)."""
        return resolve_redirect(self.start_url, self.redirects)

    def graph(self) -> GraphResult:
        return compute_graph(self.start_url, self.edges, self.redirects, self._page_urls)


ProgressCallback = Callable[[int, int], Awaitable[None]]
CancelCheck = Callable[[], Awaitable[bool]]


async def first_reachable(
    candidates: list[str], config: CrawlConfig, *, transport: httpx.AsyncBaseTransport | None = None
) -> str | None:
    """The first candidate start URL that answers over HTTP at all (any status), e.g. https before http."""
    probe_cfg = FetcherConfig(
        allowed_hosts=config.allowed_hosts,
        user_agent=config.user_agent,
        timeout=min(config.timeout, 10.0),
        rate_per_host=config.rate_per_host,
        respect_robots=False,
        allow_private_hosts=config.allow_private_hosts,
    )
    async with Fetcher(probe_cfg, transport=transport) as fetcher:
        for url in candidates:
            result = await fetcher.fetch(url, read_body="never")
            if result.status_code is not None or result.hops:
                return normalise_url(url)
    return None


class Crawler:
    def __init__(
        self,
        config: CrawlConfig,
        sink: CrawlSink,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        on_progress: ProgressCallback | None = None,
        is_cancelled: CancelCheck | None = None,
    ) -> None:
        self.config = config
        self.sink = sink
        self._transport = transport
        self._on_progress = on_progress
        self._is_cancelled = is_cancelled

    async def run(self) -> CrawlOutcome:
        cfg = self.config
        async with Fetcher(cfg.fetcher_config(), transport=self._transport) as fetcher:
            start = cfg.start_url
            robots = await fetcher.robots_for(start)
            if robots.status == "error":
                raise CrawlAborted(
                    "The site's robots.txt could not be read "
                    f"({'HTTP ' + str(robots.http_status) if robots.http_status else 'no response'}). "
                    "We do not crawl when robots.txt is unavailable; please try again later."
                )
            sitemap = await discover_sitemaps(fetcher, start, robots)
            outcome = CrawlOutcome(start_url=start, robots=robots, sitemap=sitemap)
            if sitemap.truncated:
                outcome.warnings.append("The sitemap is very large; only the first part of it was read.")
            await self._crawl(fetcher, outcome, set(sitemap.urls))
            outcome.llms_txt_present = await self._llms_txt(fetcher, outcome.homepage_url)
        return outcome

    @staticmethod
    async def _llms_txt(fetcher: Fetcher, homepage: str) -> bool | None:
        parts = urlsplit(homepage)
        result = await fetcher.fetch(
            f"{parts.scheme}://{parts.netloc}/llms.txt",
            read_body="always",
            max_bytes=200_000,
            respect_robots=False,
        )
        if result.error or result.status_code is None:
            return None
        if result.status_code != 200 or result.is_html:  # a "soft 404" that serves HTML does not count
            return False
        return bool((result.body or b"").strip())

    # ------------------------------------------------------------------------------------------
    async def _crawl(self, fetcher: Fetcher, outcome: CrawlOutcome, in_sitemap: set[str]) -> None:
        cfg = self.config
        counter = itertools.count()
        heap: list[tuple[int, int, str]] = []
        best: dict[str, int] = {}
        started: set[str] = set()

        def push(url: str, priority: int) -> None:
            if url in started or best.get(url, 1 << 30) <= priority:
                return
            best[url] = priority
            heapq.heappush(heap, (priority, next(counter), url))

        push(cfg.start_url, 0)
        for url in sorted(in_sitemap):
            push(url, SITEMAP_ONLY_PRIORITY)

        fetch_budget = 0  # fetches started (blocked-by-robots stubs are free)
        active: dict[asyncio.Task[list[PageRecord]], str] = {}
        last_cancel_check = 0.0
        completed = 0

        async def cancelled() -> bool:
            nonlocal last_cancel_check
            if self._is_cancelled is None:
                return False
            now = time.monotonic()
            if now - last_cancel_check < cfg.cancel_check_interval:
                return False
            last_cancel_check = now
            return await self._is_cancelled()

        while True:
            stop = False
            if await cancelled():
                outcome.stop_reason = "cancelled"
                stop = True
            while not stop and len(active) < cfg.concurrency and heap and fetch_budget < cfg.page_limit:
                if heap[0][0] >= SITEMAP_ONLY_PRIORITY and active:
                    break  # in-flight pages may still reveal link-reachable URLs; those go first
                priority, _, url = heapq.heappop(heap)
                if url in started or best.get(url) != priority:
                    continue
                started.add(url)
                if fetcher.config.respect_robots and not (await fetcher.robots_for(url)).can_fetch(
                    url, fetcher.config.robots_token
                ):
                    outcome.blocked_by_robots += 1
                    if url in in_sitemap:  # BLOCKED_IN_SITEMAP needs to know about these
                        record = PageRecord(url=url, fetch_error="blocked_by_robots", in_sitemap=True)
                        record.final_url = url
                        await self.sink.on_page(record)
                        outcome.pages_recorded += 1
                        outcome._page_urls.add(url)
                    continue
                fetch_budget += 1
                depth = priority if priority < SITEMAP_ONLY_PRIORITY else 1
                task = asyncio.create_task(self._process(fetcher, url, depth, in_sitemap, started))
                active[task] = url
            if not active:
                break
            done, _ = await asyncio.wait(active, return_when=asyncio.FIRST_COMPLETED, timeout=1.0)
            for task in done:
                url = active.pop(task)
                try:
                    records = task.result()
                except Exception:
                    log.exception("unexpected error while crawling %s", url)
                    records = [PageRecord(url=url, fetch_error="internal_error", final_url=url)]
                for record in records:
                    await self._accept(record, outcome, push)
                completed += 1
                outcome.urls_discovered = len(best)
                if self._on_progress:
                    await self._on_progress(completed, len(best))
            if stop and not active:
                break

        remaining = {url for _, _, url in heap if url not in started}
        outcome.urls_not_crawled = len(remaining)
        if outcome.stop_reason != "cancelled" and remaining and fetch_budget >= cfg.page_limit:
            outcome.stop_reason = "page_limit"
            outcome.warnings.append(
                f"Stopped at the page limit ({cfg.page_limit}); "
                f"{len(remaining)} more URLs were found but not crawled."
            )
        await self.sink.flush()

    async def _accept(
        self, record: PageRecord, outcome: CrawlOutcome, push: Callable[[str, int], None]
    ) -> None:
        cfg = self.config
        await self.sink.on_page(record)
        outcome.pages_recorded += 1
        outcome._page_urls.add(record.url)
        if record.redirect_chain and record.final_url and record.final_url != record.url:
            outcome.redirects[record.url] = record.final_url
        internal_targets = {link.to_url for link in record.links if link.is_internal}
        if internal_targets:
            outcome.edges[record.url] = internal_targets
        next_priority = record.discovery_depth + 1
        for target in internal_targets:
            push(target, next_priority)
        canonical = record.canonical
        if canonical and _is_internal(canonical, cfg.allowed_hosts):
            push(normalise_url(canonical), next_priority)

    # ------------------------------------------------------------------------------------------
    async def _process(
        self, fetcher: Fetcher, url: str, depth: int, in_sitemap: set[str], started: set[str]
    ) -> list[PageRecord]:
        result = await fetcher.fetch(url)
        records: list[PageRecord] = []
        if result.hops:
            source = PageRecord(
                url=url,
                status_code=result.hops[0].status,
                final_url=result.final_url,
                redirect_chain=[
                    {"url": h.url, "status": h.status, "location": h.location} for h in result.hops
                ],
                fetch_error=result.error,
                discovery_depth=depth,
                in_sitemap=url in in_sitemap,
            )
            records.append(source)
            final = result.final_url
            if result.error is None and result.status_code is not None and final not in started:
                started.add(final)
                records.append(await self._page_from_response(final, result, depth, in_sitemap))
            return records
        records.append(await self._page_from_response(url, result, depth, in_sitemap))
        return records

    async def _page_from_response(
        self, url: str, result: FetchResult, depth: int, in_sitemap: set[str]
    ) -> PageRecord:
        record = PageRecord(
            url=url,
            status_code=result.status_code,
            final_url=url,
            fetch_error=result.error,
            discovery_depth=depth,
            content_type=(result.content_type or "").split(";")[0].strip().lower() or None,
            ttfb_ms=result.ttfb_ms,
            x_robots_tag=result.headers.get("x-robots-tag"),
            in_sitemap=url in in_sitemap,
        )
        if result.status_code is None or not (200 <= result.status_code < 300):
            return record
        if not result.is_html or result.body is None:
            return record

        allowed = self.config.allowed_hosts

        def internal(target: str) -> bool:
            return _is_internal(target, allowed)

        html = decode_html(result.body, result.content_type)
        parsed = await asyncio.to_thread(parse_page, html, url, internal)
        record.title = parsed.title
        record.meta_description = parsed.meta_description
        record.h1 = parsed.h1
        record.canonical = parsed.canonical
        record.robots_meta = parsed.robots_meta
        record.lang = parsed.lang
        record.has_viewport = parsed.has_viewport
        record.hreflang = parsed.hreflang
        record.word_count = parsed.word_count
        record.main_text_method = parsed.main_text_method
        record.main_text = parsed.main_text[:MAX_STORED_TEXT_CHARS]
        record.structured_data = {
            "jsonld": parsed.jsonld,
            "jsonld_errors": parsed.jsonld_errors,
            "microdata": parsed.microdata,
        }
        record.links = [
            LinkRecord(link.url, link.anchor, link.rel, link.is_internal) for link in parsed.links
        ]
        extra: dict[str, Any] = {
            "h2": parsed.h2[:50],
            "h3": parsed.h3[:50],
            "images": [{"src": i.src, "alt": i.alt} for i in parsed.images[:MAX_STORED_IMAGES]],
            "image_count": len(parsed.images),
            "http_resources": parsed.http_resources,
            "question_headings": [
                {"text": q.text, "tag": q.tag, "answered": q.answered, "answer_words": q.answer_words}
                for q in parsed.question_headings
            ],
            "og_type": parsed.og_type,
            "is_article": parsed.is_article,
            "has_author": parsed.has_author,
            "has_date": parsed.has_date,
        }
        if result.truncated:
            extra["html_truncated"] = True
        record.extra = extra
        return record


def _is_internal(url: str, allowed_hosts: frozenset[str]) -> bool:
    try:
        return urlsplit(normalise_url(url)).netloc in allowed_hosts
    except ValueError:
        return False
