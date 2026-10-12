"""Search Console ingest: date logic, pagination, per-day storage, resumable backfill, quota behaviour."""

from __future__ import annotations

import asyncio
import json
from datetime import date, timedelta
from typing import Any

import httpx
import pytest
import respx
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from searchos.core.db import get_engine, get_sessionmaker
from searchos.integrations import google_api, gsc
from searchos.integrations.google_api import GoogleSession
from searchos.integrations.gsc import GscClient
from searchos.integrations.gsc_dates import (
    REFETCH_DAYS,
    days_to_fetch,
    earliest_day,
    latest_day,
    subtract_months,
)
from searchos.integrations.gsc_store import ingest_day
from searchos.models import ConnectionStatus, GscDailyTotal, GscRow, IntegrationConnection, Job, JobStatus
from searchos.worker.gsc_job import gsc_sync_handler
from searchos.worker.jobs import run_job
from tests.conftest import auth, register
from tests.gsc_helpers import (
    PROPERTY,
    QUERY_URL,
    fake_search_console,
    gsc_row,
    make_connected_site,
    make_sync_job,
    total_row,
)
from tests.test_integrations_google import SITES_BODY, connect_site, make_site

TODAY = date(2026, 10, 12)


# ---- dates ------------------------------------------------------------------------------------------
def test_subtract_months_clamps_to_month_end() -> None:
    assert subtract_months(date(2026, 10, 12), 16) == date(2025, 6, 12)
    assert subtract_months(date(2026, 3, 31), 1) == date(2026, 2, 28)
    assert subtract_months(date(2024, 3, 31), 1) == date(2024, 2, 29)
    assert subtract_months(date(2026, 1, 15), 2) == date(2025, 11, 15)


def test_latest_and_earliest_day() -> None:
    assert latest_day(TODAY) == date(2026, 10, 10)
    assert earliest_day(TODAY) == date(2025, 6, 12)


def test_backfill_lists_missing_days_newest_first_and_is_resumable() -> None:
    everything = days_to_fetch(TODAY, set(), mode="backfill")
    assert everything[0] == date(2026, 10, 10) and everything[-1] == date(2025, 6, 12)
    assert everything == sorted(everything, reverse=True)
    assert len(everything) == len(set(everything)) == (date(2026, 10, 10) - date(2025, 6, 12)).days + 1
    done = set(everything[:100])
    assert days_to_fetch(TODAY, done, mode="backfill") == everything[100:]  # nothing already stored is redone


def test_incremental_refetches_newest_days_plus_gaps() -> None:
    last = latest_day(TODAY)
    stored = {last - timedelta(days=i) for i in range(0, 40)} - {last - timedelta(days=20)}  # one gap
    todo = days_to_fetch(TODAY, stored, mode="incremental")
    missing = set(days_to_fetch(TODAY, stored, mode="backfill"))
    assert last - timedelta(days=20) in missing  # the gap
    assert set(todo) == missing | {last - timedelta(days=i) for i in range(REFETCH_DAYS)}
    # With full history only the newest five remain.
    full = set(days_to_fetch(TODAY, set(), mode="backfill"))
    assert days_to_fetch(TODAY, full, mode="incremental") == [last - timedelta(days=i) for i in range(5)]


def test_range_is_clamped_to_retention_and_lag() -> None:
    days = days_to_fetch(TODAY, set(), mode="backfill", start=date(2020, 1, 1), end=date(2030, 1, 1))
    assert days[0] == latest_day(TODAY) and days[-1] == earliest_day(TODAY)


# ---- client: request shape + pagination -------------------------------------------------------------
async def _client_for(session: AsyncSession) -> tuple[GoogleSession, IntegrationConnection]:
    _, _, conn = await make_connected_site(session)
    return GoogleSession(get_sessionmaker(), conn.id), conn


