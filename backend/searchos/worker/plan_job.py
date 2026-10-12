"""The plan job: re-plan one site's actions. Triggered after an audit, after the opportunities are recomputed,
and by the "Re-plan" button."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from searchos.models import Site
from searchos.planner.engine import plan_site
from searchos.worker.jobs import JobContext

log = logging.getLogger(__name__)


async def plan_handler(ctx: JobContext) -> dict[str, Any]:
    site_id = uuid.UUID(str(ctx.payload["site_id"]))
    return await plan_site(ctx.sessions, site_id, trigger=str(ctx.payload.get("trigger", "manual")))


async def trigger_plan_for_site(
    sessions: async_sessionmaker[AsyncSession], site_id: uuid.UUID, trigger: str
) -> None:
    """Queue a re-plan from inside another job. Never raises: planning must not fail an audit or a sync."""
    from searchos.worker.launch import start_plan

    try:
        async with sessions() as session:
            site = await session.get(Site, site_id)
            if site is not None:
                await start_plan(session, site.id, site.org_id, trigger=trigger)
    except Exception:
        log.exception("could not queue a plan for site %s", site_id)
