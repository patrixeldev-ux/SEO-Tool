"""Steps that run after the crawl has been stored: JS rendering check, audit, scoring, snapshot."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import timedelta
from typing import Any

import httpx
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from searchos.audit.model import AuditInput, PageData, SiteData
from searchos.audit.runner import AuditResult, run_audit
from searchos.core.config import get_settings
from searchos.crawl.engine import CrawlConfig
from searchos.crawl.parser import count_words, extract_main_text
from searchos.crawl.render import PlaywrightRenderer, Renderer, sample_pages
from searchos.crawl.urls import InvalidUrlError, normalise_url
from searchos.integrations.errors import IntegrationError
from searchos.integrations.psi import Candidate, PsiMetrics, fetch_psi, select_psi_urls
from searchos.models import AuditSnapshot, Crawl, GscRow, Issue, Link, Page, PsiResult
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
    psi = await load_psi(session, crawl.id)
    data = [page_data(p, tuple(targets.get(p.id, ()))) for p in pages]
    for page in data:
        if page.normalised_url in psi:
            page.extra["psi"] = psi[page.normalised_url]
    return AuditInput(data, site_data(crawl))


async def load_psi(session: AsyncSession, crawl_id: uuid.UUID) -> dict[str, dict[str, Any]]:
    """PageSpeed results attached to this crawl, by the page's normalised URL (only rows that have data)."""
    rows = await session.scalars(
        sa.select(PsiResult).where(PsiResult.crawl_id == crawl_id, PsiResult.error.is_(None))
    )
    return {
        r.url: {
            "lcp_ms": r.lcp_ms,
            "lcp_source": r.lcp_source,
            "inp_ms": r.inp_ms,
            "inp_source": r.inp_source,
            "cls": r.cls,
            "cls_source": r.cls_source,
            "performance_score": r.performance_score,
        }
        for r in rows
    }


# ---- PageSpeed Insights -----------------------------------------------------------------------------
PSI_CONCURRENCY = 3
PSI_UNAVAILABLE = (
    "PageSpeed Insights data could not be fetched, so the Core Web Vitals checks were not run ({reason})."
)


def match_key(url: str) -> str:
    """Compare Search Console and crawler URLs without scheme, 'www.' or trailing-slash differences."""
    try:
        norm = normalise_url(url)
    except InvalidUrlError:
        norm = url
    rest = norm.split("://", 1)[-1].lower()
    if rest.startswith("www."):
        rest = rest[4:]
    return rest.rstrip("/") or rest


async def gsc_impressions_by_page(
    session: AsyncSession, site_id: uuid.UUID, limit: int = 500
) -> dict[str, int]:
    """Impressions per match_key over the newest 28 stored days (empty when Search Console has no data)."""
    latest = await session.scalar(sa.select(sa.func.max(GscRow.date)).where(GscRow.site_id == site_id))
    if latest is None:
        return {}
    rows = await session.execute(
        sa.select(GscRow.page, sa.func.sum(GscRow.impressions))
        .where(GscRow.site_id == site_id, GscRow.date > latest - timedelta(days=28))
        .group_by(GscRow.page)
        .order_by(sa.func.sum(GscRow.impressions).desc())
        .limit(limit)
    )
    totals: dict[str, int] = {}
    for page, impressions in rows:
        key = match_key(page)
        totals[key] = totals.get(key, 0) + int(impressions)
    return totals


async def run_psi_step(
    ctx: JobContext, crawl_id: uuid.UUID, *, client: httpx.AsyncClient | None = None
) -> list[str]:
    """Fetch PageSpeed data for the top pages and store it on the crawl. Returns warnings for the audit.

    Skipped for private/local test crawls, when PSI_ENABLED=false, or when the job payload says psi=false."""
    settings = get_settings()
    if not settings.psi_enabled or ctx.payload.get("psi") is False or ctx.payload.get("allow_private_hosts"):
        return []
    async with ctx.sessions() as session:
        crawl = await session.get(Crawl, crawl_id)
        if crawl is None:
            return []
        site_id = crawl.site_id
        audit_input = await load_audit_input(session, crawl)
        impressions = await gsc_impressions_by_page(session, site_id)
    homepage = audit_input.homepage
    candidates = [
        Candidate(p.normalised_url, p.inlinks_count, p.is_indexable, homepage is not None and p is homepage)
        for p in audit_input.pages
    ]
    by_key = {match_key(c.url): c.url for c in candidates}
    mapped = {by_key[k]: n for k, n in impressions.items() if k in by_key}
    urls = select_psi_urls(candidates, mapped)
    if not urls:
        return []

    api_key = settings.psi_api_key.get_secret_value() if settings.psi_api_key else None
    gate = asyncio.Semaphore(PSI_CONCURRENCY)
    results: dict[str, PsiMetrics | str] = {}
    own = client is None
    http = client or httpx.AsyncClient(timeout=120.0)

    async def one(url: str) -> None:
        async with gate:
            if await ctx.is_cancelled():
                return
            try:
                results[url] = await fetch_psi(http, url, api_key=api_key)
            except IntegrationError as exc:
                results[url] = str(exc)[:500]

    try:
        await asyncio.gather(*(one(u) for u in urls))
    finally:
        if own:
            await http.aclose()

    async with ctx.sessions() as session:
        await session.execute(sa.delete(PsiResult).where(PsiResult.crawl_id == crawl_id))
        for url, outcome in results.items():
            if isinstance(outcome, str):
                session.add(PsiResult(site_id=site_id, crawl_id=crawl_id, url=url, error=outcome))
            else:
                session.add(
                    PsiResult(
                        site_id=site_id,
                        crawl_id=crawl_id,
                        url=url,
                        lcp_ms=outcome.lcp_ms,
                        lcp_source=outcome.lcp_source,
                        inp_ms=outcome.inp_ms,
                        inp_source=outcome.inp_source,
                        cls=outcome.cls,
                        cls_source=outcome.cls_source,
                        performance_score=outcome.performance_score,
                    )
                )
        await session.commit()
    errors = [r for r in results.values() if isinstance(r, str)]
    if results and len(errors) == len(results):
        return [PSI_UNAVAILABLE.format(reason=errors[0])]
    if errors:
        return [
            f"PageSpeed Insights failed for {len(errors)} of {len(results)} pages; those pages were not checked."
        ]
    return []


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
            await ctx.set_progress(80 + round(8 * (index + 1) / len(sample)))
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
    warnings.extend(await run_psi_step(ctx, crawl_id))
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
