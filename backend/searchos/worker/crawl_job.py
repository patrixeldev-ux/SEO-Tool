"""The crawl job: crawl a site, store pages and links, compute click depth and inlinks, audit the result.

The handler is a plain coroutine over a `JobContext`, so tests and the dev helper run it directly with
`run_job`; Celery only wraps it (see `searchos.worker.tasks`).
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any, cast

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from searchos.core.config import get_settings
from searchos.crawl.engine import CrawlAborted, CrawlConfig, Crawler, CrawlOutcome, first_reachable
from searchos.crawl.records import PageRecord
from searchos.models import Crawl, CrawlStatus, Link, Page
from searchos.worker.jobs import JobContext

log = logging.getLogger(__name__)

PAGE_BATCH = 25
CRAWL_PROGRESS_SHARE = 80  # the crawl itself fills the first 80% of the job's progress bar


def clean(value: Any) -> Any:
    """Make crawled data storable: PostgreSQL rejects NUL characters and lone surrogates."""
    if isinstance(value, str):
        return value.replace("\x00", "").encode("utf-8", "ignore").decode("utf-8")
    if isinstance(value, list):
        return [clean(v) for v in value]
    if isinstance(value, dict):
        return {clean(k): clean(v) for k, v in value.items()}
    return value


def page_values(crawl_id: uuid.UUID, r: PageRecord) -> dict[str, Any]:
    values: dict[str, Any] = clean(
        {
            "id": r.id,
            "crawl_id": crawl_id,
            "url": r.url,
            "normalised_url": r.normalised_url,
            "status_code": r.status_code,
            "final_url": r.final_url,
            "redirect_chain": r.redirect_chain,
            "fetch_error": r.fetch_error,
            "content_type": (r.content_type or None) and r.content_type[:120],
            "ttfb_ms": r.ttfb_ms,
            "title": r.title,
            "meta_description": r.meta_description,
            "h1": r.h1,
            "canonical": r.canonical,
            "robots_meta": r.robots_meta,
            "x_robots_tag": r.x_robots_tag,
            "lang": (r.lang or None) and r.lang[:40],
            "has_viewport": r.has_viewport,
            "hreflang": r.hreflang,
            "word_count": r.word_count,
            "main_text_method": r.main_text_method,
            "main_text": r.main_text or None,
            "structured_data": r.structured_data,
            "in_sitemap": r.in_sitemap,
            "extra": r.extra,
        }
    )
    return values


class DbSink:
    """Writes crawled pages (and their links) to PostgreSQL in small batches."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession], crawl_id: uuid.UUID) -> None:
        self._sessions = sessions
        self._crawl_id = crawl_id
        self._buffer: list[PageRecord] = []

    async def on_page(self, record: PageRecord) -> None:
        self._buffer.append(record)
        if len(self._buffer) >= PAGE_BATCH:
            await self.flush()

    async def flush(self) -> None:
        if not self._buffer:
            return
        batch, self._buffer = self._buffer, []
        pages = [page_values(self._crawl_id, r) for r in batch]
        links = [
            clean(
                {
                    "crawl_id": self._crawl_id,
                    "from_page_id": r.id,
                    "to_url": link.to_url,
                    "anchor": link.anchor,
                    "rel": (link.rel or None) and link.rel[:120],
                    "is_internal": link.is_internal,
                }
            )
            for r in batch
            for link in r.links
        ]
        async with self._sessions() as session:
            await session.execute(sa.insert(Page), pages)
            if links:
                await session.execute(sa.insert(Link), links)
            await session.commit()


async def _finish_graph(
    sessions: async_sessionmaker[AsyncSession], crawl_id: uuid.UUID, outcome: CrawlOutcome
) -> None:
    """Resolve link targets to pages, then store click depth and inlink counts."""
    graph = outcome.graph()
    page_table = cast(sa.Table, Page.__table__)
    async with sessions() as session:
        await session.execute(
            sa.text(
                "UPDATE links l SET to_page_id = p.id FROM pages p "
                "WHERE l.crawl_id = :c AND p.crawl_id = :c AND p.normalised_url = l.to_url"
            ),
            {"c": crawl_id},
        )
        rows = (
            await session.execute(sa.select(Page.id, Page.normalised_url).where(Page.crawl_id == crawl_id))
        ).all()
        updates = [
            {
                "b_id": row.id,
                "b_depth": graph.depth.get(row.normalised_url),
                "b_inlinks": graph.inlinks.get(row.normalised_url, 0),
            }
            for row in rows
        ]
        if updates:
            await session.execute(
                sa.update(page_table)
                .where(page_table.c.id == sa.bindparam("b_id"))
                .values(depth=sa.bindparam("b_depth"), inlinks_count=sa.bindparam("b_inlinks")),
                updates,
            )
        await session.commit()


