"""The read side of the Search Console data: totals, tables, opportunities, page drill-down, demo seed."""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from searchos import dev_seed_gsc
from searchos.models import GscDailyTotal, GscRow, Site
from searchos.search.explain import explain
from tests.conftest import auth, register

EMAIL = "o@example.com"


@pytest.fixture
async def seeded(client: AsyncClient, session: AsyncSession) -> tuple[dict[str, Any], str]:
    tokens = await register(client, EMAIL)
    message = await dev_seed_gsc.seed(EMAIL)
    assert message.startswith("Seeded"), message
    site_id = str(await session.scalar(sa.select(Site.id).where(Site.domain == dev_seed_gsc.DEMO_DOMAIN)))
    return tokens, site_id


def test_demo_data_generator_is_deterministic_and_totals_exceed_rows() -> None:
    today = date(2026, 10, 12)
    rows, totals = dev_seed_gsc.generate(today)
    again_rows, _ = dev_seed_gsc.generate(today)
    assert rows == again_rows
    assert len(totals) > 480 and totals[-1]["date"] == date(2026, 10, 10)
    by_day = {t["date"]: t for t in totals}
    day = totals[-1]["date"]
    row_clicks = sum(r["clicks"] for r in rows if r["date"] == day)
    assert by_day[day]["clicks"] > row_clicks  # anonymised queries are in the totals only
    assert any(r["is_conversational"] for r in rows) and not all(r["is_conversational"] for r in rows)


async def test_seed_refuses_unknown_user_and_reseeding_replaces(
    client: AsyncClient, session: AsyncSession
) -> None:
    assert (await dev_seed_gsc.seed("nobody@example.com")).startswith("No user")
    await register(client, EMAIL)
    await dev_seed_gsc.seed(EMAIL)
    first = await session.scalar(sa.select(sa.func.count()).select_from(GscRow))
    await dev_seed_gsc.seed(EMAIL)
    assert await session.scalar(sa.select(sa.func.count()).select_from(GscRow)) == first


async def test_totals_come_from_daily_totals_in_three_ranges(
    client: AsyncClient, seeded: tuple[dict[str, Any], str], session: AsyncSession
) -> None:
    tokens, site_id = seeded
    url = f"/sites/{site_id}/search/totals"
    r28 = (await client.get(url, headers=auth(tokens))).json()
    assert r28["range"] == "28d" and len(r28["series"]) == 28
    assert r28["summary"]["clicks"] == sum(d["clicks"] for d in r28["series"])
    assert r28["summary"]["ctr"] == pytest.approx(r28["summary"]["clicks"] / r28["summary"]["impressions"])
    assert r28["previous"] is not None and r28["previous"]["days"] == 28
    assert "anonymises" in r28["note"]
    r3 = (await client.get(url, params={"range": "3m"}, headers=auth(tokens))).json()
    r16 = (await client.get(url, params={"range": "16m"}, headers=auth(tokens))).json()
    assert 88 <= len(r3["series"]) <= 93 and len(r16["series"]) > 470
    stored = await session.scalar(
        sa.select(sa.func.sum(GscDailyTotal.clicks)).where(GscDailyTotal.site_id == site_id)
    )
    assert stored is not None and stored >= r16["summary"]["clicks"]
    # The rows do not add up to the totals (anonymised queries): the API never sums rows for the chart.
    queries = (
        await client.get(f"/sites/{site_id}/search/queries", params={"limit": 100}, headers=auth(tokens))
    ).json()
    assert sum(q["clicks"] for q in queries["rows"]) < r28["summary"]["clicks"]
    bad = await client.get(url, params={"range": "9y"}, headers=auth(tokens))
    assert bad.status_code == 422


