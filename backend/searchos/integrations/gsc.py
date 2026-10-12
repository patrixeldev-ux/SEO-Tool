"""Search Console `searchAnalytics.query` client: one single-day request set at a time, paginated.

POST https://www.googleapis.com/webmasters/v3/sites/{siteUrl}/searchAnalytics/query
Docs: https://developers.google.com/webmaster-tools/v1/searchanalytics/query

- dimensions page, query, country, device -> the rows we store (Google omits anonymised queries from these)
- dimension date only                     -> the day's real totals (anonymised queries included)
- `dataState: "final"` so only finished data is stored; rowLimit 25000 per request, `startRow` pagination.
- At most `MAX_CONCURRENT` requests are in flight per site (Google's quota is per site).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import date
from typing import Any
from urllib.parse import quote

from searchos.integrations.google_api import SITES_URL, GoogleSession

ROW_LIMIT = 25_000
MAX_CONCURRENT = 5
DIMENSIONS = ["page", "query", "country", "device"]


@dataclass(frozen=True, slots=True)
class GscRowData:
    page: str
    query: str
    country: str
    device: str
    clicks: int
    impressions: int
    ctr: float
    position: float


@dataclass(frozen=True, slots=True)
class DayTotal:
    clicks: int
    impressions: int
    ctr: float
    position: float | None


def parse_row(raw: dict[str, Any]) -> GscRowData | None:
    """None for a malformed row (wrong number of keys, missing numbers): it is skipped, never guessed."""
    keys = raw.get("keys")
    if not isinstance(keys, list) or len(keys) != len(DIMENSIONS):
        return None
    try:
        impressions = int(raw["impressions"])
        clicks = int(raw["clicks"])
        return GscRowData(
            page=str(keys[0]),
            query=str(keys[1]),
            country=str(keys[2]),
            device=str(keys[3]),
            clicks=clicks,
            impressions=impressions,
            ctr=float(raw.get("ctr", clicks / impressions if impressions else 0.0)),
            position=float(raw["position"]),
        )
    except (KeyError, TypeError, ValueError):
        return None


class GscClient:
    def __init__(
        self, google: GoogleSession, property_url: str, *, max_concurrent: int = MAX_CONCURRENT
    ) -> None:
        self._google = google
        self._url = f"{SITES_URL}/{quote(property_url, safe='')}/searchAnalytics/query"
        self._slots = asyncio.Semaphore(max_concurrent)
        self.skipped_rows = 0

    async def _query(self, body: dict[str, Any]) -> list[dict[str, Any]]:
        async with self._slots:
            data = await self._google.post_json(self._url, body)
        rows = data.get("rows") if isinstance(data, dict) else None
        return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []

    async def day_rows(self, day: date) -> AsyncIterator[list[GscRowData]]:
        """Yields the day's rows page by page (25,000 at a time) until Google returns fewer than that."""
        start_row = 0
        while True:
            raw = await self._query(
                {
                    "startDate": day.isoformat(),
                    "endDate": day.isoformat(),
                    "dimensions": DIMENSIONS,
                    "type": "web",
                    "dataState": "final",
                    "rowLimit": ROW_LIMIT,
                    "startRow": start_row,
                }
            )
            parsed = [r for r in map(parse_row, raw) if r is not None]
            self.skipped_rows += len(raw) - len(parsed)
            if parsed:
                yield parsed
            if len(raw) < ROW_LIMIT:
                return
            start_row += ROW_LIMIT

    async def day_total(self, day: date) -> DayTotal | None:
        """The day's site-wide totals from a date-only query, or None when Google has no data for the day."""
        raw = await self._query(
            {
                "startDate": day.isoformat(),
                "endDate": day.isoformat(),
                "dimensions": ["date"],
                "type": "web",
                "dataState": "final",
                "rowLimit": 1,
            }
        )
        if not raw:
            return None
        row = raw[0]
        try:
            return DayTotal(
                clicks=int(row["clicks"]),
                impressions=int(row["impressions"]),
                ctr=float(row.get("ctr", 0.0)),
                position=float(row["position"]) if row.get("position") is not None else None,
            )
        except (KeyError, TypeError, ValueError):
            return None
