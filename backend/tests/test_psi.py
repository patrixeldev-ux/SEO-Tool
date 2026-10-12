"""PageSpeed Insights: parsing (field preferred, lab fallback, INP never from lab), page selection, the job step,
and the Core Web Vitals checks end to end on a crawled site."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import respx
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from searchos.core.config import get_settings
from searchos.core.db import get_engine, get_sessionmaker
from searchos.devtools.fixture_server import FixtureServer
from searchos.integrations.errors import ProviderApiError
from searchos.integrations.psi import PSI_URL, Candidate, fetch_psi, parse_psi, select_psi_urls
from searchos.models import Crawl, GscRow, Issue, Page, PsiResult
from searchos.worker.audit_job import match_key, run_audit_for_crawl, run_psi_step
from searchos.worker.crawl_job import crawl_handler
from searchos.worker.jobs import JobContext, run_job
from tests.conftest import auth, register
from tests.helpers import make_crawl


def psi_body(
    *, lcp: int | None = None, inp: int | None = None, cls: int | None = None, lab_lcp: float | None = None,
    lab_cls: float | None = None, lab_inp: float | None = None, score: float | None = 0.5,
) -> dict[str, Any]:  # fmt: skip
    metrics: dict[str, Any] = {}
    if lcp is not None:
        metrics["LARGEST_CONTENTFUL_PAINT_MS"] = {"percentile": lcp, "category": "SLOW"}
    if inp is not None:
        metrics["INTERACTION_TO_NEXT_PAINT"] = {"percentile": inp, "category": "SLOW"}
    if cls is not None:
        metrics["CUMULATIVE_LAYOUT_SHIFT_SCORE"] = {"percentile": cls, "category": "SLOW"}
    audits: dict[str, Any] = {}
    if lab_lcp is not None:
        audits["largest-contentful-paint"] = {"numericValue": lab_lcp}
    if lab_cls is not None:
        audits["cumulative-layout-shift"] = {"numericValue": lab_cls}
    if lab_inp is not None:
        audits["interaction-to-next-paint"] = {"numericValue": lab_inp}
    body: dict[str, Any] = {
        "lighthouseResult": {"audits": audits, "categories": {"performance": {"score": score}}}
    }
    if metrics:
        body["loadingExperience"] = {"metrics": metrics}
    return body


# ---- parsing ----------------------------------------------------------------------------------------
def test_field_data_is_used_and_cls_is_divided_by_100() -> None:
    m = parse_psi(psi_body(lcp=5200, inp=650, cls=27, lab_lcp=1000, lab_cls=0.01))
    assert (m.lcp_ms, m.lcp_source) == (5200.0, "field")
    assert (m.inp_ms, m.inp_source) == (650.0, "field")
    assert m.cls == pytest.approx(0.27) and m.cls_source == "field"
    assert m.performance_score == pytest.approx(50.0)


def test_cls_of_zero_field_value_is_kept_not_replaced_by_lab() -> None:
    m = parse_psi(psi_body(lcp=1800, inp=100, cls=0, lab_cls=0.4))
    assert m.cls == 0.0 and m.cls_source == "field"


def test_lab_fallback_for_lcp_and_cls_only_and_never_for_inp() -> None:
    m = parse_psi(psi_body(lab_lcp=4500.5, lab_cls=0.12, lab_inp=900.0))
    assert (m.lcp_ms, m.lcp_source) == (4500.5, "lab")
    assert m.cls == pytest.approx(0.12) and m.cls_source == "lab"
    assert m.inp_ms is None and m.inp_source is None  # the lab INP is deliberately ignored


def test_partial_field_data_mixes_sources_per_metric() -> None:
    m = parse_psi(psi_body(lcp=3000, lab_cls=0.05))
    assert (m.lcp_source, m.cls_source, m.inp_source) == ("field", "lab", None)


def test_empty_or_odd_bodies_give_no_data() -> None:
    assert not parse_psi({}).has_data
    assert not parse_psi(
        {"loadingExperience": {"metrics": {"LARGEST_CONTENTFUL_PAINT_MS": {"percentile": "x"}}}}
    ).has_data
    assert parse_psi(psi_body(lab_lcp=1200.0)).has_data


# ---- selection --------------------------------------------------------------------------------------
def cand(path: str, inlinks: int = 0, indexable: bool = True, home: bool = False) -> Candidate:
    return Candidate(f"https://s.test{path}", inlinks, indexable, home)


def test_with_search_console_data_the_most_visible_pages_come_first() -> None:
    pages = [
        cand("/", 9, home=True),
        cand("/a", 1),
        cand("/b", 2),
        cand("/c", 3),
        cand("/noindex", 5, indexable=False),
    ]
    impressions = {"https://s.test/a": 500, "https://s.test/c": 900, "https://s.test/noindex": 10_000}
    assert select_psi_urls(pages, impressions, limit=2) == ["https://s.test/c", "https://s.test/a"]


def test_slots_left_after_search_console_pages_are_filled_with_homepage_then_most_linked() -> None:
    pages = [cand("/", 1, home=True), cand("/a", 1), cand("/b", 7), cand("/c", 3)]
    chosen = select_psi_urls(pages, {"https://s.test/a": 50}, limit=4)
    assert chosen == ["https://s.test/a", "https://s.test/", "https://s.test/b", "https://s.test/c"]


def test_without_search_console_it_is_the_homepage_plus_the_most_linked_pages_up_to_20() -> None:
    pages = [cand("/", 0, home=True)] + [cand(f"/p{i:02d}", i) for i in range(40)]
    chosen = select_psi_urls(pages, None)
    assert len(chosen) == 20 and chosen[0] == "https://s.test/"
    assert chosen[1:3] == ["https://s.test/p39", "https://s.test/p38"] and chosen[-1] == "https://s.test/p21"
    assert select_psi_urls(pages, {}) == chosen  # empty Search Console data behaves like none
    assert select_psi_urls([], None) == []


def test_match_key_ignores_scheme_www_and_trailing_slash() -> None:
    assert match_key("https://www.Example.com/a/") == match_key("http://example.com/a") == "example.com/a"
    assert match_key("https://example.com/") == "example.com"
    assert match_key("https://example.com/a?x=1") != match_key("https://example.com/a")


# ---- fetching ---------------------------------------------------------------------------------------
async def no_sleep(_seconds: float) -> None:
    return None


async def test_fetch_sends_url_strategy_and_optional_key() -> None:
    with respx.mock(assert_all_called=False) as router:
        route = router.get(PSI_URL).respond(200, json=psi_body(lcp=2000, inp=150, cls=3))
        async with httpx.AsyncClient() as client:
            await fetch_psi(client, "https://example.com/a", api_key="SECRETKEY123")
            await fetch_psi(client, "https://example.com/a")
    first, second = (parse_qs(urlparse(str(c.request.url)).query) for c in route.calls)
    assert (
        first["url"] == ["https://example.com/a"]
        and first["strategy"] == ["mobile"]
        and first["key"] == ["SECRETKEY123"]
    )
    assert "key" not in second


async def test_fetch_retries_429_and_5xx_then_gives_up_without_leaking_the_key() -> None:
    waits: list[float] = []

    async def sleep(seconds: float) -> None:
        waits.append(seconds)

    with respx.mock(assert_all_called=False) as router:
        router.get(PSI_URL).mock(
            side_effect=[
                httpx.Response(429),
                httpx.Response(503),
                httpx.Response(200, json=psi_body(lcp=1000)),
            ]
        )
        async with httpx.AsyncClient() as client:
            assert (await fetch_psi(client, "https://e.com/", sleep=sleep)).lcp_ms == 1000.0
        assert waits == [2.0, 4.0]
        router.get(PSI_URL).respond(
            400,
            json={
                "error": {
                    "message": "Lighthouse returned error: FAILED_DOCUMENT_REQUEST for key=SECRETKEY123"
                }
            },
        )
        async with httpx.AsyncClient() as client:
            with pytest.raises(ProviderApiError) as info:
                await fetch_psi(client, "https://e.com/", api_key="SECRETKEY123", sleep=sleep)
    assert (
        info.value.status == 400
        and "SECRETKEY123" not in str(info.value)
        and "FAILED_DOCUMENT_REQUEST" in str(info.value)
    )


# ---- the audit step ---------------------------------------------------------------------------------
def respond_by_url(table: dict[str, dict[str, Any]], default: dict[str, Any]) -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        url = parse_qs(urlparse(str(request.url)).query)["url"][0]
        return httpx.Response(200, json=table.get(url, default))

    return handler


async def crawl_fixture(session: AsyncSession, server: FixtureServer) -> tuple[Crawl, Any]:
    _, site, crawl, job = await make_crawl(session, server, extra_payload={"js_check": False})
    await run_job(job.id, crawl_handler, engine=get_engine())
    await session.refresh(crawl)
    return crawl, site


def step_context(crawl: Crawl, **payload: Any) -> JobContext:
    return JobContext(crawl.job_id, payload, get_sessionmaker())  # type: ignore[arg-type]


async def test_psi_step_stores_results_and_the_audit_reports_poor_vitals(
    session: AsyncSession, fixture_site: FixtureServer
) -> None:
    crawl, _ = await crawl_fixture(session, fixture_site)
    assert (
        await session.scalar(sa.select(sa.func.count()).select_from(PsiResult))
    ) == 0  # fixture crawl: skipped
    homepage = crawl.site_info["homepage_url"]
    others = (
        await session.scalars(
            sa.select(Page.normalised_url)
            .where(Page.crawl_id == crawl.id, Page.status_code == 200, Page.normalised_url != homepage)
            .order_by(Page.inlinks_count.desc(), Page.normalised_url)
            .limit(3)
        )
    ).all()
    lab_only, good = others[0], others[1]
    table = {
        homepage: psi_body(lcp=5200, inp=650, cls=30),  # poor on all three, from real users
        lab_only: psi_body(
            lab_lcp=4500.0, lab_cls=0.3, lab_inp=2000.0
        ),  # lab only: LCP + CLS evaluated, no INP
        good: psi_body(lcp=1900, inp=120, cls=2),
    }
    with respx.mock(assert_all_called=False) as router:
        router.get(PSI_URL).mock(side_effect=respond_by_url(table, psi_body(lcp=1500, inp=100, cls=1)))
        warnings = await run_psi_step(step_context(crawl, psi=True), crawl.id)
    assert warnings == []
    stored = {
        r.url: r
        for r in (await session.scalars(sa.select(PsiResult).where(PsiResult.crawl_id == crawl.id))).all()
    }
    assert len(stored) == 20  # no Search Console data: homepage + the 19 most linked pages
    assert (
        stored[lab_only].inp_ms is None
        and stored[lab_only].lcp_source == "lab"
        and stored[lab_only].cls_source == "lab"
    )
    assert stored[homepage].cls == pytest.approx(0.30)

    await run_audit_for_crawl(get_sessionmaker(), crawl.id)
    issues = {
        (check, url)
        for check, url in (
            await session.execute(
                sa.select(Issue.check_id, Page.normalised_url)
                .join(Page, Page.id == Issue.page_id)
                .where(Issue.crawl_id == crawl.id, Issue.check_id.like("CWV_%"))
            )
        ).all()
    }
    assert issues == {
        ("CWV_LCP_POOR", homepage),
        ("CWV_INP_POOR", homepage),
        ("CWV_CLS_POOR", homepage),
        ("CWV_LCP_POOR", lab_only),
        ("CWV_CLS_POOR", lab_only),
    }
    detail = await session.scalar(
        sa.select(Issue.details).where(Issue.check_id == "CWV_LCP_POOR", Issue.crawl_id == crawl.id).limit(1)
    )
    assert detail is not None and detail["source"] in ("field", "lab")


async def test_psi_step_uses_search_console_impressions_to_pick_pages(
    session: AsyncSession, fixture_site: FixtureServer
) -> None:
    crawl, site = await crawl_fixture(session, fixture_site)
    rows = (
        await session.scalars(
            sa.select(Page.normalised_url).where(Page.crawl_id == crawl.id, Page.status_code == 200)
        )
    ).all()
    favourites = sorted(rows)[-3:]
    from datetime import date

    await session.execute(
        sa.insert(GscRow),
        [
            {
                "site_id": site.id, "date": date(2026, 10, 1), "page": url, "query": "q", "country": "usa",
                "device": "DESKTOP", "clicks": 1, "impressions": 1000 + i, "ctr": 0.001, "position": 4.0,
            }
            for i, url in enumerate(favourites)
        ],
    )  # fmt: skip
    await session.commit()
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(parse_qs(urlparse(str(request.url)).query)["url"][0])
        return httpx.Response(200, json=psi_body(lcp=1000, inp=100, cls=1))

    with respx.mock(assert_all_called=False) as router:
        router.get(PSI_URL).mock(side_effect=handler)
        await run_psi_step(step_context(crawl, psi=True), crawl.id)
    assert len(seen) == 20 and set(favourites) <= set(seen)


async def test_psi_step_reports_a_warning_when_every_page_fails_and_skips_private_crawls(
    session: AsyncSession, fixture_site: FixtureServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    import searchos.integrations.psi as psi_module

    monkeypatch.setattr(psi_module, "MAX_ATTEMPTS", 1)
    crawl, _ = await crawl_fixture(session, fixture_site)
    with respx.mock(assert_all_called=False) as router:
        route = router.get(PSI_URL).respond(500, json={"error": {"message": "backend down"}})
        assert (
            await run_psi_step(step_context(crawl, allow_private_hosts=True), crawl.id) == []
        )  # private test crawl
        assert route.call_count == 0
        warnings = await run_psi_step(step_context(crawl, psi=True), crawl.id)
    assert len(warnings) == 1 and "Core Web Vitals checks were not run" in warnings[0]
    errors = (await session.scalars(sa.select(PsiResult.error).where(PsiResult.crawl_id == crawl.id))).all()
    assert len(errors) == 20 and all(e and "backend down" in e for e in errors)
    # With only failed results nothing is attached, so the audit reports no CWV findings.
    await run_audit_for_crawl(get_sessionmaker(), crawl.id)
    assert (
        await session.scalar(
            sa.select(sa.func.count()).select_from(Issue).where(Issue.check_id.like("CWV_%"))
        )
    ) == 0


async def test_psi_can_be_switched_off(
    session: AsyncSession, fixture_site: FixtureServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    crawl, _ = await crawl_fixture(session, fixture_site)
    monkeypatch.setenv("PSI_ENABLED", "false")
    get_settings.cache_clear()
    try:
        with respx.mock(assert_all_called=False) as router:
            route = router.get(PSI_URL).respond(200, json=psi_body(lcp=1000))
            assert await run_psi_step(step_context(crawl, psi=True), crawl.id) == []
            assert route.call_count == 0
    finally:
        get_settings.cache_clear()


# ---- API --------------------------------------------------------------------------------------------
async def test_audit_api_shows_psi_per_page_and_lists_cwv_checks_as_not_run(
    client: AsyncClient, session: AsyncSession, fixture_site: FixtureServer
) -> None:
    from tests.test_audits_api import make_site, run_against_fixture  # reuse the audit API helpers

    tokens = await register(client, "o@example.com")
    site_id = await make_site(client, tokens)
    from unittest.mock import patch

    async def ok(*_a: Any, **_k: Any) -> bool:
        return True

    with patch("searchos.api.audits.enqueue_crawl", ok):
        audit = (
            await client.post(f"/sites/{site_id}/audits", json={"page_limit": 30}, headers=auth(tokens))
        ).json()
    await run_against_fixture(session, fixture_site, audit["id"])
    issues = (await client.get(f"/audits/{audit['id']}/issues", headers=auth(tokens))).json()
    not_run = {n["check_id"]: n for n in issues["not_run"]}
    assert set(not_run) == {"CWV_LCP_POOR", "CWV_INP_POOR", "CWV_CLS_POOR"}
    assert "PageSpeed" in not_run["CWV_LCP_POOR"]["reason"]
    assert not {"CWV_LCP_POOR", "CWV_INP_POOR", "CWV_CLS_POOR"} & {p["check_id"] for p in issues["passed"]}

    home = (
        await client.get(f"/audits/{audit['id']}/pages", params={"q": "/index"}, headers=auth(tokens))
    ).json()
    pages = (await client.get(f"/audits/{audit['id']}/pages", headers=auth(tokens))).json()["items"]
    page = pages[0]
    assert (await client.get(f"/audits/{audit['id']}/pages/{page['id']}", headers=auth(tokens))).json()[
        "psi"
    ] is None
    session.add(
        PsiResult(
            site_id=crawl_site(audit),
            crawl_id=audit["id"],
            url=page["url"],
            lcp_ms=3100,
            lcp_source="field",
            cls=0.04,
            cls_source="lab",
        )
    )
    await session.commit()
    detail = (await client.get(f"/audits/{audit['id']}/pages/{page['id']}", headers=auth(tokens))).json()
    assert (
        detail["psi"]["lcp_ms"] == 3100
        and detail["psi"]["lcp_source"] == "field"
        and detail["psi"]["inp_ms"] is None
    )
    assert json.dumps(home)  # the search param keeps working


def crawl_site(audit: dict[str, Any]) -> Any:
    import uuid

    return uuid.UUID(audit["site_id"])
