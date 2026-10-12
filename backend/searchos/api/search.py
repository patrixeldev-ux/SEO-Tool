"""Search Console data endpoints (read side): coverage, daily totals, queries, pages, opportunities."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Any, Literal

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from searchos.api.schemas import JobOut
from searchos.core.deps import OrgContext, require_role
from searchos.integrations import gsc_sync
from searchos.integrations.gsc_dates import earliest_day, latest_day, subtract_months
from searchos.models import (
    GscDailyTotal,
    GscRow,
    IntegrationConnection,
    Opportunity,
    Provider,
    Role,
    SearchInsights,
    Site,
)
from searchos.search.explain import TITLES, explain
from searchos.stats.ctr_curve import default_curve

router = APIRouter(prefix="/sites/{site_id}/search", tags=["search"])
ReadCtx = Annotated[OrgContext, Depends(require_role(Role.client_viewer))]


class CoverageOut(BaseModel):
    connected: bool
    property_url: str | None
    first_day: date | None
    last_day: date | None
    days_stored: int
    days_expected: int
    active_job: JobOut | None
    last_job: JobOut | None
    last_sync_at: datetime | None
    last_error: str | None


@router.get("/coverage", response_model=CoverageOut)
async def coverage(site_id: uuid.UUID, ctx: ReadCtx) -> CoverageOut:
    """How much Search Console history is stored, and what the current sync is doing."""
    await ctx.get_or_404(Site, site_id, "Site")
    conn = await ctx.session.scalar(
        ctx.select(IntegrationConnection).where(
            IntegrationConnection.site_id == site_id, IntegrationConnection.provider == Provider.google
        )
    )
    first, last, count = (
        await ctx.session.execute(
            sa.select(
                sa.func.min(GscDailyTotal.date), sa.func.max(GscDailyTotal.date), sa.func.count()
            ).where(GscDailyTotal.site_id == site_id)
        )
    ).one()
    today = datetime.now(UTC).date()
    active = await gsc_sync.active_sync_job(ctx.session, site_id)
    latest = await gsc_sync.latest_sync_job(ctx.session, site_id)
    return CoverageOut(
        connected=bool(conn and conn.property_url),
        property_url=conn.property_url if conn else None,
        first_day=first,
        last_day=last,
        days_stored=count,
        days_expected=(latest_day(today) - earliest_day(today)).days + 1,
        active_job=JobOut.model_validate(active) if active else None,
        last_job=JobOut.model_validate(latest) if latest else None,
        last_sync_at=conn.last_sync_at if conn else None,
        last_error=conn.last_error if conn else None,
    )


# ---- read side: totals, tables, opportunities, page drill-down --------------------------------------
RangeName = Literal["28d", "3m", "16m"]
Segment = Literal["real", "all", "conversational"]
TABLE_SORTS = {"clicks": "clicks", "impressions": "impressions", "position": "position"}
OPPORTUNITY_TYPES = ("STRIKING_DISTANCE", "CTR_GAP", "DECAY", "CANNIBALISATION")


async def _window(ctx: OrgContext, site_id: uuid.UUID, range_name: RangeName) -> tuple[date, date] | None:
    """(start, end) of the requested range ending at the newest stored day, or None when nothing is stored."""
    await ctx.get_or_404(Site, site_id, "Site")
    end = await ctx.session.scalar(
        sa.select(sa.func.max(GscDailyTotal.date)).where(
            GscDailyTotal.site_id == site_id, GscDailyTotal.date <= latest_day(datetime.now(UTC).date())
        )
    )
    if end is None:
        return None
    if range_name == "28d":
        return end - timedelta(days=27), end
    months = 3 if range_name == "3m" else 16
    return subtract_months(end, months) + timedelta(days=1), end


class TotalsDay(BaseModel):
    date: date
    clicks: int
    impressions: int
    ctr: float
    position: float | None


class Summary(BaseModel):
    clicks: int
    impressions: int
    ctr: float
    position: float | None
    days: int


class TotalsOut(BaseModel):
    range: RangeName
    start: date | None
    end: date | None
    series: list[TotalsDay]
    summary: Summary | None
    previous: Summary | None
    note: str


TOTALS_NOTE = (
    "Totals include queries Google anonymises. The query and page tables below do not, "
    "so they add up to less."
)


async def _summary(session: Any, site_id: uuid.UUID, start: date, end: date) -> Summary | None:
    row = (
        await session.execute(
            sa.select(
                sa.func.count(),
                sa.func.coalesce(sa.func.sum(GscDailyTotal.clicks), 0),
                sa.func.coalesce(sa.func.sum(GscDailyTotal.impressions), 0),
                sa.func.sum(GscDailyTotal.position * GscDailyTotal.impressions),
            ).where(GscDailyTotal.site_id == site_id, GscDailyTotal.date.between(start, end))
        )
    ).one()
    days, clicks, impressions, weighted = row
    if days == 0:
        return None
    return Summary(
        clicks=int(clicks),
        impressions=int(impressions),
        ctr=clicks / impressions if impressions else 0.0,
        position=float(weighted) / impressions if impressions and weighted is not None else None,
        days=int(days),
    )


@router.get("/totals", response_model=TotalsOut)
async def totals(site_id: uuid.UUID, ctx: ReadCtx, range: Annotated[RangeName, Query()] = "28d") -> TotalsOut:
    """Daily clicks, impressions, CTR and position straight from Search Console's date-only totals."""
    window = await _window(ctx, site_id, range)
    if window is None:
        return TotalsOut(
            range=range, start=None, end=None, series=[], summary=None, previous=None, note=TOTALS_NOTE
        )
    start, end = window
    rows = await ctx.session.scalars(
        sa.select(GscDailyTotal)
        .where(GscDailyTotal.site_id == site_id, GscDailyTotal.date.between(start, end))
        .order_by(GscDailyTotal.date)
    )
    length = (end - start).days + 1
    previous = await _summary(ctx.session, site_id, start - timedelta(days=length), start - timedelta(days=1))
    return TotalsOut(
        range=range,
        start=start,
        end=end,
        series=[
            TotalsDay(date=r.date, clicks=r.clicks, impressions=r.impressions, ctr=r.ctr, position=r.position)
            for r in rows
        ],
        summary=await _summary(ctx.session, site_id, start, end),
        previous=previous if previous and previous.days == length else None,
        note=TOTALS_NOTE,
    )