async def test_query_and_page_tables_and_the_conversational_segment(
    client: AsyncClient, seeded: tuple[dict[str, Any], str]
) -> None:
    tokens, site_id = seeded
    base = f"/sites/{site_id}/search"
    real = (await client.get(f"{base}/queries", params={"limit": 100}, headers=auth(tokens))).json()
    keys = {r["key"] for r in real["rows"]}
    assert "espresso beans" in keys and "yes" not in keys and real["segment"] == "real"
    assert (
        real["conversational_impressions"] > 0
        and real["all_impressions"] > real["conversational_impressions"]
    )
    assert [r["clicks"] for r in real["rows"]] == sorted((r["clicks"] for r in real["rows"]), reverse=True)
    only = (
        await client.get(f"{base}/queries", params={"segment": "conversational"}, headers=auth(tokens))
    ).json()
    assert {r["key"] for r in only["rows"]} == {
        "yes",
        "tell me more",
        "what about the second one",
        "how about cheaper",
    }
    assert all(r["is_conversational"] for r in only["rows"])
    everything = (
        await client.get(f"{base}/queries", params={"segment": "all", "limit": 100}, headers=auth(tokens))
    ).json()
    assert (
        "yes" in {r["key"] for r in everything["rows"]}
        and everything["total"] == real["total"] + only["total"]
    )
    paged = (
        await client.get(f"{base}/queries", params={"limit": 3, "offset": 3}, headers=auth(tokens))
    ).json()
    assert len(paged["rows"]) == 3 and paged["total"] == real["total"]
    by_position = (
        await client.get(f"{base}/queries", params={"sort": "position", "limit": 5}, headers=auth(tokens))
    ).json()
    positions = [r["position"] for r in by_position["rows"]]
    assert positions == sorted(positions)
    pages = (await client.get(f"{base}/pages", headers=auth(tokens))).json()
    assert pages["rows"][0]["key"].startswith("https://harborandpine.example/")
    assert (
        await client.get(f"{base}/queries", params={"limit": 500}, headers=auth(tokens))
    ).status_code == 422


async def test_opportunities_cover_all_four_types_with_plain_language_reasons(
    client: AsyncClient, seeded: tuple[dict[str, Any], str]
) -> None:
    tokens, site_id = seeded
    base = f"/sites/{site_id}/search/opportunities"
    body = (await client.get(base, headers=auth(tokens))).json()
    assert body["computed"] is True and body["window_end"] is not None
    assert set(body["counts"]) == {"STRIKING_DISTANCE", "CTR_GAP", "DECAY", "CANNIBALISATION"}
    assert all(body["counts"][k] >= 1 for k in body["counts"]), body["counts"]
    assert body["skipped"] == {}
    scores = [i["score"] for i in body["items"]]
    assert scores == sorted(scores, reverse=True)
    assert {i["type"] for i in body["items"]} == set(body["counts"])
    assert all(i["explanation"] and i["title"] for i in body["items"])
    by_type = {
        t: (await client.get(base, params={"type": t}, headers=auth(tokens))).json() for t in body["counts"]
    }
    assert all(i["type"] == t for t, b in by_type.items() for i in b["items"])
    grinder = next(i for i in by_type["STRIKING_DISTANCE"]["items"] if i["query"] == "best coffee grinder")
    assert "position" in grinder["explanation"] and grinder["evidence"]["impressions"] > 0
    assert any("espresso-basics" in (i["page"] or "") for i in by_type["DECAY"]["items"])
    assert any(i["query"] == "espresso at home" for i in by_type["CANNIBALISATION"]["items"])
    gap_pages = {i["page"] for i in by_type["CTR_GAP"]["items"]}
    assert "https://harborandpine.example/blog/french-press" in gap_pages
    # conversational queries never become opportunities
    assert all(i["query"] not in ("yes", "tell me more") for i in body["items"])
    assert (await client.get(base, params={"type": "NOPE"}, headers=auth(tokens))).status_code == 422


