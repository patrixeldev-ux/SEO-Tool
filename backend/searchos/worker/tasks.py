from __future__ import annotations

import asyncio
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from searchos.core.auth import purge_expired_refresh_tokens
from searchos.core.config import get_settings
from searchos.core.db import make_engine
from searchos.worker.app import app
from searchos.worker.jobs import JobContext, run_job


async def demo_handler(ctx: JobContext) -> dict[str, Any]:
    """Demo job: N steps with a delay, reporting progress. `fail=true` raises to exercise the failure path."""
    steps = int(ctx.payload.get("steps", 5))
    delay = float(ctx.payload.get("step_seconds", 0.2))
    for step in range(1, steps + 1):
        await asyncio.sleep(delay)
        if ctx.payload.get("fail") and step == max(1, steps // 2):
            raise RuntimeError("demo job asked to fail")
        await ctx.set_progress(round(step * 100 / steps))
    return {"steps_completed": steps}


@app.task(name="searchos.crawl.run")
def crawl_task(job_id: str) -> None:
    """Crawl + audit one site. All parameters live in the `jobs` row payload."""
    from searchos.worker.crawl_job import crawl_handler

    asyncio.run(run_job(uuid.UUID(job_id), crawl_handler))


@app.task(name="searchos.plan.site")
def plan_task(job_id: str) -> None:
    """Re-plan one site's actions. Parameters (site id, trigger) live in the job payload."""
    from searchos.worker.plan_job import plan_handler

    asyncio.run(run_job(uuid.UUID(job_id), plan_handler))


@app.task(name="searchos.gsc.sync")
def gsc_sync_task(job_id: str) -> None:
    """Search Console backfill / incremental sync for one connection. Parameters live in the job payload."""
    from searchos.worker.gsc_job import gsc_sync_handler

    asyncio.run(run_job(uuid.UUID(job_id), gsc_sync_handler))


async def _nightly_gsc() -> int:
    from searchos.integrations.gsc_sync import connections_to_sync, start_sync

    engine = make_engine(get_settings().database_url, null_pool=True)
    started = 0
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            for conn in await connections_to_sync(session):
                if await start_sync(session, conn, "incremental") is not None:
                    started += 1
    finally:
        await engine.dispose()
    return started


@app.task(name="searchos.gsc.nightly")
def gsc_nightly() -> int:
    """Beat-scheduled (23:30 UTC): re-fetch the newest 5 days and any gaps for every connected property."""
    return asyncio.run(_nightly_gsc())


@app.task(name="searchos.bing.sync")
def bing_sync_task(job_id: str) -> None:
    """Bing Webmaster query/page statistics for one connection."""
    from searchos.worker.bing_job import bing_sync_handler

    asyncio.run(run_job(uuid.UUID(job_id), bing_sync_handler))


async def _nightly_bing() -> int:
    from searchos.integrations.bing_sync import bing_connections, start_sync

    engine = make_engine(get_settings().database_url, null_pool=True)
    started = 0
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            for conn in await bing_connections(session):
                if await start_sync(session, conn) is not None:
                    started += 1
    finally:
        await engine.dispose()
    return started


@app.task(name="searchos.bing.nightly")
def bing_nightly() -> int:
    """Beat-scheduled (23:45 UTC): refresh Bing statistics for every connected site."""
    return asyncio.run(_nightly_bing())


@app.task(name="searchos.demo")
def demo_task(job_id: str) -> None:
    asyncio.run(run_job(uuid.UUID(job_id), demo_handler))


async def _purge() -> int:
    engine = make_engine(get_settings().database_url, null_pool=True)
    try:
        async with AsyncSession(engine) as session:
            return await purge_expired_refresh_tokens(session)
    finally:
        await engine.dispose()


@app.task(name="searchos.maintenance.purge_refresh_tokens")
def purge_refresh_tokens() -> int:
    """Beat-scheduled housekeeping: delete long-expired refresh tokens."""
    return asyncio.run(_purge())
