"""Bing Webmaster sync jobs: create, enqueue, and the nightly sweep."""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from searchos.models import ConnectionStatus, IntegrationConnection, Job, JobStatus, Provider

JOB_TYPE = "bing_sync"


async def start_sync(
    session: AsyncSession, conn: IntegrationConnection, created_by: uuid.UUID | None = None
) -> Job | None:
    """Queue a sync unless one is already queued or running for the site. None when Bing is not connected."""
    if conn.status is not ConnectionStatus.connected or not conn.credentials_encrypted:
        return None
    existing = await session.scalar(
        sa.select(Job).where(
            Job.site_id == conn.site_id,
            Job.type == JOB_TYPE,
            Job.status.in_((JobStatus.queued, JobStatus.running)),
        )
    )
    if existing is not None:
        return existing
    job = Job(
        org_id=conn.org_id,
        site_id=conn.site_id,
        created_by=created_by,
        type=JOB_TYPE,
        payload={"connection_id": str(conn.id)},
    )
    session.add(job)
    await session.commit()
    from searchos.worker.tasks import bing_sync_task

    try:
        bing_sync_task.delay(str(job.id))
    except Exception:
        job.status = JobStatus.failed
        job.error = "Could not enqueue job: broker unavailable"
        await session.commit()
    return job


async def bing_connections(session: AsyncSession) -> list[IntegrationConnection]:
    rows = await session.scalars(
        sa.select(IntegrationConnection).where(
            IntegrationConnection.provider == Provider.bing,
            IntegrationConnection.status == ConnectionStatus.connected,
        )
    )
    return list(rows)