def build_site_info(outcome: CrawlOutcome) -> dict[str, Any]:
    homepage = outcome.homepage_url
    return {
        "start_url": outcome.start_url,
        "homepage_url": homepage,
        "homepage_scheme": homepage.split(":", 1)[0],
        "robots": {
            "status": outcome.robots.status,
            "http_status": outcome.robots.http_status,
            "text": outcome.robots.text,
        },
        "sitemap": {
            "found": outcome.sitemap.found,
            "declared": outcome.sitemap.declared,
            "files": outcome.sitemap.files,
            "url_count": len(outcome.sitemap.urls),
            "errors": outcome.sitemap.errors,
        },
        "llms_txt_present": outcome.llms_txt_present,
        "stop_reason": outcome.stop_reason,
        "urls_not_crawled": outcome.urls_not_crawled,
        "blocked_by_robots": outcome.blocked_by_robots,
    }


async def crawl_handler(ctx: JobContext) -> dict[str, Any]:
    """Job payload: crawl_id, allowed_hosts, optional start_url_candidates, rate_per_host, concurrency,
    allow_private_hosts. The API only ever creates payloads derived from a Site; tests and the dev helper
    may point it at the local fixture server."""
    payload = ctx.payload
    crawl_id = uuid.UUID(str(payload["crawl_id"]))
    sessions = ctx.sessions

    async with sessions() as session:
        crawl = await session.get(Crawl, crawl_id)
        if crawl is None:
            raise RuntimeError(f"crawl {crawl_id} does not exist")
        crawl.status = CrawlStatus.running
        crawl.started_at = datetime.now(UTC)
        start_url, page_limit, site_id = crawl.start_url, crawl.page_limit, crawl.site_id
        await session.commit()

    try:
        config = CrawlConfig(
            start_url=start_url,
            allowed_hosts=frozenset(payload["allowed_hosts"]),
            page_limit=page_limit,
            rate_per_host=float(payload.get("rate_per_host") or get_settings().crawl_rate_per_host),
            concurrency=int(payload.get("concurrency", 4)),
            allow_private_hosts=bool(payload.get("allow_private_hosts", False)),
        )
        candidates: list[str] = list(payload.get("start_url_candidates") or [])
        if candidates:
            reachable = await first_reachable(candidates, config)
            if reachable is None:
                raise CrawlAborted(
                    "We could not connect to the site (tried "
                    + ", ".join(candidates)
                    + "). Check the domain and that the site is online."
                )
            config.start_url = reachable
            async with sessions() as session:
                await session.execute(
                    sa.update(Crawl).where(Crawl.id == crawl_id).values(start_url=reachable)
                )
                await session.commit()

        sink = DbSink(sessions, crawl_id)

        async def on_progress(done: int, discovered: int) -> None:
            expected = max(1, min(discovered, config.page_limit))
            await ctx.set_progress(round(CRAWL_PROGRESS_SHARE * min(done, expected) / expected))
            async with sessions() as session:
                await session.execute(
                    sa.update(Crawl)
                    .where(Crawl.id == crawl_id)
                    .values(pages_crawled=done, urls_discovered=discovered)
                )
                await session.commit()

        outcome = await Crawler(config, sink, on_progress=on_progress, is_cancelled=ctx.is_cancelled).run()
        await _finish_graph(sessions, crawl_id, outcome)

        warnings = list(outcome.warnings)
        site_info = build_site_info(outcome)
        status = CrawlStatus.cancelled if outcome.stop_reason == "cancelled" else CrawlStatus.done

        if status is CrawlStatus.done:
            from searchos.worker.audit_job import run_post_crawl_steps

            warnings.extend(await run_post_crawl_steps(ctx, crawl_id, site_info, config))

        async with sessions() as session:
            await session.execute(
                sa.update(Crawl)
                .where(Crawl.id == crawl_id)
                .values(
                    status=status,
                    finished_at=datetime.now(UTC),
                    pages_crawled=outcome.pages_recorded,
                    urls_discovered=outcome.urls_discovered,
                    warnings=warnings,
                    site_info=clean(site_info),
                )
            )
            await session.commit()
        if status is CrawlStatus.done and ctx.payload.get("plan", True):
            from searchos.worker.plan_job import trigger_plan_for_site

            await trigger_plan_for_site(sessions, site_id, "audit")
        return {
            "crawl_id": str(crawl_id),
            "pages": outcome.pages_recorded,
            "stop_reason": outcome.stop_reason,
        }
    except Exception as exc:
        message = str(exc) if isinstance(exc, CrawlAborted) else f"{type(exc).__name__}: {exc}"
        log.exception("crawl %s failed", crawl_id)
        async with sessions() as session:
            await session.execute(
                sa.update(Crawl)
                .where(Crawl.id == crawl_id)
                .values(status=CrawlStatus.failed, finished_at=datetime.now(UTC), error=message[:2000])
            )
            await session.commit()
        raise
