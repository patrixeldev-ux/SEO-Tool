"""Recompute a site's CTR curve and opportunities from the stored Search Console rows.

The pure maths lives in `searchos.stats`; this module only loads the aggregates, calls it, and stores the result.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from searchos.integrations.gsc_dates import latest_day
from searchos.models import GscDailyTotal, GscRow, Opportunity, SearchInsights, Site
from searchos.stats.brand import brand_terms, is_branded
from searchos.stats.ctr_curve import POSITIONS, Bucket, CtrCurve, PositionObs, fit_ctr_curve
from searchos.stats.opportunities import PageClicks, PageQueryStat, find_opportunities

WINDOW_DAYS = 28
CURVE_DAYS = 90
MAX_STORED_PER_TYPE = 500


def site_brand_terms(site: Site) -> frozenset[str]:
    extra = site.settings.get("brand_terms") if isinstance(site.settings, dict) else None
    return brand_terms(site.domain, [str(t) for t in extra] if isinstance(extra, list) else [])


async def latest_total_day(session: AsyncSession, site_id: uuid.UUID, today: date) -> date | None:
    return await session.scalar(
        sa.select(sa.func.max(GscDailyTotal.date)).where(
            GscDailyTotal.site_id == site_id, GscDailyTotal.date <= latest_day(today)
        )
    )


async def position_buckets(session: AsyncSession, site: Site, start: date, end: date) -> list[PositionObs]:
    """(rounded position, impressions, clicks) over [start, end], without conversational or branded queries."""
    bucket = sa.cast(sa.func.floor(GscRow.position + 0.5), sa.Integer)
    rows = await session.execute(
        sa.select(
            GscRow.query, bucket.label("bucket"), sa.func.sum(GscRow.impressions), sa.func.sum(GscRow.clicks)
        )
        .where(
            GscRow.site_id == site.id,
            GscRow.date.between(start, end),
            GscRow.is_conversational.is_(False),
            bucket.between(1, POSITIONS),
        )
        .group_by(GscRow.query, bucket)
    )
    terms = site_brand_terms(site)
    return [
        PositionObs(float(b), int(imp), int(clk))
        for query, b, imp, clk in rows
        if not is_branded(query, terms)
    ]


async def pooled_buckets(session: AsyncSession, site: Site, start: date, end: date) -> list[Bucket] | None:
    """Positions summed over the OTHER sites of the same organisation (never other organisations)."""
    others = (
        await session.scalars(
            sa.select(Site).where(
                Site.org_id == site.org_id,
                Site.id != site.id,
                sa.exists().where(GscDailyTotal.site_id == Site.id),
            )
        )
    ).all()
    if not others:
        return None
    impressions = [0] * POSITIONS
    clicks = [0] * POSITIONS
    for other in others:
        for obs in await position_buckets(session, other, start, end):
            index = int(obs.position) - 1
            impressions[index] += obs.impressions
            clicks[index] += obs.clicks
    return [Bucket(i, c) for i, c in zip(impressions, clicks, strict=True)]


async def load_page_query_stats(
    session: AsyncSession, site_id: uuid.UUID, start: date, end: date
) -> list[PageQueryStat]:
    rows = await session.execute(
        sa.select(
            GscRow.page,
            GscRow.query,
            sa.func.sum(GscRow.impressions),
            sa.func.sum(GscRow.clicks),
            sa.func.sum(GscRow.position * GscRow.impressions)
            / sa.func.nullif(sa.func.sum(GscRow.impressions), 0),
        )
        .where(
            GscRow.site_id == site_id, GscRow.date.between(start, end), GscRow.is_conversational.is_(False)
        )
        .group_by(GscRow.page, GscRow.query)
        .having(sa.func.sum(GscRow.impressions) > 0)
    )
    return [PageQueryStat(p, q, int(imp), int(clk), float(pos)) for p, q, imp, clk, pos in rows]


async def load_page_clicks(
    session: AsyncSession, site_id: uuid.UUID, recent: tuple[date, date], prior: tuple[date, date]
) -> list[PageClicks]:
    recent_sum = sa.func.coalesce(sa.func.sum(GscRow.clicks).filter(GscRow.date.between(*recent)), 0)
    prior_sum = sa.func.coalesce(sa.func.sum(GscRow.clicks).filter(GscRow.date.between(*prior)), 0)
    rows = await session.execute(
        sa.select(GscRow.page, recent_sum, prior_sum)
        .where(GscRow.site_id == site_id, GscRow.date.between(prior[0], recent[1]))
        .group_by(GscRow.page)
        .having(prior_sum > 0)
    )
    return [PageClicks(p, int(r), int(pr)) for p, r, pr in rows]


def curve_to_json(curve: CtrCurve) -> dict[str, Any]:
    return {
        "values": [round(v, 6) for v in curve.values],
        "confidence": list(curve.confidence),
        "impressions": list(curve.impressions),
        "summary": curve.summary,
    }


async def fit_site_curve(session: AsyncSession, site: Site, end: date) -> CtrCurve:
    start = end - timedelta(days=CURVE_DAYS - 1)
    own = await position_buckets(session, site, start, end)
    pooled = await pooled_buckets(session, site, start, end)
    return fit_ctr_curve(own, pooled=pooled)


async def recompute_insights(
    sessions: async_sessionmaker[AsyncSession], site_id: uuid.UUID, *, today: date | None = None
) -> dict[str, Any] | None:
    """Replace the site's opportunities and stored curve. None when the site has no stored Search Console days."""
    today = today or datetime.now(UTC).date()
    async with sessions() as session:
        site = await session.get(Site, site_id)
        if site is None:
            return None
        end = await latest_total_day(session, site_id, today)
        if end is None:
            return None
        start = end - timedelta(days=WINDOW_DAYS - 1)
        prior = (end - timedelta(days=2 * WINDOW_DAYS - 1), end - timedelta(days=WINDOW_DAYS))

        curve = await fit_site_curve(session, site, end)
        stats = await load_page_query_stats(session, site_id, start, end)

        covered = await session.scalar(
            sa.select(sa.func.count()).where(
                GscDailyTotal.site_id == site_id, GscDailyTotal.date.between(prior[0], end)
            )
        )
        skipped: dict[str, str] = {}
        page_clicks: list[PageClicks] | None = None
        if covered == 2 * WINDOW_DAYS:
            page_clicks = await load_page_clicks(session, site_id, (start, end), prior)
        else:
            skipped["DECAY"] = (
                f"Needs {2 * WINDOW_DAYS} days of history to compare two {WINDOW_DAYS}-day windows; "
                f"{covered} days are stored so far."
            )

        found = find_opportunities(stats, page_clicks, curve)

        conv = await session.execute(
            sa.select(
                sa.func.coalesce(
                    sa.func.sum(GscRow.impressions).filter(GscRow.is_conversational.is_(True)), 0
                ),
                sa.func.coalesce(sa.func.sum(GscRow.impressions), 0),
            ).where(GscRow.site_id == site_id, GscRow.date.between(start, end))
        )
        conv_impr, all_impr = conv.one()

        await session.execute(sa.delete(Opportunity).where(Opportunity.site_id == site_id))
        counts: dict[str, int] = {}
        for kind, items in found.items():
            counts[kind] = len(items)
            for item in items[:MAX_STORED_PER_TYPE]:
                session.add(
                    Opportunity(
                        site_id=site_id,
                        type=kind,
                        page=item.page,
                        query=item.query,
                        evidence=item.evidence,
                        score=item.score,
                        window_start=start,
                        window_end=end,
                    )
                )
        summary: dict[str, Any] = {
            "counts": counts,
            "stored_per_type_limit": MAX_STORED_PER_TYPE,
            "skipped": skipped,
            "page_query_pairs": len(stats),
            "conversational_impressions": int(conv_impr),
            "all_impressions": int(all_impr),
            "brand_terms": sorted(site_brand_terms(site)),
        }
        now = datetime.now(UTC)
        await session.execute(sa.delete(SearchInsights).where(SearchInsights.site_id == site_id))
        session.add(
            SearchInsights(
                site_id=site_id,
                computed_at=now,
                window_start=start,
                window_end=end,
                curve=curve_to_json(curve),
                summary=summary,
            )
        )
        await session.commit()
    return {"window_start": start.isoformat(), "window_end": end.isoformat(), **summary}
