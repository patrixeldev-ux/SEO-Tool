"""Search Console data endpoints (read side): coverage, daily totals, queries, pages, opportunities."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import Annotated

import sqlalchemy as sa
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from searchos.api.schemas import JobOut
from searchos.core.deps import OrgContext, require_role
from searchos.integrations import gsc_sync
from searchos.integrations.gsc_dates import earliest_day, latest_day
from searchos.models import GscDailyTotal, IntegrationConnection, Provider, Role, Site

router = APIRouter(prefix="/sites/{site_id}/search", tags=["search"])
ReadCtx = Annotated[OrgContext, Depends(require_role(Role.client_viewer))]


class CoverageOut(BaseModel):
    connected: bool
    property_url: str | None
    first_day: date | None
    last_day: date | None
    days_stored: int
    days_expected: int
    active_job: JobOut | None
    last_job: JobOut | None
    last_sync_at: datetime | None
    last_error: str | None


@router.get("/coverage", response_model=CoverageOut)
async def coverage(site_id: uuid.UUID, ctx: ReadCtx) -> CoverageOut:
    """How much Search Console history is stored, and what the current sync is doing."""
    await ctx.get_or_404(Site, site_id, "Site")
    conn = await ctx.session.scalar(
        ctx.select(IntegrationConnection).where(
            IntegrationConnection.site_id == site_id, IntegrationConnection.provider == Provider.google
        )
    )
    first, last, count = (
        await ctx.session.execute(
            sa.select(
                sa.func.min(GscDailyTotal.date), sa.func.max(GscDailyTotal.date), sa.func.count()
            ).where(GscDailyTotal.site_id == site_id)
        )
    ).one()
    today = datetime.now(UTC).date()
    active = await gsc_sync.active_sync_job(ctx.session, site_id)
    latest = await gsc_sync.latest_sync_job(ctx.session, site_id)
    return CoverageOut(
        connected=bool(conn and conn.property_url),
        property_url=conn.property_url if conn else None,
        first_day=first,
        last_day=last,
        days_stored=count,
        days_expected=(latest_day(today) - earliest_day(today)).days + 1,
        active_job=JobOut.model_validate(active) if active else None,
        last_job=JobOut.model_validate(latest) if latest else None,
        last_sync_at=conn.last_sync_at if conn else None,
        last_error=conn.last_error if conn else None,
    )