async def test_day_rows_paginate_with_start_row_until_a_short_page(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(gsc, "ROW_LIMIT", 3)
    day = date(2026, 9, 1)
    rows = [gsc_row(f"https://example.com/p{i}", f"q{i}", 1, 10, 3.0) for i in range(7)]
    calls: list[dict[str, Any]] = []
    google, _ = await _client_for(session)
    with respx.mock(assert_all_called=False) as router:
        router.post(QUERY_URL).mock(side_effect=fake_search_console(lambda d: rows, lambda d: None, calls))
        async with google:
            pages = [page async for page in GscClient(google, PROPERTY).day_rows(day)]
    assert [len(p) for p in pages] == [3, 3, 1]
    assert [c["startRow"] for c in calls] == [0, 3, 6]
    first = calls[0]
    assert first["dimensions"] == ["page", "query", "country", "device"]
    assert first["dataState"] == "final" and first["startDate"] == first["endDate"] == "2026-09-01"
    assert first["rowLimit"] == 3


async def test_a_full_last_page_triggers_one_more_request(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(gsc, "ROW_LIMIT", 3)
    rows = [gsc_row("https://example.com/", f"q{i}", 1, 10, 3.0) for i in range(6)]
    calls: list[dict[str, Any]] = []
    google, _ = await _client_for(session)
    with respx.mock(assert_all_called=False) as router:
        router.post(QUERY_URL).mock(side_effect=fake_search_console(lambda d: rows, lambda d: None, calls))
        async with google:
            pages = [p async for p in GscClient(google, PROPERTY).day_rows(date(2026, 9, 1))]
    assert [len(p) for p in pages] == [3, 3] and [c["startRow"] for c in calls] == [0, 3, 6]


async def test_malformed_rows_are_skipped_and_counted(session: AsyncSession) -> None:
    good = gsc_row("https://example.com/", "q", 1, 10, 3.0)
    bad = [{"keys": ["only-one-key"], "clicks": 1}, {"keys": ["a", "b", "c", "d"], "clicks": "x"}]
    google, _ = await _client_for(session)
    with respx.mock(assert_all_called=False) as router:
        router.post(QUERY_URL).mock(side_effect=fake_search_console(lambda d: [good, *bad], lambda d: None))
        async with google:
            client = GscClient(google, PROPERTY)
            pages = [p async for p in client.day_rows(date(2026, 9, 1))]
    assert [len(p) for p in pages] == [1] and client.skipped_rows == 2


# ---- storing one day --------------------------------------------------------------------------------
async def counts(session: AsyncSession, site_id: Any) -> tuple[int, int]:
    rows = await session.scalar(
        sa.select(sa.func.count()).select_from(GscRow).where(GscRow.site_id == site_id)
    )
    totals = await session.scalar(
        sa.select(sa.func.count()).select_from(GscDailyTotal).where(GscDailyTotal.site_id == site_id)
    )
    return int(rows or 0), int(totals or 0)


async def test_totals_come_from_the_date_only_query_not_from_summing_rows(session: AsyncSession) -> None:
    _, site, conn = await make_connected_site(session)
    day = date(2026, 9, 1)
    rows = [
        gsc_row("https://example.com/a", "alpha", 5, 100, 4.0),
        gsc_row("https://example.com/b", "beta", 3, 50, 7.0),
    ]
    # Anonymised queries make the real totals larger than the rows.
    total = total_row(day, clicks=40, impressions=900, position=6.5)
    async with GoogleSession(get_sessionmaker(), conn.id) as google:
        with respx.mock(assert_all_called=False) as router:
            router.post(QUERY_URL).mock(side_effect=fake_search_console(lambda d: rows, lambda d: total))
            result = await ingest_day(
                get_sessionmaker(), GscClient(google, PROPERTY), site.id, day, today=TODAY
            )
    assert result.rows == 2 and result.total_stored
    stored = await session.scalar(sa.select(GscDailyTotal).where(GscDailyTotal.site_id == site.id))
    assert stored is not None and (stored.clicks, stored.impressions, stored.position) == (40, 900, 6.5)
    assert await session.scalar(sa.select(sa.func.sum(GscRow.impressions))) == 150


async def test_rerunning_a_day_upserts_and_removes_rows_google_no_longer_returns(
    session: AsyncSession,
) -> None:
    _, site, conn = await make_connected_site(session)
    day = date(2026, 9, 1)
    first = [
        gsc_row("https://example.com/a", "alpha", 5, 100, 4.0),
        gsc_row("https://example.com/b", "beta", 3, 50, 7.0),
    ]
    second = [gsc_row("https://example.com/a", "alpha", 9, 120, 3.5)]  # alpha updated, beta gone

    async def run(rows: list[dict[str, Any]]) -> None:
        async with GoogleSession(get_sessionmaker(), conn.id) as google:
            with respx.mock(assert_all_called=False) as router:
                router.post(QUERY_URL).mock(
                    side_effect=fake_search_console(lambda d: rows, lambda d: total_row(d, 99, 999))
                )
                await ingest_day(get_sessionmaker(), GscClient(google, PROPERTY), site.id, day, today=TODAY)

    await run(first)
    assert await counts(session, site.id) == (2, 1)
    await run(second)
    assert await counts(session, site.id) == (1, 1)
    row = await session.scalar(
        sa.select(GscRow).where(GscRow.site_id == site.id).execution_options(populate_existing=True)
    )
    assert row is not None and (row.clicks, row.impressions, row.position) == (9, 120, 3.5)


async def test_same_page_and_query_in_different_country_or_device_are_distinct_rows(
    session: AsyncSession,
) -> None:
    _, site, conn = await make_connected_site(session)
    rows = [
        gsc_row("https://example.com/a", "alpha", 1, 10, 4.0, "usa", "DESKTOP"),
        gsc_row("https://example.com/a", "alpha", 1, 10, 4.0, "gbr", "DESKTOP"),
        gsc_row("https://example.com/a", "alpha", 1, 10, 4.0, "usa", "MOBILE"),
    ]
    async with GoogleSession(get_sessionmaker(), conn.id) as google:
        with respx.mock(assert_all_called=False) as router:
            router.post(QUERY_URL).mock(
                side_effect=fake_search_console(lambda d: rows, lambda d: total_row(d, 3, 30))
            )
            await ingest_day(
                get_sessionmaker(), GscClient(google, PROPERTY), site.id, date(2026, 9, 1), today=TODAY
            )
    assert await counts(session, site.id) == (3, 1)


async def test_very_long_queries_and_urls_are_stored(session: AsyncSession) -> None:
    _, site, conn = await make_connected_site(session)
    rows = [gsc_row("https://example.com/" + "p" * 1900, "q " * 1500, 1, 10, 4.0)]
    async with GoogleSession(get_sessionmaker(), conn.id) as google:
        with respx.mock(assert_all_called=False) as router:
            router.post(QUERY_URL).mock(
                side_effect=fake_search_console(lambda d: rows, lambda d: total_row(d, 1, 10))
            )
            await ingest_day(
                get_sessionmaker(), GscClient(google, PROPERTY), site.id, date(2026, 9, 1), today=TODAY
            )
    assert await counts(session, site.id) == (1, 1)


async def test_unpublished_recent_day_stores_nothing_but_old_empty_day_stores_zero(
    session: AsyncSession,
) -> None:
    _, site, conn = await make_connected_site(session)
    async with GoogleSession(get_sessionmaker(), conn.id) as google:
        client = GscClient(google, PROPERTY)
        with respx.mock(assert_all_called=False) as router:
            router.post(QUERY_URL).mock(side_effect=fake_search_console(lambda d: [], lambda d: None))
            recent = await ingest_day(
                get_sessionmaker(), client, site.id, TODAY - timedelta(days=2), today=TODAY
            )
            old = await ingest_day(
                get_sessionmaker(), client, site.id, TODAY - timedelta(days=30), today=TODAY
            )
    assert not recent.total_stored and old.total_stored
    assert await counts(session, site.id) == (0, 1)
    zero = await session.scalar(sa.select(GscDailyTotal).where(GscDailyTotal.site_id == site.id))
    assert zero is not None and zero.clicks == 0 and zero.position is None


async def test_rows_without_a_total_fail_the_day_and_leave_it_unfinished(session: AsyncSession) -> None:
    _, site, conn = await make_connected_site(session)
    rows = [gsc_row("https://example.com/a", "alpha", 5, 100, 4.0)]
    async with GoogleSession(get_sessionmaker(), conn.id) as google:
        with respx.mock(assert_all_called=False) as router:
            router.post(QUERY_URL).mock(side_effect=fake_search_console(lambda d: rows, lambda d: None))
            with pytest.raises(RuntimeError, match="no daily total"):
                await ingest_day(
                    get_sessionmaker(), GscClient(google, PROPERTY), site.id, date(2026, 9, 1), today=TODAY
                )
    assert (await counts(session, site.id))[1] == 0  # not marked complete -> redone next time


# ---- the job ----------------------------------------------------------------------------------------
def steady(day: date) -> list[dict[str, Any]]:
    return [gsc_row(f"https://example.com/p{day.day % 3}", "widgets", 2, 40, 5.0)]


async def run_sync(session: AsyncSession, conn: IntegrationConnection, **payload: Any) -> Job:
    job = await make_sync_job(session, conn, **payload)
    await run_job(job.id, gsc_sync_handler, engine=get_engine())
    await session.refresh(job)
    return job


async def test_backfill_covers_16_months_one_day_per_unit_with_at_most_5_in_flight(
    session: AsyncSession,
) -> None:
    _, site, conn = await make_connected_site(session)
    in_flight = 0
    peak = 0
    inner = fake_search_console(steady, lambda d: total_row(d, 6, 130))

    async def slow(request: httpx.Request) -> httpx.Response:
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.002)
        in_flight -= 1
        return inner(request)

    with respx.mock(assert_all_called=False) as router:
        router.post(QUERY_URL).mock(side_effect=slow)
        job = await run_sync(session, conn, mode="backfill", today=TODAY.isoformat())
    assert job.status is JobStatus.done, job.error
    expected_days = (latest_day(TODAY) - earliest_day(TODAY)).days + 1
    assert (await counts(session, site.id))[1] == expected_days
    assert job.result is not None and job.result["days_fetched"] == expected_days
    assert job.progress == 100
    assert 2 <= peak <= 5
    await session.refresh(conn)
    assert conn.last_sync_at is not None and conn.last_error is None


async def test_backfill_resumes_and_only_fetches_missing_days(session: AsyncSession) -> None:
    _, site, conn = await make_connected_site(session)
    start, end = date(2026, 8, 1), date(2026, 8, 10)
    seen: list[date] = []

    def total(day: date) -> dict[str, Any]:
        seen.append(day)
        return total_row(day, 6, 130)

    with respx.mock(assert_all_called=False) as router:
        router.post(QUERY_URL).mock(side_effect=fake_search_console(steady, total))
        await run_sync(
            session, conn, mode="backfill", today=TODAY.isoformat(), end="2026-08-05", start=start.isoformat()
        )
        assert sorted(seen) == [date(2026, 8, d) for d in range(1, 6)]
        seen.clear()
        job = await run_sync(
            session,
            conn,
            mode="backfill",
            today=TODAY.isoformat(),
            end=end.isoformat(),
            start=start.isoformat(),
        )
    assert sorted(seen) == [date(2026, 8, d) for d in range(6, 11)]
    assert job.status is JobStatus.done and (await counts(session, site.id))[1] == 10


async def test_incremental_refetches_the_newest_five_days_only_when_history_is_complete(
    session: AsyncSession,
) -> None:
    _, site, conn = await make_connected_site(session)
    last = latest_day(TODAY)
    for i in range(60):  # 60 stored days, newest = last
        session.add(
            GscDailyTotal(
                site_id=site.id, date=last - timedelta(days=i), clicks=1, impressions=2, ctr=0.5, position=3.0
            )
        )
    await session.commit()
    seen: list[date] = []

    def total(day: date) -> dict[str, Any]:
        seen.append(day)
        return total_row(day, 7, 70)

    with respx.mock(assert_all_called=False) as router:
        router.post(QUERY_URL).mock(side_effect=fake_search_console(steady, total))
        job = await run_sync(session, conn, mode="incremental", today=TODAY.isoformat())
    # 5 newest days + everything older than the stored 60 days (history gap back to 16 months) are missing.
    assert {last - timedelta(days=i) for i in range(5)} <= set(seen)
    assert job.status is JobStatus.done
    refreshed = await session.scalar(
        sa.select(GscDailyTotal).where(GscDailyTotal.site_id == site.id, GscDailyTotal.date == last)
    )
    await session.refresh(refreshed)
    assert refreshed is not None and refreshed.clicks == 7  # re-fetched value replaced the stored one


async def test_failed_days_are_reported_and_retried_next_run(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(google_api, "BASE_DELAY", 0.0)
    _, site, conn = await make_connected_site(session)
    bad = date(2026, 8, 3)
    flaky = True
    inner = fake_search_console(steady, lambda d: total_row(d, 6, 130))

    def answer(request: httpx.Request) -> httpx.Response:
        if flaky and json.loads(request.content)["startDate"] == bad.isoformat():
            return httpx.Response(503, json={"error": {"message": "backend error"}})
        return inner(request)

    payload = {"mode": "backfill", "today": TODAY.isoformat(), "start": "2026-08-01", "end": "2026-08-05"}
    with respx.mock(assert_all_called=False) as router:
        router.post(QUERY_URL).mock(side_effect=answer)
        job = await run_sync(session, conn, **payload)
        assert job.status is JobStatus.failed and "1 of 5 days failed" in (job.error or "")
        assert (await counts(session, site.id))[1] == 4
        await session.refresh(conn)
        assert conn.last_error and "1 of 5" in conn.last_error
        flaky = False
        job = await run_sync(session, conn, **payload)
    assert job.status is JobStatus.done and (await counts(session, site.id))[1] == 5
    await session.refresh(conn)
    assert conn.last_error is None


async def test_permission_error_stops_the_whole_job_early(session: AsyncSession) -> None:
    _, _, conn = await make_connected_site(session)
    with respx.mock(assert_all_called=False) as router:
        route = router.post(QUERY_URL).respond(
            403, json={"error": {"message": "User does not have sufficient permission"}}
        )
        job = await run_sync(session, conn, mode="backfill", today=TODAY.isoformat())
    assert job.status is JobStatus.failed and "sufficient permission" in (job.error or "")
    assert route.call_count <= 12  # roughly the 5 in-flight days, not the ~480 days of the range
    await session.refresh(conn)
    assert conn.last_error and "sufficient permission" in conn.last_error


async def test_revoked_access_marks_needs_reauth_and_fails_the_job(
    session: AsyncSession, google_env: None
) -> None:
    from searchos.core import crypto

    _, _, conn = await make_connected_site(session)
    conn.credentials_encrypted = crypto.encrypt_json(
        {"access_token": "old", "refresh_token": "1//r", "expires_at": 1.0}
    )
    await session.commit()
    with respx.mock(assert_all_called=False) as router:
        router.post("https://oauth2.googleapis.com/token").respond(400, json={"error": "invalid_grant"})
        job = await run_sync(session, conn, mode="backfill", today=TODAY.isoformat())
    assert job.status is JobStatus.failed
    await session.refresh(conn)
    assert conn.status is ConnectionStatus.needs_reauth


async def test_cancelled_job_does_no_work(session: AsyncSession) -> None:
    from searchos.worker.jobs import JobContext

    _, _, conn = await make_connected_site(session)
    job = await make_sync_job(session, conn, mode="backfill", today=TODAY.isoformat())
    job.status = JobStatus.cancelled
    await session.commit()
    calls = 0

    def answer(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={})

    with respx.mock(assert_all_called=False) as router:
        router.post(QUERY_URL).mock(side_effect=answer)
        result = await gsc_sync_handler(JobContext(job.id, dict(job.payload), get_sessionmaker()))
    assert calls == 0 and result["days_fetched"] > 400  # planned, but nothing fetched
    assert (await session.scalar(sa.select(sa.func.count()).select_from(GscDailyTotal))) == 0


# ---- API + nightly ----------------------------------------------------------------------------------
@pytest.fixture
def enqueued(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    sent: list[str] = []

    async def fake(_session: Any, job: Job) -> bool:
        sent.append(str(job.id))
        return True

    monkeypatch.setattr("searchos.integrations.gsc_sync.enqueue_sync", fake)
    return sent


async def test_picking_a_property_starts_one_backfill_and_coverage_reports_it(
    client: AsyncClient, enqueued: list[str], google_env: None
) -> None:
    tokens = await register(client, "o@example.com")
    site_id = await make_site(client, tokens)
    await connect_site(client, tokens, site_id)
    url = f"/sites/{site_id}/integrations/google/property"
    with respx.mock(assert_all_called=False) as router:
        router.get(google_api.SITES_URL).respond(200, json=SITES_BODY)
        resp = await client.put(url, json={"site_url": PROPERTY}, headers=auth(tokens))
        again = await client.put(url, json={"site_url": PROPERTY}, headers=auth(tokens))
    assert resp.status_code == 200 and again.status_code == 200
    assert len(enqueued) == 1  # picking the same property twice does not start a second backfill
    cov = (await client.get(f"/sites/{site_id}/search/coverage", headers=auth(tokens))).json()
    assert cov["connected"] is True and cov["property_url"] == PROPERTY
    assert cov["active_job"]["payload"]["mode"] == "backfill" and cov["active_job"]["status"] == "queued"
    assert cov["days_stored"] == 0 and cov["days_expected"] > 480

    manual = await client.post(
        f"/sites/{site_id}/integrations/google/sync", json={"mode": "incremental"}, headers=auth(tokens)
    )
    assert manual.status_code == 202 and manual.json()["id"] == enqueued[0]  # the running sync is returned


async def test_coverage_for_a_site_without_google(client: AsyncClient) -> None:
    tokens = await register(client, "o@example.com")
    site_id = (await client.post("/sites", json={"domain": "plain.example"}, headers=auth(tokens))).json()[
        "id"
    ]
    cov = (await client.get(f"/sites/{site_id}/search/coverage", headers=auth(tokens))).json()
    assert cov["connected"] is False and cov["days_stored"] == 0 and cov["active_job"] is None


async def test_nightly_starts_incremental_syncs_for_connected_properties_only(
    session: AsyncSession, enqueued: list[str]
) -> None:
    from searchos.worker.tasks import _nightly_gsc

    _, _site1, _conn1 = await make_connected_site(session, domain="a.example")
    _, _site2, pending = await make_connected_site(session, property_url=None, domain="b.example")
    _, _, conn3 = await make_connected_site(session, domain="c.example")
    await make_sync_job(session, conn3, mode="backfill")  # already queued -> not started twice
    assert pending.status is ConnectionStatus.pending
    started = await _nightly_gsc()
    assert started == 2 or started == 1
    jobs = (await session.scalars(sa.select(Job).where(Job.type == "gsc_sync"))).all()
    modes = sorted(str(j.payload["mode"]) for j in jobs)
    assert modes.count("incremental") == 1 and len(enqueued) == 1


def test_beat_schedule_runs_the_nightly_sync_at_2330_utc() -> None:
    from searchos.worker.app import app

    entry = app.conf.beat_schedule["gsc-nightly-incremental"]
    assert entry["task"] == "searchos.gsc.nightly"
    assert entry["schedule"]._orig_hour == 23 and entry["schedule"]._orig_minute == 30


async def test_conversational_queries_are_tagged_at_ingest_and_retagging_updates_old_rows(
    session: AsyncSession,
) -> None:
    from searchos.integrations.gsc_store import retag_conversational
    from searchos.stats.conversational import is_conversational

    _, site, conn = await make_connected_site(session)
    site_id = site.id
    rows = [
        gsc_row("https://example.com/a", "tell me more", 0, 40, 5.0),
        gsc_row("https://example.com/a", "best widgets", 3, 40, 5.0),
    ]
    with respx.mock(assert_all_called=False) as router:
        router.post(QUERY_URL).mock(
            side_effect=fake_search_console(lambda d: rows, lambda d: total_row(d, 3, 80))
        )
        job = await run_sync(
            session, conn, mode="backfill", today=TODAY.isoformat(), start="2026-08-01", end="2026-08-01"
        )
    assert job.status is JobStatus.done, job.error
    flags = dict((await session.execute(sa.select(GscRow.query, GscRow.is_conversational))).all())
    assert flags == {"tell me more": True, "best widgets": False}

    await session.execute(sa.update(GscRow).values(is_conversational=False))
    await session.commit()
    assert await retag_conversational(get_sessionmaker(), site_id, is_conversational) == 1
    assert await retag_conversational(get_sessionmaker(), site_id, is_conversational) == 0  # idempotent