class TableRow(BaseModel):
    key: str
    clicks: int
    impressions: int
    ctr: float
    position: float
    is_conversational: bool


class TableOut(BaseModel):
    range: RangeName
    segment: Segment
    start: date | None
    end: date | None
    total: int
    rows: list[TableRow]
    # Impressions of the heuristic "conversational" queries in this range, for the segment toggle.
    conversational_impressions: int
    all_impressions: int
    note: str


def _segment_filter(segment: Segment) -> list[Any]:
    if segment == "real":
        return [GscRow.is_conversational.is_(False)]
    if segment == "conversational":
        return [GscRow.is_conversational.is_(True)]
    return []


async def _table(
    ctx: OrgContext,
    site_id: uuid.UUID,
    group: Any,
    range_name: RangeName,
    segment: Segment,
    sort: str,
    limit: int,
    offset: int,
) -> TableOut:
    window = await _window(ctx, site_id, range_name)
    note = "Tables leave out queries Google anonymises, so they add up to less than the totals."
    if window is None:
        return TableOut(
            range=range_name, segment=segment, start=None, end=None, total=0, rows=[],
            conversational_impressions=0, all_impressions=0, note=note,
        )  # fmt: skip
    start, end = window
    base = [GscRow.site_id == site_id, GscRow.date.between(start, end)]
    impressions = sa.func.sum(GscRow.impressions)
    clicks = sa.func.sum(GscRow.clicks)
    position = sa.func.sum(GscRow.position * GscRow.impressions) / sa.func.nullif(impressions, 0)
    where = [*base, *_segment_filter(segment)]
    orders: dict[str, Any] = {
        "clicks": clicks.desc(),
        "impressions": impressions.desc(),
        "position": position.asc(),
    }
    order = orders[sort]
    grouped = (
        sa.select(
            group.label("key"),
            clicks.label("clicks"),
            impressions.label("impressions"),
            position.label("position"),
            sa.func.bool_or(GscRow.is_conversational).label("conv"),
        )
        .where(*where)
        .group_by(group)
        .having(impressions > 0)
    )
    count = await ctx.session.scalar(sa.select(sa.func.count()).select_from(grouped.subquery()))
    rows = await ctx.session.execute(grouped.order_by(order, group).limit(limit).offset(offset))
    conv, everything = (
        await ctx.session.execute(
            sa.select(
                sa.func.coalesce(
                    sa.func.sum(GscRow.impressions).filter(GscRow.is_conversational.is_(True)), 0
                ),
                sa.func.coalesce(impressions, 0),
            ).where(*base)
        )
    ).one()
    return TableOut(
        range=range_name,
        segment=segment,
        start=start,
        end=end,
        total=int(count or 0),
        rows=[
            TableRow(
                key=str(r.key),
                clicks=int(r.clicks),
                impressions=int(r.impressions),
                ctr=int(r.clicks) / int(r.impressions),
                position=float(r.position),
                is_conversational=bool(r.conv),
            )
            for r in rows
        ],
        conversational_impressions=int(conv),
        all_impressions=int(everything),
        note=note,
    )


