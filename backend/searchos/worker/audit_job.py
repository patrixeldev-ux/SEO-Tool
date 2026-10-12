"""Steps that run after the crawl has been stored: JS rendering check, audit, scoring, snapshot."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from searchos.audit.model import AuditInput, PageData, SiteData
from searchos.audit.runner import AuditResult, run_audit
from searchos.crawl.engine import CrawlConfig
from searchos.crawl.parser import count_words, extract_main_text
from searchos.crawl.render import PlaywrightRenderer, Renderer, sample_pages
from searchos.models import AuditSnapshot, Crawl, Issue, Link, Page
from searchos.worker.jobs import JobContext

log = logging.getLogger(__name__)

JS_UNAVAILABLE = "JS rendering check unavailable (no browser could be started on the server); it was skipped."
ISSUE_BATCH = 1000


def make_renderer(config: CrawlConfig) -> Renderer:
    """The browser used for the JS check. Tests replace this with a fake."""
    return PlaywrightRenderer(config.allowed_hosts)


# ---- loading ----------------------------------------------------------------------------------------
def page_data(row: Page, targets: tuple[str, ...]) -> PageData:
    return PageData(
        id=str(row.id),
        url=row.url,
        normalised_url=row.normalised_url,
        status_code=row.status_code,
        final_url=row.final_url,
        redirect_chain=list(row.redirect_chain or []),
        fetch_error=row.fetch_error,
        depth=row.depth,
        content_type=row.content_type,
        ttfb_ms=row.ttfb_ms,
        title=row.title,
        meta_description=row.meta_description,
        h1=list(row.h1 or []),
        canonical=row.canonical,
        robots_meta=row.robots_meta,
        x_robots_tag=row.x_robots_tag,
        lang=row.lang,
        has_viewport=row.has_viewport,
        hreflang=list(row.hreflang or []),
        word_count=row.word_count,
        main_text_method=row.main_text_method,
        structured_data=dict(row.structured_data or {}),
        in_sitemap=row.in_sitemap,
        inlinks_count=row.inlinks_count,
        extra=dict(row.extra or {}),
        internal_link_targets=targets,
    )


def site_data(crawl: Crawl) -> SiteData:
    info: dict[str, Any] = crawl.site_info or {}
    robots = info.get("robots") or {}
    return SiteData(
        start_url=info.get("start_url") or crawl.start_url,
        homepage_url=info.get("homepage_url") or crawl.start_url,
        robots_status=robots.get("status", "ok"),
        robots_text=robots.get("text"),
        sitemap_found=bool((info.get("sitemap") or {}).get("found")),
        llms_txt_present=info.get("llms_txt_present"),
        js_check_available=bool((info.get("js_check") or {}).get("available")),
    )


async def load_audit_input(session: AsyncSession, crawl: Crawl) -> AuditInput:
    targets: dict[uuid.UUID, list[str]] = {}
    link_rows = await session.execute(
        sa.select(Link.from_page_id, Link.to_url).where(Link.crawl_id == crawl.id, Link.is_internal.is_(True))
    )
    for from_id, to_url in link_rows:
        targets.setdefault(from_id, []).append(to_url)
    pages = (await session.scalars(sa.select(Page).where(Page.crawl_id == crawl.id).order_by(Page.url))).all()
    return AuditInput([page_data(p, tuple(targets.get(p.id, ()))) for p in pages], site_data(crawl))


# ---- JS rendering check -----------------------------------------------------------------------------
async def run_js_check(
    ctx: JobContext, crawl_id: uuid.UUID, config: CrawlConfig, renderer: Renderer
) -> tuple[bool, int]:
    """Render a sample of indexable pages and store their rendered word counts.

    Returns (browser available, pages rendered).
    """
    async with ctx.sessions() as session:
        crawl = await session.get(Crawl, crawl_id)
        if crawl is None:
            raise LookupError(f"crawl {crawl_id} not found")
        audit_input = await load_audit_input(session, crawl)
    candidates = [(p.normalised_url, p.depth) for p in audit_input.pages if p.is_indexable]
    if not candidates:
        return True, 0
    if not await renderer.start():
        return False, 0
    rendered = 0
    try:
        sample = sample_pages(candidates)
        for index, url in enumerate(sample):
            html = await renderer.render(url)
            if html is not None:
                text, _method = await asyncio.to_thread(extract_main_text, html, url)
                words = count_words(text)
                async with ctx.sessions() as session:
                    await session.execute(
                        sa.text(
                            "UPDATE pages SET extra = COALESCE(extra, '{}'::jsonb) || CAST(:patch AS jsonb) "
                            "WHERE crawl_id = :crawl_id AND normalised_url = :url"
                        ),
                        {
                            "patch": json.dumps({"rendered_word_count": words}),
                            "crawl_id": crawl_id,
                            "url": url,
                        },
                    )
                    await session.commit()
                rendered += 1
            await ctx.set_progress(80 + round(12 * (index + 1) / len(sample)))
    finally:
        await renderer.aclose()
    return True, rendered


# ---- audit ------------------------------------------------------------------------------------------
async def store_audit(
    sessions: async_sessionmaker[AsyncSession], crawl: Crawl, result: AuditResult
) -> AuditSnapshot:
    rows = [
        {
            "crawl_id": crawl.id,
            "check_id": i.check_id,
            "page_id": uuid.UUID(i.page_id) if i.page_id else None,
            "severity": i.severity,
            "category": i.category,
            "details": i.details,
        }
        for i in result.issues
    ]
    async with sessions() as session:
        await session.execute(sa.delete(Issue).where(Issue.crawl_id == crawl.id))
        await session.execute(sa.delete(AuditSnapshot).where(AuditSnapshot.crawl_id == crawl.id))
        for start in range(0, len(rows), ISSUE_BATCH):
            await session.execute(sa.insert(Issue), rows[start : start + ISSUE_BATCH])
        snapshot = AuditSnapshot(
            crawl_id=crawl.id,
            site_id=crawl.site_id,
            score=result.scores.score,
            subscores=dict(result.scores.subscores.items()),
            issue_counts=result.issue_counts,
        )
        session.add(snapshot)
        await session.commit()
        return snapshot


async def run_audit_for_crawl(sessions: async_sessionmaker[AsyncSession], crawl_id: uuid.UUID) -> AuditResult:
    """(Re-)run the audit on stored crawl data and replace its issues and snapshot."""
    async with sessions() as session:
        crawl = await session.get(Crawl, crawl_id)
        if crawl is None:
            raise RuntimeError(f"crawl {crawl_id} does not exist")
        audit_input = await load_audit_input(session, crawl)
    result = await asyncio.to_thread(run_audit, audit_input)
    await store_audit(sessions, crawl, result)
    return result


async def run_post_crawl_steps(
    ctx: JobContext, crawl_id: uuid.UUID, site_info: dict[str, Any], config: CrawlConfig
) -> list[str]:
    """JS check, then audit. Fills `site_info["js_check"]` and returns warnings to show with the audit."""
    warnings: list[str] = []
    js_enabled = bool(ctx.payload.get("js_check", True))
    if js_enabled:
        available, rendered = await run_js_check(ctx, crawl_id, config, make_renderer(config))
        site_info["js_check"] = {"available": available, "pages_rendered": rendered}
        if not available:
            warnings.append(JS_UNAVAILABLE)
    else:
        site_info["js_check"] = {"available": False, "pages_rendered": 0, "disabled": True}
    await ctx.set_progress(93)
    await run_audit_for_crawl_with_info(ctx.sessions, crawl_id, site_info)
    await ctx.set_progress(99)
    return warnings


async def run_audit_for_crawl_with_info(
    sessions: async_sessionmaker[AsyncSession], crawl_id: uuid.UUID, site_info: dict[str, Any]
) -> AuditResult:
    # The crawl row's site_info is only written at the very end of the job, so give the audit the live value.
    async with sessions() as session:
        crawl = await session.get(Crawl, crawl_id)
        if crawl is None:
            raise RuntimeError(f"crawl {crawl_id} does not exist")
        crawl.site_info = site_info
        await session.commit()
    return await run_audit_for_crawl(sessions, crawl_id)
