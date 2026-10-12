"""Bing Webmaster API client (query and page statistics) and storage.

Endpoints (JSON):
  GET https://ssl.bing.com/webmaster/api.svc/json/GetQueryStats?siteUrl=<url>&apikey=<key>
  GET https://ssl.bing.com/webmaster/api.svc/json/GetPageStats?siteUrl=<url>&apikey=<key>
Both take only the site URL (no date range or limit) and return everything Bing has, as {"d": [ ... ]} where
each entry has: Date ("/Date(1399100400000)/", milliseconds since the epoch), Query (the search query for
GetQueryStats; the PAGE URL for GetPageStats), Clicks, Impressions, AvgClickPosition, AvgImpressionPosition.
Bing aggregates in WEEKLY buckets (one Date per week), so these rows are weekly, not daily.

Sources used to confirm the names and shapes (the official reference at
https://learn.microsoft.com/en-us/dotnet/api/microsoft.bing.webmaster.api.interfaces.iwebmasterapi.getquerystats
could not be opened from the build sandbox, so they were checked on the Bing API walkthrough that mirrors it):
  https://bing-webmaster-api.analyticsedge.com/getquerystats/
  https://bing-webmaster-api.analyticsedge.com/getpagestats/

The API key travels in the URL query string (Bing offers nothing else), so the URL is never logged and error
messages never include it.
"""

from __future__ import annotations

import logging
import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, Literal

import httpx
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from searchos.integrations.errors import ProviderApiError
from searchos.models import BingRow

# httpx logs full request URLs at INFO, which would put the API key in the logs.
logging.getLogger("httpx").setLevel(logging.WARNING)

BASE_URL = "https://ssl.bing.com/webmaster/api.svc/json"
Kind = Literal["query", "page"]
ENDPOINTS: dict[Kind, str] = {"query": "GetQueryStats", "page": "GetPageStats"}
CHUNK = 2000

_MS_DATE = re.compile(r"/Date\((-?\d+)(?:[+-]\d{4})?\)/")


@dataclass(frozen=True, slots=True)
class BingStat:
    date: date
    key: str
    clicks: int
    impressions: int
    avg_click_position: float | None
    avg_impression_position: float | None


def parse_ms_date(value: str) -> date:
    """'/Date(1399100400000)/' (optionally with a '-0700' suffix) -> the UTC calendar date."""
    match = _MS_DATE.fullmatch(value.strip().replace("\\/", "/"))
    if match is None:
        raise ValueError(value)
    return datetime.fromtimestamp(int(match.group(1)) / 1000, tz=UTC).date()


def _position(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value) if value >= 0 else None  # Bing uses -1 for "no position"


def parse_stats(body: dict[str, Any]) -> tuple[list[BingStat], int]:
    """(rows, skipped). Entries that are malformed are skipped, never guessed."""
    entries = body.get("d")
    if not isinstance(entries, list):
        raise ValueError("no 'd' list in the answer")
    rows: list[BingStat] = []
    skipped = 0
    for entry in entries:
        try:
            rows.append(
                BingStat(
                    date=parse_ms_date(str(entry["Date"])),
                    key=str(entry["Query"]),
                    clicks=int(entry["Clicks"]),
                    impressions=int(entry["Impressions"]),
                    avg_click_position=_position(entry.get("AvgClickPosition")),
                    avg_impression_position=_position(entry.get("AvgImpressionPosition")),
                )
            )
        except (KeyError, TypeError, ValueError):
            skipped += 1
    return rows, skipped


def _error_message(response: httpx.Response, api_key: str) -> str:
    try:
        body = response.json()
        detail = str(body.get("Message") or body.get("message") or "")
    except (ValueError, AttributeError):
        detail = ""
    detail = (detail or response.text[:150] or f"HTTP {response.status_code}").replace(api_key, "***")
    return f"Bing answered {response.status_code}: {detail}"


async def fetch_stats(
    client: httpx.AsyncClient, kind: Kind, site_url: str, api_key: str
) -> tuple[list[BingStat], int]:
    try:
        response = await client.get(
            f"{BASE_URL}/{ENDPOINTS[kind]}", params={"siteUrl": site_url, "apikey": api_key}
        )
    except httpx.TransportError as exc:
        raise ProviderApiError(0, f"Could not reach Bing: {type(exc).__name__}") from exc
    if not response.is_success:
        raise ProviderApiError(response.status_code, _error_message(response, api_key))
    try:
        return parse_stats(response.json())
    except ValueError as exc:
        raise ProviderApiError(response.status_code, f"Bing sent an unreadable answer ({exc})") from exc


async def store_stats(
    sessions: async_sessionmaker[AsyncSession], site_id: uuid.UUID, kind: Kind, rows: Sequence[BingStat]
) -> int:
    """Upsert on (site, kind, week, key). Returns the number of rows written."""
    run_at = datetime.now(UTC)
    values = [
        {
            "site_id": site_id,
            "kind": kind,
            "date": r.date,
            "key": r.key,
            "clicks": r.clicks,
            "impressions": r.impressions,
            "avg_click_position": r.avg_click_position,
            "avg_impression_position": r.avg_impression_position,
            "ingested_at": run_at,
        }
        for r in rows
    ]
    # Bing can repeat a (week, key) pair: keep the last, as an upsert cannot touch a row twice.
    unique: dict[tuple[date, str], dict[str, Any]] = {
        (r.date, r.key): v for r, v in zip(rows, values, strict=True)
    }
    batch = list(unique.values())
    for start in range(0, len(batch), CHUNK):
        stmt = pg_insert(BingRow).values(batch[start : start + CHUNK])
        stmt = stmt.on_conflict_do_update(
            index_elements=[BingRow.site_id, BingRow.kind, BingRow.date, sa.func.md5(BingRow.key)],
            set_={
                "clicks": stmt.excluded.clicks,
                "impressions": stmt.excluded.impressions,
                "avg_click_position": stmt.excluded.avg_click_position,
                "avg_impression_position": stmt.excluded.avg_impression_position,
                "ingested_at": stmt.excluded.ingested_at,
            },
        )
        async with sessions() as session:
            await session.execute(stmt)
            await session.commit()
    return len(batch)


async def sync_site(
    sessions: async_sessionmaker[AsyncSession],
    client: httpx.AsyncClient,
    site_id: uuid.UUID,
    site_url: str,
    api_key: str,
) -> dict[str, int]:
    """Fetch and store both query and page statistics. Raises `ProviderApiError`."""
    result: dict[str, int] = {}
    for kind in ("query", "page"):
        rows, skipped = await fetch_stats(client, kind, site_url, api_key)
        result[f"{kind}_rows"] = await store_stats(sessions, site_id, kind, rows)
        result[f"{kind}_skipped"] = skipped
    return result
