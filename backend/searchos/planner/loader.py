"""Reads what the planner needs from the database: the latest finished audit, the opportunities, Search Console."""

from __future__ import annotations

import uuid
from datetime import timedelta
from urllib.parse import urlsplit

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from searchos.audit.model import PageData
from searchos.crawl.urls import match_key
from searchos.models import Crawl, CrawlStatus, GscRow, Issue, Link, Opportunity, Page, SearchInsights, Site
from searchos.planner.mapping import MAPPINGS
from searchos.planner.model import Finding, GscPage, GscQuery, PlanContext, PlanPage
from searchos.stats.ctr_curve import POSITIONS, CtrCurve

TEXT_CHARS = 6000
GSC_WINDOW_DAYS = 28
GSC_QUERIES_PER_PAGE = 8
GSC_MAX_ROWS = 60_000


async def latest_done_crawl(session: AsyncSession, site_id: uuid.UUID) -> Crawl | None:
    return await session.scalar(
        sa.select(Crawl)
        .where(Crawl.site_id == site_id, Crawl.status == CrawlStatus.done)
        .order_by(Crawl.created_at.desc())
        .limit(1)
    )


def _plan_page(row: sa.Row[tuple[object, ...]]) -> PlanPage:
    data = PageData(
        id=str(row.id),
        url=row.url,
        normalised_url=row.normalised_url,
        status_code=row.status_code,
        content_type=row.content_type,
        fetch_error=row.fetch_error,
        robots_meta=row.robots_meta,
        x_robots_tag=row.x_robots_tag,
    )
    extra = dict(row.extra or {})
    structured = row.structured_data or {}
    return PlanPage(
        id=str(row.id),
        url=row.normalised_url,
        status_code=row.status_code,
        final_url=row.final_url,
        redirect_chain=tuple(row.redirect_chain or ()),
        title=row.title,
        meta_description=row.meta_description,
        h1=tuple(row.h1 or ()),
        canonical=row.canonical,
        word_count=row.word_count,
        depth=row.depth,
        inlinks=row.inlinks_count,
        in_sitemap=row.in_sitemap,
        is_indexable=data.is_indexable,
        is_success_html=data.is_success_html,
        text=row.text or "",
        images=tuple(extra.get("images") or ()),
        h2=tuple(extra.get("h2") or ()),
        jsonld=tuple(structured.get("jsonld") or ()),
        jsonld_errors=tuple(structured.get("jsonld_errors") or ()),
        extra=extra,
    )


async def load_pages(session: AsyncSession, crawl_id: uuid.UUID) -> dict[str, PlanPage]:
    rows = await session.execute(
        sa.select(
            Page.id,
            Page.normalised_url,
            Page.url,
            Page.status_code,
            Page.final_url,
            Page.redirect_chain,
            Page.fetch_error,
            Page.content_type,
            Page.title,
            Page.meta_description,
            Page.h1,
            Page.canonical,
            Page.robots_meta,
            Page.x_robots_tag,
            Page.word_count,
            Page.depth,
            Page.inlinks_count,
            Page.in_sitemap,
            Page.structured_data,
            Page.extra,
            sa.func.left(sa.func.coalesce(Page.main_text, ""), TEXT_CHARS).label("text"),
        )
        .where(Page.crawl_id == crawl_id)
        .order_by(Page.url)
    )
    return {p.url: p for p in (_plan_page(r) for r in rows)}


async def load_gsc(session: AsyncSession, site_id: uuid.UUID) -> dict[str, GscPage]:
    """Per page (keyed by `match_key`): impressions, clicks and the best queries of the newest 28 stored days."""
    latest = await session.scalar(sa.select(sa.func.max(GscRow.date)).where(GscRow.site_id == site_id))
    if latest is None:
        return {}
    impressions = sa.func.sum(GscRow.impressions)
    rows = await session.execute(
        sa.select(
            GscRow.page,
            GscRow.query,
            impressions,
            sa.func.sum(GscRow.clicks),
            sa.func.sum(GscRow.position * GscRow.impressions) / sa.func.nullif(impressions, 0),
        )
        .where(
            GscRow.site_id == site_id,
            GscRow.date > latest - timedelta(days=GSC_WINDOW_DAYS),
            GscRow.is_conversational.is_(False),
        )
        .group_by(GscRow.page, GscRow.query)
        .having(impressions > 0)
        .order_by(impressions.desc())
        .limit(GSC_MAX_ROWS)
    )
    pages: dict[str, GscPage] = {}
    for page, query, impr, clicks, position in rows:
        entry = pages.setdefault(match_key(page), GscPage())
        entry.impressions += int(impr)
        entry.clicks += int(clicks)
        if len(entry.queries) < GSC_QUERIES_PER_PAGE:  # rows arrive best-first
            entry.queries.append(GscQuery(query, int(impr), int(clicks), float(position or 0.0)))
    return pages


