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
