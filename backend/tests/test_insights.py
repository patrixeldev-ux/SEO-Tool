"""Recomputing opportunities from stored rows: the database layer around the pure finders."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from searchos.core.db import get_sessionmaker
from searchos.integrations.gsc_dates import latest_day
from searchos.models import GscDailyTotal, GscRow, Opportunity, SearchInsights, Site
from searchos.search.insights import recompute_insights
from tests.gsc_helpers import make_connected_site

TODAY = date(2026, 10, 12)
END = latest_day(TODAY)  # 2026-10-10
RECENT = [END - timedelta(days=i) for i in range(28)]
PRIOR = [END - timedelta(days=28 + i) for i in range(28)]


def row(
    site_id: Any,
    day: date,
    page: str,
    query: str,
    impressions: int,
    clicks: int,
    position: float,
    conv: bool = False,
) -> dict[str, Any]:
    return {
        "site_id": site_id,
        "date": day,
        "page": page,
        "query": query,
        "country": "usa",
        "device": "DESKTOP",
        "clicks": clicks,
        "impressions": impressions,
        "ctr": clicks / impressions,
        "position": position,
        "is_conversational": conv,
    }


async def add_totals(session: AsyncSession, site_id: Any, days: list[date]) -> None:
    await session.execute(
        sa.insert(GscDailyTotal),
        [
            {"site_id": site_id, "date": d, "clicks": 10, "impressions": 100, "ctr": 0.1, "position": 5.0}
            for d in days
        ],
    )


def synthetic_rows(site_id: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for day in RECENT:
        rows.append(row(site_id, day, "/strike", "wireless widgets", 100, 2, 6.0))
        rows.append(row(site_id, day, "/gap", "gap query", 100, 1, 5.0))  # 1% CTR at position 5
        for i in range(10):  # healthy pages at position 5: 8% CTR, so the curve at 5 is ~6.5%
            rows.append(row(site_id, day, f"/healthy{i}", f"healthy query {i}", 100, 8, 5.0))
        for i in range(20):  # filler at position 2 to give P75 something to compare with
            rows.append(row(site_id, day, f"/filler{i}", f"filler query {i}", 10, 3, 2.0))
        rows.append(row(site_id, day, "/c1", "shared topic", 100, 4, 4.0))
        rows.append(row(site_id, day, "/c2", "shared topic", 50, 1, 7.0))
        rows.append(row(site_id, day, "/conv", "yes", 2000, 0, 5.0, conv=True))
        rows.append(row(site_id, day, "/", "example brand", 100, 60, 1.0))
    # /decay: 100 clicks in the previous 28 days, 40 in the last 28
    rows.append(row(site_id, RECENT[0], "/decay", "decay topic", 1000, 40, 3.0))
    rows.append(row(site_id, PRIOR[0], "/decay", "decay topic", 1000, 100, 3.0))
    return rows


async def seed(session: AsyncSession, site_id: Any, days: list[date]) -> None:
    rows = [r for r in synthetic_rows(site_id) if r["date"] in set(days)]
    for start in range(0, len(rows), 2000):
        await session.execute(sa.insert(GscRow), rows[start : start + 2000])
    await add_totals(session, site_id, days)
    await session.commit()


async def stored(session: AsyncSession, site_id: Any) -> dict[str, list[Opportunity]]:
    found = (
        await session.scalars(
            sa.select(Opportunity)
            .where(Opportunity.site_id == site_id)
            .order_by(Opportunity.score.desc())
            .execution_options(populate_existing=True)
        )
    ).all()
    out: dict[str, list[Opportunity]] = {}
    for o in found:
        out.setdefault(o.type, []).append(o)
    return out


async def test_recompute_finds_all_four_types_and_ignores_conversational_queries(
    session: AsyncSession,
) -> None:
    _, site, _ = await make_connected_site(session)
    site_id = site.id
    await seed(session, site_id, RECENT + PRIOR)
    summary = await recompute_insights(get_sessionmaker(), site_id, today=TODAY)
    assert summary is not None and summary["window_end"] == END.isoformat()
    assert summary["skipped"] == {}

    found = await stored(session, site_id)
    assert set(found) == {"STRIKING_DISTANCE", "CTR_GAP", "DECAY", "CANNIBALISATION"}
    assert "wireless widgets" in {o.query for o in found["STRIKING_DISTANCE"]}
    assert [o.query for o in found["CTR_GAP"]][:1] == ["gap query"]
    assert [o.page for o in found["DECAY"]] == ["/decay"]
    decay = found["DECAY"][0].evidence
    assert (decay["clicks_last_28d"], decay["clicks_previous_28d"]) == (40, 100) and decay["p_value"] < 0.01
    assert [o.query for o in found["CANNIBALISATION"]] == ["shared topic"]
    assert found["CANNIBALISATION"][0].evidence["query_impressions"] == 28 * 150

    everything = [o for group in found.values() for o in group]
    assert all(
        o.query != "yes" and o.page != "/conv" for o in everything
    )  # conversational: never an opportunity
    assert summary["conversational_impressions"] == 28 * 2000
    assert summary["counts"]["DECAY"] == 1


async def test_curve_excludes_branded_and_conversational_queries(session: AsyncSession) -> None:
    _, site, _ = await make_connected_site(session)
    site_id = site.id
    await seed(session, site_id, RECENT + PRIOR)
    await recompute_insights(get_sessionmaker(), site_id, today=TODAY)
    insights = await session.scalar(
        sa.select(SearchInsights)
        .where(SearchInsights.site_id == site_id)
        .execution_options(populate_existing=True)
    )
    assert insights is not None
    curve = insights.curve
    # Position 1 has only the branded query ("example brand" contains the domain label), so it is not
    # site data: it falls back to the default prior instead of the 60% branded CTR.
    assert curve["confidence"][0] != "site" and curve["values"][0] < 0.4
    # Position 5: 10 healthy pages at 8% + the 1% gap page = 7.4% raw; pooling with the lower-CTR position 4
    # (the cannibalisation pages at 4%) brings it slightly down. The 2000-impression conversational rows at
    # position 5 are excluded (see the impression count).
    assert curve["confidence"][4] == "site" and 0.065 < curve["values"][4] < 0.0745
    assert curve["impressions"][4] == 11 * 28 * 100
    assert all(a >= b - 1e-9 for a, b in zip(curve["values"], curve["values"][1:], strict=False))


async def test_decay_is_skipped_when_there_are_not_two_full_windows(session: AsyncSession) -> None:
    _, site, _ = await make_connected_site(session)
    site_id = site.id
    await seed(session, site_id, RECENT + PRIOR[:10])  # only 38 of the 56 days
    summary = await recompute_insights(get_sessionmaker(), site_id, today=TODAY)
    assert summary is not None and "DECAY" in summary["skipped"] and "38 days" in summary["skipped"]["DECAY"]
    assert "DECAY" not in await stored(session, site_id)


async def test_recompute_replaces_the_previous_set_and_returns_none_without_data(
    session: AsyncSession,
) -> None:
    _, site, _ = await make_connected_site(session)
    site_id = site.id
    assert await recompute_insights(get_sessionmaker(), site_id, today=TODAY) is None
    await seed(session, site_id, RECENT + PRIOR)
    await recompute_insights(get_sessionmaker(), site_id, today=TODAY)
    first = sum(len(v) for v in (await stored(session, site_id)).values())
    await recompute_insights(get_sessionmaker(), site_id, today=TODAY)
    second = sum(len(v) for v in (await stored(session, site_id)).values())
    assert first == second > 0


async def test_owner_brand_terms_are_excluded_from_the_curve(session: AsyncSession) -> None:
    _, site, _ = await make_connected_site(session)
    site_id = site.id
    await session.execute(
        sa.insert(GscRow),
        [row(site_id, d, "/", "widgetco store", 1000, 700, 3.0) for d in RECENT],
    )
    await add_totals(session, site_id, RECENT)
    await session.commit()
    await recompute_insights(get_sessionmaker(), site_id, today=TODAY)
    before = (await session.scalar(sa.select(SearchInsights.curve).where(SearchInsights.site_id == site_id)))[
        "values"
    ][2]
    assert before > 0.5  # nothing says "widgetco" is a brand yet: the 70% CTR becomes the position-3 value
    site_row = await session.get(Site, site_id)
    assert site_row is not None
    site_row.settings = {"brand_terms": ["WidgetCo"]}
    await session.commit()
    await recompute_insights(get_sessionmaker(), site_id, today=TODAY)
    after = (
        await session.scalar(
            sa.select(SearchInsights.curve)
            .where(SearchInsights.site_id == site_id)
            .execution_options(populate_existing=True)
        )
    )["values"][2]
    assert after == 0.110  # back to the default prior at position 3


async def test_pooled_curve_borrows_from_other_sites_of_the_same_org_only(session: AsyncSession) -> None:
    from searchos.models import Site as SiteModel

    org, site, _ = await make_connected_site(session, domain="main.example")
    site_id = site.id
    sibling = SiteModel(org_id=org.id, domain="sibling.example", display_name="sibling")
    session.add(sibling)
    await session.flush()
    sibling_id = sibling.id
    _, stranger, _ = await make_connected_site(session, domain="stranger.example")  # another organisation
    stranger_id = stranger.id
    # The sibling has lots of data at position 3 (CTR 10%); the stranger has lots at position 3 with 40%.
    for sid, clicks in ((sibling_id, 100), (stranger_id, 400)):
        await session.execute(
            sa.insert(GscRow), [row(sid, d, "/x", "generic topic", 1000, clicks, 3.0) for d in RECENT]
        )
        await add_totals(session, sid, RECENT)
    # The main site has data at position 1 only, so position 3 must be borrowed.
    await session.execute(
        sa.insert(GscRow), [row(site_id, d, "/y", "some topic", 1000, 250, 1.0) for d in RECENT]
    )
    await add_totals(session, site_id, RECENT)
    await session.commit()
    await recompute_insights(get_sessionmaker(), site_id, today=TODAY)
    curve = await session.scalar(sa.select(SearchInsights.curve).where(SearchInsights.site_id == site_id))
    assert curve["confidence"][0] == "site" and curve["confidence"][2] == "pooled"
    assert abs(curve["values"][2] - 0.1) < 1e-9  # the sibling's 10%, not the stranger's 40%
    assert curve["summary"] == "pooled"