LINK_NEED_SOURCES = frozenset({"ORPHAN_PAGE", "DEEP_PAGE", "STRIKING_DISTANCE"})


async def load_linked_from(
    session: AsyncSession, crawl_id: uuid.UUID, targets: list[str]
) -> dict[str, frozenset[str]]:
    rows = await session.execute(
        sa.select(Page.normalised_url, Link.to_url)
        .join(Link, Link.from_page_id == Page.id)
        .where(Link.crawl_id == crawl_id, Link.is_internal.is_(True), Link.to_url.in_(targets))
    )
    found: dict[str, set[str]] = {}
    for source, target in rows:
        found.setdefault(target, set()).add(source)
    return {t: frozenset(s) for t, s in found.items()}


def curve_from_json(data: dict[str, object]) -> CtrCurve | None:
    values = data.get("values")
    confidence = data.get("confidence")
    impressions = data.get("impressions")
    if not (isinstance(values, list) and len(values) == POSITIONS):
        return None
    return CtrCurve(
        values=tuple(float(v) for v in values),
        confidence=tuple(confidence) if isinstance(confidence, list) else ("default",) * POSITIONS,  # type: ignore[arg-type]
        impressions=tuple(int(i) for i in impressions) if isinstance(impressions, list) else (0,) * POSITIONS,
    )


async def load_context(session: AsyncSession, site: Site) -> PlanContext:
    crawl = await latest_done_crawl(session, site.id)
    host = f"www.{site.domain}" if site.uses_www else site.domain
    pages: dict[str, PlanPage] = {}
    findings: list[Finding] = []
    homepage_url: str | None = None
    robots_text: str | None = None
    origin = f"https://{host}"

    if crawl is not None:
        pages = await load_pages(session, crawl.id)
        info = crawl.site_info or {}
        homepage_url = info.get("homepage_url") or crawl.start_url
        robots_text = (info.get("robots") or {}).get("text")
        if homepage_url:
            parts = urlsplit(homepage_url)
            origin = f"{parts.scheme}://{parts.netloc}"
        by_id = {p.id: p for p in pages.values()}
        issue_rows = await session.execute(
            sa.select(Issue.check_id, Issue.page_id, Issue.details).where(Issue.crawl_id == crawl.id)
        )
        for check_id, page_id, details in issue_rows:
            if check_id not in MAPPINGS:
                continue
            page = by_id.get(str(page_id)) if page_id else None
            findings.append(
                Finding(check_id, "audit", page.url if page else None, dict(details or {}), raw_url=None)
            )

    by_key = {match_key(url): url for url in pages}
    opportunities = (
        await session.scalars(sa.select(Opportunity).where(Opportunity.site_id == site.id))
    ).all()
    for opp in opportunities:
        if opp.type not in MAPPINGS:
            continue
        url = by_key.get(match_key(opp.page)) if opp.page else None
        findings.append(
            Finding(
                opp.type,
                "gsc",
                url,
                dict(opp.evidence or {}),
                query=opp.query,
                score=opp.score,
                raw_url=opp.page,
            )
        )

    linked_from: dict[str, frozenset[str]] = {}
    if crawl is not None:
        needing_links = sorted({f.url for f in findings if f.url and f.source in LINK_NEED_SOURCES})
        if needing_links:
            linked_from = await load_linked_from(session, crawl.id, needing_links)

    insights = await session.get(SearchInsights, site.id)
    curve = curve_from_json(insights.curve) if insights is not None else None
    gsc = await load_gsc(session, site.id)
    return PlanContext(
        site_id=str(site.id),
        org_id=str(site.org_id),
        domain=site.domain,
        site_name=site.display_name,
        origin=origin,
        homepage_url=homepage_url,
        pages=pages,
        findings=findings,
        gsc=gsc,
        curve=curve,
        has_audit=crawl is not None,
        has_gsc=insights is not None,
        crawl_id=str(crawl.id) if crawl is not None else None,
        robots_text=robots_text,
        linked_from=linked_from,
    )
