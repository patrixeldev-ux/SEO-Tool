"""Shared helpers for tests that need a connected Google property or fake Search Console answers."""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Callable
from datetime import date
from typing import Any
from urllib.parse import quote

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from searchos.core.crypto import encrypt_json
from searchos.integrations.google_api import SITES_URL
from searchos.models import ConnectionStatus, IntegrationConnection, Job, Org, Provider, Site

PROPERTY = "sc-domain:example.com"
QUERY_URL = f"{SITES_URL}/{quote(PROPERTY, safe='')}/searchAnalytics/query"


async def make_connected_site(
    session: AsyncSession, *, property_url: str | None = PROPERTY, domain: str = "example.com"
) -> tuple[Org, Site, IntegrationConnection]:
    org = Org(name=f"Org {uuid.uuid4().hex[:6]}")
    session.add(org)
    await session.flush()
    site = Site(org_id=org.id, domain=domain, display_name=domain)
    session.add(site)
    await session.flush()
    conn = IntegrationConnection(
        org_id=org.id,
        site_id=site.id,
        provider=Provider.google,
        credentials_encrypted=encrypt_json(
            {
                "access_token": "ya29.test",
                "refresh_token": "1//refresh",
                "expires_at": time.time() + 3000,
                "scope": "x",
            }
        ),
        property_url=property_url,
        status=ConnectionStatus.connected if property_url else ConnectionStatus.pending,
    )
    session.add(conn)
    await session.commit()
    return org, site, conn


async def make_sync_job(session: AsyncSession, conn: IntegrationConnection, **payload: Any) -> Job:
    job = Job(
        org_id=conn.org_id,
        site_id=conn.site_id,
        type="gsc_sync",
        payload={"connection_id": str(conn.id), **payload},
    )
    session.add(job)
    await session.commit()
    return job


def gsc_row(
    page: str,
    query: str,
    clicks: int,
    impressions: int,
    position: float,
    country: str = "usa",
    device: str = "DESKTOP",
) -> dict[str, Any]:
    return {
        "keys": [page, query, country, device],
        "clicks": clicks,
        "impressions": impressions,
        "ctr": clicks / impressions if impressions else 0.0,
        "position": position,
    }


def total_row(day: date, clicks: int, impressions: int, position: float = 5.0) -> dict[str, Any]:
    return {
        "keys": [day.isoformat()],
        "clicks": clicks,
        "impressions": impressions,
        "ctr": clicks / impressions if impressions else 0.0,
        "position": position,
    }


def fake_search_console(
    rows_for_day: Callable[[date], list[dict[str, Any]]],
    total_for_day: Callable[[date], dict[str, Any] | None],
    calls: list[dict[str, Any]] | None = None,
) -> Callable[[httpx.Request], httpx.Response]:
    """A respx side effect that answers like the searchAnalytics.query endpoint (with startRow paging)."""

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if calls is not None:
            calls.append(body)
        day = date.fromisoformat(body["startDate"])
        assert body["startDate"] == body["endDate"], "one request set per single day"
        if body["dimensions"] == ["date"]:
            total = total_for_day(day)
            return httpx.Response(200, json={"rows": [total]} if total else {})
        rows = rows_for_day(day)
        start = int(body.get("startRow", 0))
        page = rows[start : start + int(body["rowLimit"])]
        return httpx.Response(200, json={"rows": page} if page else {})

    return handler
