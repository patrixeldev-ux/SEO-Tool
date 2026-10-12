"""The Search Console sync job: backfill (16 months, resumable) or incremental (newest days + gaps).

One day is one unit of work; up to 5 run at once (Google's per-site quota). Progress goes to the `jobs` row
after every day. Re-running after a crash only redoes days that have no totals row.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections import deque
from datetime import UTC, date, datetime
from typing import Any

import sqlalchemy as sa

from searchos.integrations.errors import NeedsReauthError, ProviderApiError
from searchos.integrations.google_api import GoogleSession
from searchos.integrations.gsc import MAX_CONCURRENT, GscClient
from searchos.integrations.gsc_dates import days_to_fetch, earliest_day, latest_day
from searchos.integrations.gsc_store import existing_total_days, ingest_day
from searchos.models import IntegrationConnection
from searchos.search.insights import recompute_insights
from searchos.stats.conversational import is_conversational
from searchos.worker.jobs import JobContext

log = logging.getLogger(__name__)

FATAL_STATUSES = frozenset({400, 401, 403, 404})  # every other day would fail the same way: stop early


async def gsc_sync_handler(ctx: JobContext) -> dict[str, Any]:
    payload = ctx.payload
    connection_id = uuid.UUID(str(payload["connection_id"]))
    mode = str(payload.get("mode", "incremental"))
    today = date.fromisoformat(payload["today"]) if payload.get("today") else datetime.now(UTC).date()

    async with ctx.sessions() as session:
        conn = await session.get(IntegrationConnection, connection_id)
        if conn is None or not conn.property_url:
            raise RuntimeError("The Google connection is gone or has no property selected.")
        site_id, property_url = conn.site_id, conn.property_url

    existing = await existing_total_days(ctx.sessions, site_id, earliest_day(today), latest_day(today))
    days = days_to_fetch(
        today,
        existing,
        mode=mode,
        start=date.fromisoformat(payload["start"]) if payload.get("start") else None,
        end=date.fromisoformat(payload["end"]) if payload.get("end") else None,
    )
    queue = deque(days)
    total = len(days)
    done = 0
    rows = 0
    failures: list[str] = []
    fatal: Exception | None = None

    async with GoogleSession(ctx.sessions, connection_id) as google:
        client = GscClient(google, property_url)

        async def worker() -> None:
            nonlocal done, rows, fatal
            while fatal is None:
                if await ctx.is_cancelled():
                    return
                try:
                    day = queue.popleft()  # no await between the check and the pop: workers never race
                except IndexError:
                    return
                try:
                    result = await ingest_day(
                        ctx.sessions, client, site_id, day, today=today, tag=is_conversational
                    )
                    rows += result.rows
                except NeedsReauthError as exc:
                    fatal = exc
                except ProviderApiError as exc:
                    if exc.status in FATAL_STATUSES:
                        fatal = exc
                    else:
                        failures.append(f"{day}: {exc.message}")
                except Exception as exc:
                    failures.append(f"{day}: {type(exc).__name__}: {exc}")
                done += 1
                await ctx.set_progress(min(99, round(100 * done / total)))

        await asyncio.gather(*(worker() for _ in range(MAX_CONCURRENT)))

    async with ctx.sessions() as session:
        values: dict[str, Any] = {}
        if fatal is None:
            values["last_error"] = (
                f"{len(failures)} of {total} days failed: {failures[0]}" if failures else None
            )
            if not failures:
                values["last_sync_at"] = datetime.now(UTC)
        elif not isinstance(fatal, NeedsReauthError):  # needs_reauth was already recorded by the session
            values["last_error"] = str(fatal)[:500]
        if values:
            await session.execute(
                sa.update(IntegrationConnection)
                .where(IntegrationConnection.id == connection_id)
                .values(**values)
            )
            await session.commit()
    if fatal is not None:
        raise fatal
    if failures:
        raise RuntimeError(
            f"{len(failures)} of {total} days failed (they are retried next sync). First: {failures[0]}"
        )
    result: dict[str, Any] = {
        "mode": mode,
        "days_fetched": total,
        "rows": rows,
        "skipped_rows": client.skipped_rows,
    }
    try:
        insights = await recompute_insights(ctx.sessions, site_id, today=today)
        result["opportunities"] = (insights or {}).get("counts", {})
    except Exception as exc:  # the data is stored; a failed recompute must not fail the sync
        log.exception("recomputing insights for site %s failed", site_id)
        result["insights_error"] = f"{type(exc).__name__}: {exc}"[:300]
    return result
