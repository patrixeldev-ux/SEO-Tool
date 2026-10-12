"""Bing Webmaster API: parsing, fetching, storing, the key endpoint, the sync job and the nightly sweep.

Every Bing call is mocked with respx; nothing here reaches the network.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

import httpx
import pytest
import respx
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from searchos.core.crypto import decrypt_json
from searchos.core.db import get_engine, get_sessionmaker
from searchos.integrations import bing
from searchos.integrations.bing import BingStat, fetch_stats, parse_ms_date, parse_stats, store_stats
from searchos.integrations.errors import ProviderApiError
from searchos.models import BingRow, ConnectionStatus, IntegrationConnection, Job, JobStatus, Provider
from searchos.worker.bing_job import bing_sync_handler
from searchos.worker.jobs import run_job
from tests.conftest import auth, register

KEY = "bing-secret-key-123456"
QUERY_URL = f"{bing.BASE_URL}/GetQueryStats"
PAGE_URL = f"{bing.BASE_URL}/GetPageStats"


def entry(ms: int, key: str, clicks: int = 3, impressions: int = 40, **extra: Any) -> dict[str, Any]:
    return {
        "Date": f"/Date({ms})/",
        "Query": key,
        "Clicks": clicks,
        "Impressions": impressions,
        "AvgClickPosition": extra.get("click", 4.0),
        "AvgImpressionPosition": extra.get("imp", 6.5),
    }


WEEK1 = 1399100400000  # 2014-05-03T07:00:00Z
WEEK2 = WEEK1 + 7 * 86_400_000


# ---- parsing ----------------------------------------------------------------------------------------
def test_parse_ms_date_handles_plain_offset_and_escaped_forms() -> None:
    assert parse_ms_date("/Date(1399100400000)/") == date(2014, 5, 3)
    assert parse_ms_date("/Date(1399100400000-0700)/") == date(2014, 5, 3)
    assert parse_ms_date("/Date(1399100400000+0100)/") == date(2014, 5, 3)
    assert parse_ms_date("\\/Date(1399100400000)\\/") == date(2014, 5, 3)
    assert parse_ms_date("/Date(0)/") == date(1970, 1, 1)
    assert parse_ms_date("/Date(-86400000)/") == date(1969, 12, 31)


@pytest.mark.parametrize("bad", ["", "2014-05-03", "/Date(abc)/", "Date(1)"])
def test_parse_ms_date_rejects_other_shapes(bad: str) -> None:
    with pytest.raises(ValueError):
        parse_ms_date(bad)


def test_parse_stats_keeps_good_rows_and_counts_malformed_ones() -> None:
    body = {
        "d": [
            entry(WEEK1, "blue widgets", 5, 100, click=3.2, imp=7.0),
            entry(WEEK1, "no position", 0, 12, click=-1, imp=-1),
            {"Date": "garbage", "Query": "x", "Clicks": 1, "Impressions": 2},
            {"Date": f"/Date({WEEK1})/", "Clicks": 1, "Impressions": 2},  # no key
            {"Date": f"/Date({WEEK1})/", "Query": "words", "Clicks": "many", "Impressions": 2},
        ]
    }
    rows, skipped = parse_stats(body)
    assert skipped == 3
    assert [r.key for r in rows] == ["blue widgets", "no position"]
    assert rows[0] == BingStat(date(2014, 5, 3), "blue widgets", 5, 100, 3.2, 7.0)
    assert rows[1].avg_click_position is None and rows[1].avg_impression_position is None  # -1 means none


def test_parse_stats_needs_the_d_list() -> None:
    with pytest.raises(ValueError):
        parse_stats({"nope": []})
    assert parse_stats({"d": []}) == ([], 0)


# ---- fetching ---------------------------------------------------------------------------------------
async def test_fetch_stats_sends_site_and_key_and_parses() -> None:
    with respx.mock() as router:
        route = router.get(QUERY_URL).respond(200, json={"d": [entry(WEEK1, "widgets")]})
        async with httpx.AsyncClient() as client:
            rows, skipped = await fetch_stats(client, "query", "https://example.com/", KEY)
    assert skipped == 0 and rows[0].key == "widgets"
    params = route.calls.last.request.url.params
    assert params["siteUrl"] == "https://example.com/" and params["apikey"] == KEY


async def test_fetch_stats_uses_the_page_endpoint_for_pages() -> None:
    with respx.mock() as router:
        route = router.get(PAGE_URL).respond(200, json={"d": [entry(WEEK1, "https://example.com/a")]})
        async with httpx.AsyncClient() as client:
            rows, _ = await fetch_stats(client, "page", "https://example.com/", KEY)
    assert route.called and rows[0].key == "https://example.com/a"


async def test_errors_never_contain_the_api_key() -> None:
    with respx.mock() as router:
        router.get(QUERY_URL).respond(400, json={"Message": f"ERROR!!! InvalidApiKey {KEY}"})
        async with httpx.AsyncClient() as client:
            with pytest.raises(ProviderApiError) as caught:
                await fetch_stats(client, "query", "https://example.com/", KEY)
    assert caught.value.status == 400
    assert KEY not in caught.value.message and "InvalidApiKey" in caught.value.message


async def test_network_failure_and_garbage_answers_become_provider_errors() -> None:
    async with httpx.AsyncClient() as client:
        with respx.mock() as router:
            router.get(QUERY_URL).mock(side_effect=httpx.ConnectError("boom"))
            with pytest.raises(ProviderApiError) as down:
                await fetch_stats(client, "query", "https://example.com/", KEY)
        with respx.mock() as router:
            router.get(QUERY_URL).respond(200, json={"unexpected": True})
            with pytest.raises(ProviderApiError) as odd:
                await fetch_stats(client, "query", "https://example.com/", KEY)
    assert down.value.status == 0 and "reach Bing" in down.value.message
    assert "unreadable" in odd.value.message


def test_httpx_request_logging_is_quiet_so_keys_stay_out_of_logs() -> None:
    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING


# ---- storing ----------------------------------------------------------------------------------------
async def make_site(client: AsyncClient, tokens: dict[str, Any], domain: str = "example.com") -> str:
    resp = await client.post("/sites", json={"domain": domain}, headers=auth(tokens))
    assert resp.status_code == 201
    return str(resp.json()["id"])


async def bing_rows(session: AsyncSession, site_id: Any) -> list[BingRow]:
    result = await session.scalars(
        sa.select(BingRow)
        .where(BingRow.site_id == site_id)
        .order_by(BingRow.kind, BingRow.date, BingRow.key)
        .execution_options(populate_existing=True)
    )
    return list(result)


async def test_store_stats_is_an_idempotent_upsert(client: AsyncClient, session: AsyncSession) -> None:
    import uuid

    tokens = await register(client, "o@example.com")
    site_id = uuid.UUID(await make_site(client, tokens))
    first = [
        BingStat(date(2014, 5, 3), "widgets", 3, 40, 4.0, 6.0),
        BingStat(date(2014, 5, 3), "x", 1, 9, None, None),
    ]
    assert await store_stats(get_sessionmaker(), site_id, "query", first) == 2
    changed = [BingStat(date(2014, 5, 3), "widgets", 9, 80, 3.0, 5.0)]
    assert await store_stats(get_sessionmaker(), site_id, "query", changed) == 1
    rows = await bing_rows(session, site_id)
    assert len(rows) == 2
    widgets = next(r for r in rows if r.key == "widgets")
    assert (widgets.clicks, widgets.impressions, widgets.avg_click_position) == (9, 80, 3.0)
    # The same key as a page is a different row, and a repeated pair inside one batch keeps the last.
    dup = [
        BingStat(date(2014, 5, 3), "widgets", 1, 1, None, None),
        BingStat(date(2014, 5, 3), "widgets", 2, 2, None, None),
    ]
    assert await store_stats(get_sessionmaker(), site_id, "page", dup) == 1
    assert len(await bing_rows(session, site_id)) == 3


# ---- the key endpoint -------------------------------------------------------------------------------
def answer(request: httpx.Request) -> httpx.Response:
    if request.url.path.endswith("GetQueryStats"):
        return httpx.Response(200, json={"d": [entry(WEEK1, "widgets"), entry(WEEK2, "widgets", 6, 90)]})
    return httpx.Response(200, json={"d": [entry(WEEK1, "https://example.com/a", 4, 50)]})


async def test_saving_a_key_validates_it_by_fetching_encrypts_it_and_stores_rows(
    client: AsyncClient, session: AsyncSession
) -> None:
    tokens = await register(client, "o@example.com")
    site_id = await make_site(client, tokens)
    with respx.mock(assert_all_called=False) as router:
        router.get(QUERY_URL).mock(side_effect=answer)
        router.get(PAGE_URL).mock(side_effect=answer)
        resp = await client.put(
            f"/sites/{site_id}/integrations/bing", json={"api_key": KEY}, headers=auth(tokens)
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["provider"] == "bing" and body["status"] == "connected"
    assert body["property_url"] == "https://example.com/"
    assert KEY not in resp.text  # the key is never sent back
    conn = (
        await session.scalars(
            sa.select(IntegrationConnection).where(IntegrationConnection.provider == Provider.bing)
        )
    ).one()
    assert conn.credentials_encrypted and KEY not in conn.credentials_encrypted
    assert decrypt_json(conn.credentials_encrypted) == {"api_key": KEY}
    import uuid

    rows = await bing_rows(session, uuid.UUID(site_id))
    assert [r.kind for r in rows].count("query") == 2 and [r.kind for r in rows].count("page") == 1
    listing = (await client.get(f"/sites/{site_id}/integrations", headers=auth(tokens))).json()
    assert listing["bing"]["status"] == "connected" and listing["google"] is None


async def test_a_bad_key_is_reported_and_nothing_is_stored(
    client: AsyncClient, session: AsyncSession
) -> None:
    tokens = await register(client, "o@example.com")
    site_id = await make_site(client, tokens)
    with respx.mock(assert_all_called=False) as router:
        router.get(QUERY_URL).respond(400, json={"Message": "ERROR!!! InvalidApiKey"})
        resp = await client.put(
            f"/sites/{site_id}/integrations/bing", json={"api_key": KEY}, headers=auth(tokens)
        )
    assert resp.status_code == 502 and "InvalidApiKey" in resp.json()["detail"] and KEY not in resp.text
    assert (await session.scalar(sa.select(sa.func.count()).select_from(IntegrationConnection))) == 0
    assert (await session.scalar(sa.select(sa.func.count()).select_from(BingRow))) == 0


async def test_site_url_can_be_overridden_and_short_keys_are_refused(client: AsyncClient) -> None:
    tokens = await register(client, "o@example.com")
    site_id = await make_site(client, tokens)
    with respx.mock(assert_all_called=False) as router:
        route = router.get(QUERY_URL).mock(side_effect=answer)
        router.get(PAGE_URL).mock(side_effect=answer)
        ok = await client.put(
            f"/sites/{site_id}/integrations/bing",
            json={"api_key": KEY, "site_url": "https://www.example.com/"},
            headers=auth(tokens),
        )
        short = await client.put(
            f"/sites/{site_id}/integrations/bing", json={"api_key": "short"}, headers=auth(tokens)
        )
    assert ok.status_code == 200 and ok.json()["property_url"] == "https://www.example.com/"
    assert route.calls.last.request.url.params["siteUrl"] == "https://www.example.com/"
    assert short.status_code == 422


async def test_only_admins_can_save_the_key(client: AsyncClient) -> None:
    tokens = await register(client, "o@example.com")
    other = await register(client, "x@example.com", org_name="Other")
    site_id = await make_site(client, tokens)
    resp = await client.put(f"/sites/{site_id}/integrations/bing", json={"api_key": KEY}, headers=auth(other))
    assert resp.status_code in (403, 404)


# ---- the job ----------------------------------------------------------------------------------------
async def connected_bing(session: AsyncSession, client: AsyncClient) -> tuple[str, IntegrationConnection]:
    tokens = await register(client, "o@example.com")
    site_id = await make_site(client, tokens)
    with respx.mock(assert_all_called=False) as router:
        router.get(QUERY_URL).mock(side_effect=answer)
        router.get(PAGE_URL).mock(side_effect=answer)
        assert (
            await client.put(
                f"/sites/{site_id}/integrations/bing", json={"api_key": KEY}, headers=auth(tokens)
            )
        ).status_code == 200
    conn = (
        await session.scalars(
            sa.select(IntegrationConnection).where(IntegrationConnection.provider == Provider.bing)
        )
    ).one()
    return site_id, conn


async def run_sync(session: AsyncSession, conn: IntegrationConnection) -> Job:
    job = Job(
        org_id=conn.org_id, site_id=conn.site_id, type="bing_sync", payload={"connection_id": str(conn.id)}
    )
    session.add(job)
    await session.commit()
    await run_job(job.id, bing_sync_handler, engine=get_engine())
    await session.refresh(job)
    return job


async def test_sync_job_refreshes_rows_and_records_the_sync(
    session: AsyncSession, client: AsyncClient
) -> None:
    _, conn = await connected_bing(session, client)
    with respx.mock(assert_all_called=False) as router:
        router.get(QUERY_URL).respond(
            200, json={"d": [entry(WEEK1, "widgets", 11, 200), entry(WEEK2, "gadgets")]}
        )
        router.get(PAGE_URL).respond(200, json={"d": []})
        job = await run_sync(session, conn)
    assert job.status is JobStatus.done and job.result == {
        "query_rows": 2,
        "query_skipped": 0,
        "page_rows": 0,
        "page_skipped": 0,
    }
    await session.refresh(conn)
    assert conn.status is ConnectionStatus.connected and conn.last_error is None and conn.last_sync_at
    rows = await bing_rows(session, conn.site_id)
    assert {(r.kind, r.key) for r in rows} == {
        ("query", "widgets"),
        ("query", "gadgets"),
        ("page", "https://example.com/a"),
    }
    assert next(r for r in rows if r.key == "widgets" and r.date == date(2014, 5, 3)).clicks == 11


@pytest.mark.parametrize("code", [400, 401, 403])
async def test_a_rejected_key_marks_the_connection_as_needing_attention(
    session: AsyncSession, client: AsyncClient, code: int
) -> None:
    _, conn = await connected_bing(session, client)
    with respx.mock(assert_all_called=False) as router:
        router.get(QUERY_URL).respond(code, json={"Message": "InvalidApiKey"})
        job = await run_sync(session, conn)
    assert job.status is JobStatus.failed and KEY not in (job.error or "")
    await session.refresh(conn)
    assert conn.status is ConnectionStatus.needs_reauth and "InvalidApiKey" in (conn.last_error or "")


async def test_a_server_error_keeps_the_connection_but_records_it(
    session: AsyncSession, client: AsyncClient
) -> None:
    _, conn = await connected_bing(session, client)
    with respx.mock(assert_all_called=False) as router:
        router.get(QUERY_URL).respond(503, text="down")
        job = await run_sync(session, conn)
    assert job.status is JobStatus.failed
    await session.refresh(conn)
    assert conn.status is ConnectionStatus.connected and "503" in (conn.last_error or "")


async def test_an_unreadable_stored_key_needs_re_entering(session: AsyncSession, client: AsyncClient) -> None:
    _, conn = await connected_bing(session, client)
    conn.credentials_encrypted = "not-a-valid-token"
    await session.commit()
    job = await run_sync(session, conn)
    assert job.status is JobStatus.failed
    await session.refresh(conn)
    assert conn.status is ConnectionStatus.needs_reauth


# ---- API sync, nightly, disconnect ------------------------------------------------------------------
@pytest.fixture
def enqueued(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    sent: list[str] = []

    class FakeTask:
        @staticmethod
        def delay(job_id: str) -> None:
            sent.append(job_id)

    monkeypatch.setattr("searchos.worker.tasks.bing_sync_task", FakeTask)
    return sent


async def test_manual_sync_starts_one_job_and_needs_a_connection(
    client: AsyncClient, session: AsyncSession, enqueued: list[str]
) -> None:
    tokens = await register(client, "o@example.com")
    site_id = await make_site(client, tokens)
    none = await client.post(f"/sites/{site_id}/integrations/bing/sync", headers=auth(tokens))
    assert none.status_code == 409
    other_id, _ = await connected_bing_for(session, client, tokens, site_id)
    first = await client.post(f"/sites/{other_id}/integrations/bing/sync", headers=auth(tokens))
    again = await client.post(f"/sites/{other_id}/integrations/bing/sync", headers=auth(tokens))
    assert first.status_code == 202 and again.json()["id"] == first.json()["id"] and len(enqueued) == 1


async def connected_bing_for(
    session: AsyncSession, client: AsyncClient, tokens: dict[str, Any], site_id: str
) -> tuple[str, IntegrationConnection]:
    with respx.mock(assert_all_called=False) as router:
        router.get(QUERY_URL).mock(side_effect=answer)
        router.get(PAGE_URL).mock(side_effect=answer)
        assert (
            await client.put(
                f"/sites/{site_id}/integrations/bing", json={"api_key": KEY}, headers=auth(tokens)
            )
        ).status_code == 200
    conn = (
        await session.scalars(
            sa.select(IntegrationConnection).where(IntegrationConnection.provider == Provider.bing)
        )
    ).one()
    return site_id, conn


async def test_nightly_syncs_connected_bing_sites_only(
    client: AsyncClient, session: AsyncSession, enqueued: list[str]
) -> None:
    from searchos.worker.tasks import _nightly_bing

    _, conn = await connected_bing(session, client)
    conn2 = IntegrationConnection(
        org_id=conn.org_id, site_id=conn.site_id, provider=Provider.google, status=ConnectionStatus.connected
    )
    session.add(conn2)
    await session.commit()
    assert await _nightly_bing() == 1 and len(enqueued) == 1
    conn.status = ConnectionStatus.needs_reauth
    await session.commit()
    assert await _nightly_bing() == 0  # a rejected key is not hammered every night


def test_beat_schedule_runs_the_bing_sweep_at_2345_utc() -> None:
    from searchos.worker.app import app

    entry_ = app.conf.beat_schedule["bing-nightly"]
    assert entry_["task"] == "searchos.bing.nightly"
    assert entry_["schedule"]._orig_hour == 23 and entry_["schedule"]._orig_minute == 45


async def test_disconnect_deletes_the_key_but_keeps_imported_rows(
    client: AsyncClient, session: AsyncSession
) -> None:
    tokens = await register(client, "o@example.com")
    site_id = await make_site(client, tokens)
    await connected_bing_for(session, client, tokens, site_id)
    resp = await client.delete(f"/sites/{site_id}/integrations/bing", headers=auth(tokens))
    assert resp.status_code == 204
    assert (await session.scalar(sa.select(sa.func.count()).select_from(IntegrationConnection))) == 0
    assert (await session.scalar(sa.select(sa.func.count()).select_from(BingRow))) == 3
    assert (
        await client.delete(f"/sites/{site_id}/integrations/bing", headers=auth(tokens))
    ).status_code == 204