async def test_page_drill_down_shows_queries_position_and_ctr_against_the_curve(
    client: AsyncClient, seeded: tuple[dict[str, Any], str]
) -> None:
    tokens, site_id = seeded
    page = "https://harborandpine.example/blog/french-press"
    detail = (
        await client.get(f"/sites/{site_id}/search/page", params={"url": page}, headers=auth(tokens))
    ).json()
    assert detail["page"] == page and detail["summary"]["impressions"] > 0
    query = detail["queries"][0]
    assert query["query"] == "french press ratio" and 2.5 <= query["position"] <= 3.5
    assert query["vs_curve"] < -0.03 and query["expected_ctr"] > query["ctr"]  # far below the curve
    assert detail["expected_ctr"] == pytest.approx(query["expected_ctr"])
    assert len(detail["series"]) == 28 and len(detail["curve"]["values"]) == 20
    assert any(o["type"] == "CTR_GAP" for o in detail["opportunities"])
    healthy = (
        await client.get(
            f"/sites/{site_id}/search/page",
            params={"url": "https://harborandpine.example/shop/espresso-beans"},
            headers=auth(tokens),
        )
    ).json()
    assert healthy["queries"][0]["vs_curve"] > -0.03
    missing = (
        await client.get(
            f"/sites/{site_id}/search/page", params={"url": "https://nope.example/"}, headers=auth(tokens)
        )
    ).json()
    assert missing["summary"] is None and missing["queries"] == []


async def test_curve_endpoint_and_empty_site(client: AsyncClient, seeded: tuple[dict[str, Any], str]) -> None:
    tokens, site_id = seeded
    curve = (await client.get(f"/sites/{site_id}/search/curve", headers=auth(tokens))).json()
    assert curve["summary"] in ("site", "pooled", "default") and curve["computed_at"] is not None
    assert curve["values"] == sorted(curve["values"], reverse=True)
    plain = (await client.post("/sites", json={"domain": "plain.example"}, headers=auth(tokens))).json()["id"]
    base = f"/sites/{plain}/search"
    totals = (await client.get(f"{base}/totals", headers=auth(tokens))).json()
    assert totals["series"] == [] and totals["summary"] is None
    opps = (await client.get(f"{base}/opportunities", headers=auth(tokens))).json()
    assert opps["computed"] is False and opps["items"] == []
    default = (await client.get(f"{base}/curve", headers=auth(tokens))).json()
    assert default["summary"] == "default" and default["computed_at"] is None
    assert (await client.get(f"{base}/queries", headers=auth(tokens))).json()["rows"] == []
    assert (await client.get(f"{base}/page", params={"url": "https://x/"}, headers=auth(tokens))).json()[
        "queries"
    ] == []


async def test_another_organisation_cannot_read_the_data(
    client: AsyncClient, seeded: tuple[dict[str, Any], str]
) -> None:
    _, site_id = seeded
    other = await register(client, "x@example.com", org_name="Other")
    for path in ("totals", "queries", "pages", "opportunities", "curve", "coverage"):
        resp = await client.get(f"/sites/{site_id}/search/{path}", headers=auth(other))
        assert resp.status_code == 404, path
    resp = await client.get(
        f"/sites/{site_id}/search/page", params={"url": "https://a/"}, headers=auth(other)
    )
    assert resp.status_code == 404


def test_every_type_has_an_explanation_made_of_its_own_numbers() -> None:
    sd = {"position": 8.4, "impressions": 700, "expected_ctr_at_3": 0.1, "expected_ctr_at_position": 0.025,
          "extra_clicks_if_position_3": 52.5}  # fmt: skip
    assert "8.4" in explain("STRIKING_DISTANCE", "/p", "q", sd) and "52" in explain(
        "STRIKING_DISTANCE", "/p", "q", sd
    )
    gap = {"ctr": 0.02, "clicks": 10, "impressions": 500, "position": 3.0, "expected_ctr": 0.1,
           "ctr_upper_95": 0.036, "missing_clicks": 40.0}  # fmt: skip
    assert "2.0%" in explain("CTR_GAP", "/p", "q", gap) and "10.0%" in explain("CTR_GAP", "/p", "q", gap)
    decay = {"clicks_last_28d": 40, "clicks_previous_28d": 100, "change_pct": -60.0, "p_value": 1e-9}
    assert "-60.0%" in explain("DECAY", "/p", None, decay)
    cannibal = {"query_impressions": 900, "impressions_on_other_pages": 300, "pages": [{}, {}]}
    assert "2 of your pages" in explain("CANNIBALISATION", None, "q", cannibal)
    assert explain("UNKNOWN", None, None, {}) == ""
