"""Writing one day of Search Console data.

A day is complete exactly when its `gsc_daily_totals` row exists: rows are upserted first, stale rows (ones Google
no longer returns for that day) are removed, and the totals row is written last in the same transaction.
A sync that dies half-way therefore leaves the day "missing" and the next run redoes it.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from searchos.integrations.gsc import DayTotal, GscClient, GscRowData
from searchos.integrations.gsc_dates import is_unpublished_window
from searchos.models import GscDailyTotal, GscRow

CHUNK = 2000  # rows per INSERT


@dataclass(slots=True)
class DayResult:
    day: date
    rows: int
    total_stored: bool


def _row_values(
    site_id: uuid.UUID, day: date, row: GscRowData, run_at: datetime, tag: Callable[[str], bool] | None
) -> dict[str, object]:
    return {
        "site_id": site_id,
        "date": day,
        "page": row.page,
        "query": row.query,
        "country": row.country,
        "device": row.device,
        "clicks": row.clicks,
        "impressions": row.impressions,
        "ctr": row.ctr,
        "position": row.position,
        "is_conversational": bool(tag(row.query)) if tag else False,
        "ingested_at": run_at,
    }


async def upsert_rows(session: AsyncSession, values: list[dict[str, object]]) -> None:
    for start in range(0, len(values), CHUNK):
        stmt = pg_insert(GscRow).values(values[start : start + CHUNK])
        stmt = stmt.on_conflict_do_update(
            index_elements=[
                GscRow.site_id,
                GscRow.date,
                sa.func.md5(GscRow.page),
                sa.func.md5(GscRow.query),
                GscRow.country,
                GscRow.device,
            ],
            set_={
                "clicks": stmt.excluded.clicks,
                "impressions": stmt.excluded.impressions,
                "ctr": stmt.excluded.ctr,
                "position": stmt.excluded.position,
                "is_conversational": stmt.excluded.is_conversational,
                "ingested_at": stmt.excluded.ingested_at,
            },
        )
        await session.execute(stmt)


async def ingest_day(
    sessions: async_sessionmaker[AsyncSession],
    client: GscClient,
    site_id: uuid.UUID,
    day: date,
    *,
    today: date | None = None,
    tag: Callable[[str], bool] | None = None,
) -> DayResult:
    """Fetch and store one day. Any API error propagates before the totals row is written."""
    today = today or datetime.now(UTC).date()
    run_at = datetime.now(UTC)
    total = await client.day_total(day)
    stored = 0
    async for page in client.day_rows(day):
        values = [_row_values(site_id, day, r, run_at, tag) for r in page]
        async with sessions() as session:
            await upsert_rows(session, values)
            await session.commit()
        stored += len(values)

    if total is None:
        if stored == 0 and is_unpublished_window(day, today):
            # Google has not published this day yet: store nothing, so it is fetched again next time.
            return DayResult(day, 0, total_stored=False)
        if stored:
            # Never rebuild totals from rows: they omit anonymised queries. Fail the day; it is retried.
            raise RuntimeError(f"Google returned {stored} rows for {day} but no daily total")
        total = DayTotal(0, 0, 0.0, None)
    async with sessions() as session:
        await session.execute(
            sa.delete(GscRow).where(
                GscRow.site_id == site_id, GscRow.date == day, GscRow.ingested_at < run_at
            )
        )
        stmt = pg_insert(GscDailyTotal).values(
            site_id=site_id,
            date=day,
            clicks=total.clicks,
            impressions=total.impressions,
            ctr=total.ctr,
            position=total.position,
            ingested_at=run_at,
        )
        await session.execute(
            stmt.on_conflict_do_update(
                index_elements=[GscDailyTotal.site_id, GscDailyTotal.date],
                set_={
                    "clicks": stmt.excluded.clicks,
                    "impressions": stmt.excluded.impressions,
                    "ctr": stmt.excluded.ctr,
                    "position": stmt.excluded.position,
                    "ingested_at": stmt.excluded.ingested_at,
                },
            )
        )
        await session.commit()
    return DayResult(day, stored, total_stored=True)


async def existing_total_days(
    sessions: async_sessionmaker[AsyncSession], site_id: uuid.UUID, start: date, end: date
) -> set[date]:
    async with sessions() as session:
        rows = await session.scalars(
            sa.select(GscDailyTotal.date).where(
                GscDailyTotal.site_id == site_id, GscDailyTotal.date.between(start, end)
            )
        )
        return set(rows)