@router.get("/queries", response_model=TableOut)
async def top_queries(
    site_id: uuid.UUID,
    ctx: ReadCtx,
    range: Annotated[RangeName, Query()] = "28d",
    segment: Annotated[Segment, Query()] = "real",
    sort: Annotated[Literal["clicks", "impressions", "position"], Query()] = "clicks",
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> TableOut:
    return await _table(ctx, site_id, GscRow.query, range, segment, sort, limit, offset)


@router.get("/pages", response_model=TableOut)
async def top_pages(
    site_id: uuid.UUID,
    ctx: ReadCtx,
    range: Annotated[RangeName, Query()] = "28d",
    segment: Annotated[Segment, Query()] = "real",
    sort: Annotated[Literal["clicks", "impressions", "position"], Query()] = "clicks",
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> TableOut:
    return await _table(ctx, site_id, GscRow.page, range, segment, sort, limit, offset)


class CurveOut(BaseModel):
    positions: list[int]
    values: list[float]
    confidence: list[str]
    impressions: list[int]
    summary: str
    computed_at: datetime | None
    window_start: date | None
    window_end: date | None


async def _curve(ctx: OrgContext, site_id: uuid.UUID) -> CurveOut:
    insights = await ctx.session.get(SearchInsights, site_id)
    if insights is None or not insights.curve.get("values"):
        curve = default_curve()
        return CurveOut(
            positions=list(range(1, len(curve.values) + 1)),
            values=[round(v, 6) for v in curve.values],
            confidence=list(curve.confidence),
            impressions=list(curve.impressions),
            summary="default",
            computed_at=None,
            window_start=None,
            window_end=None,
        )
    c = insights.curve
    return CurveOut(
        positions=list(range(1, len(c["values"]) + 1)),
        values=list(c["values"]),
        confidence=list(c["confidence"]),
        impressions=list(c["impressions"]),
        summary=str(c.get("summary", "default")),
        computed_at=insights.computed_at,
        window_start=insights.window_start,
        window_end=insights.window_end,
    )


@router.get("/curve", response_model=CurveOut)
async def ctr_curve(site_id: uuid.UUID, ctx: ReadCtx) -> CurveOut:
    """The CTR-by-position curve this site's opportunities were judged against."""
    await ctx.get_or_404(Site, site_id, "Site")
    return await _curve(ctx, site_id)


class OpportunityOut(BaseModel):
    id: uuid.UUID
    type: str
    title: str
    page: str | None
    query: str | None
    score: float
    explanation: str
    evidence: dict[str, Any]


class OpportunitiesOut(BaseModel):
    computed: bool
    computed_at: datetime | None
    window_start: date | None
    window_end: date | None
    counts: dict[str, int]
    skipped: dict[str, str]
    curve_summary: str | None
    total: int
    items: list[OpportunityOut]


@router.get("/opportunities", response_model=OpportunitiesOut)
async def opportunities(
    site_id: uuid.UUID,
    ctx: ReadCtx,
    type: Annotated[str | None, Query()] = None,
    page: Annotated[str | None, Query(max_length=2000)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> OpportunitiesOut:
    """Opportunities ranked by score, with a plain-language reason and the numbers behind it."""
    await ctx.get_or_404(Site, site_id, "Site")
    if type is not None and type not in OPPORTUNITY_TYPES:
        raise HTTPException(422, f"type must be one of {', '.join(OPPORTUNITY_TYPES)}")
    insights = await ctx.session.get(SearchInsights, site_id)
    where = [Opportunity.site_id == site_id]
    if type:
        where.append(Opportunity.type == type)
    if page:
        where.append(Opportunity.page == page)
    total = await ctx.session.scalar(sa.select(sa.func.count()).select_from(Opportunity).where(*where))
    rows = await ctx.session.scalars(
        sa.select(Opportunity)
        .where(*where)
        .order_by(Opportunity.score.desc(), Opportunity.id)
        .limit(limit)
        .offset(offset)
    )
    summary = insights.summary if insights else {}
    return OpportunitiesOut(
        computed=insights is not None,
        computed_at=insights.computed_at if insights else None,
        window_start=insights.window_start if insights else None,
        window_end=insights.window_end if insights else None,
        counts={str(k): int(v) for k, v in summary.get("counts", {}).items()},
        skipped={str(k): str(v) for k, v in summary.get("skipped", {}).items()},
        curve_summary=str(insights.curve.get("summary")) if insights else None,
        total=int(total or 0),
        items=[
            OpportunityOut(
                id=o.id,
                type=o.type,
                title=TITLES.get(o.type, o.type),
                page=o.page,
                query=o.query,
                score=o.score,
                explanation=explain(o.type, o.page, o.query, o.evidence),
                evidence=o.evidence,
            )
            for o in rows
        ],
    )


class PageQueryRow(BaseModel):
    query: str
    clicks: int
    impressions: int
    ctr: float
    position: float
    expected_ctr: float
    vs_curve: float  # actual CTR minus the curve's CTR at this position (negative = under-performing)
    is_conversational: bool


class PageDay(BaseModel):
    date: date
    clicks: int
    impressions: int
    position: float | None


class PageDetailOut(BaseModel):
    page: str
    range: RangeName
    segment: Segment
    start: date | None
    end: date | None
    summary: Summary | None
    expected_ctr: float | None
    curve_confidence: str | None
    series: list[PageDay]
    queries: list[PageQueryRow]
    opportunities: list[OpportunityOut]
    curve: CurveOut


@router.get("/page", response_model=PageDetailOut)
async def page_detail(
    site_id: uuid.UUID,
    ctx: ReadCtx,
    url: Annotated[str, Query(min_length=1, max_length=4000)],
    range: Annotated[RangeName, Query()] = "28d",
    segment: Annotated[Segment, Query()] = "real",
) -> PageDetailOut:
    """One page: its queries, positions, daily clicks and how its CTR compares with the site's curve."""
    window = await _window(ctx, site_id, range)
    curve = await _curve(ctx, site_id)
    if window is None:
        return PageDetailOut(
            page=url, range=range, segment=segment, start=None, end=None, summary=None, expected_ctr=None,
            curve_confidence=None, series=[], queries=[], opportunities=[], curve=curve,
        )  # fmt: skip
    start, end = window
    where = [
        GscRow.site_id == site_id,
        sa.func.md5(GscRow.page) == sa.func.md5(url),
        GscRow.page == url,
        GscRow.date.between(start, end),
        *_segment_filter(segment),
    ]
    impressions = sa.func.sum(GscRow.impressions)
    clicks = sa.func.sum(GscRow.clicks)
    weighted = sa.func.sum(GscRow.position * GscRow.impressions)
    q_rows = await ctx.session.execute(
        sa.select(
            GscRow.query,
            clicks,
            impressions,
            weighted / sa.func.nullif(impressions, 0),
            sa.func.bool_or(GscRow.is_conversational),
        )
        .where(*where)
        .group_by(GscRow.query)
        .having(impressions > 0)
        .order_by(impressions.desc(), GscRow.query)
        .limit(200)
    )

    def expected(position: float) -> float:
        index = min(max(int(position + 0.5), 1), len(curve.values)) - 1
        return float(curve.values[index])

    queries = [
        PageQueryRow(
            query=q,
            clicks=int(c),
            impressions=int(i),
            ctr=int(c) / int(i),
            position=float(p),
            expected_ctr=expected(float(p)),
            vs_curve=int(c) / int(i) - expected(float(p)),
            is_conversational=bool(conv),
        )
        for q, c, i, p, conv in q_rows
    ]
    day_rows = await ctx.session.execute(
        sa.select(GscRow.date, clicks, impressions, weighted / sa.func.nullif(impressions, 0))
        .where(*where)
        .group_by(GscRow.date)
        .order_by(GscRow.date)
    )
    series = [
        PageDay(date=d, clicks=int(c), impressions=int(i), position=float(p) if p is not None else None)
        for d, c, i, p in day_rows
    ]
    total_clicks = sum(q.clicks for q in queries)
    total_impr = sum(q.impressions for q in queries)
    summary = None
    expected_ctr = None
    confidence = None
    if total_impr:
        position = sum(q.position * q.impressions for q in queries) / total_impr
        summary = Summary(
            clicks=total_clicks,
            impressions=total_impr,
            ctr=total_clicks / total_impr,
            position=position,
            days=len(series),
        )
        expected_ctr = expected(position)
        confidence = curve.confidence[min(max(int(position + 0.5), 1), len(curve.values)) - 1]
    found = await opportunities(site_id, ctx, type=None, page=url, limit=50, offset=0)
    return PageDetailOut(
        page=url,
        range=range,
        segment=segment,
        start=start,
        end=end,
        summary=summary,
        expected_ctr=expected_ctr,
        curve_confidence=confidence,
        series=series,
        queries=queries,
        opportunities=found.items,
        curve=curve,
    )
