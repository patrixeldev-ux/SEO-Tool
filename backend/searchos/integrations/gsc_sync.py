"""Creating Search Console sync jobs (backfill and incremental) and handing them to Celery."""

from __future__ import annotations

import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from searchos.models import ConnectionStatus, IntegrationConnection, Job, JobStatus, Provider

JOB_TYPE = "gsc_sync"
ACTIVE = (JobStatus.queued, JobStatus.running)


async def active_sync_job(session: AsyncSession, site_id: uuid.UUID) -> Job | None:
    return await session.scalar(
        sa.select(Job)
        .where(Job.site_id == site_id, Job.type == JOB_TYPE, Job.status.in_(ACTIVE))
        .order_by(Job.created_at.desc())
        .limit(1)
    )


async def latest_sync_job(session: AsyncSession, site_id: uuid.UUID) -> Job | None:
    return await session.scalar(
        sa.select(Job)
        .where(Job.site_id == site_id, Job.type == JOB_TYPE)
        .order_by(Job.created_at.desc())
        .limit(1)
    )


async def create_sync_job(
    session: AsyncSession, conn: IntegrationConnection, mode: str, created_by: uuid.UUID | None = None
) -> Job:
    """Adds and commits the job row (the worker must see it before it is enqueued)."""
    if mode not in ("backfill", "incremental"):
        raise ValueError(f"unknown sync mode {mode!r}")
    payload: dict[str, Any] = {"connection_id": str(conn.id), "mode": mode}
    job = Job(org_id=conn.org_id, site_id=conn.site_id, created_by=created_by, type=JOB_TYPE, payload=payload)
    session.add(job)
    await session.flush()
    conn.meta = {**conn.meta, "last_sync_job_id": str(job.id), f"last_{mode}_job_id": str(job.id)}
    await session.commit()
    return job


async def enqueue_sync(session: AsyncSession, job: Job) -> bool:
    from searchos.worker.tasks import gsc_sync_task

    try:
        gsc_sync_task.delay(str(job.id))
    except Exception:
        job.status = JobStatus.failed
        job.error = "Could not enqueue job: broker unavailable"
        await session.commit()
        return False
    return True


async def start_sync(
    session: AsyncSession, conn: IntegrationConnection, mode: str, created_by: uuid.UUID | None = None
) -> Job | None:
    """Start a sync unless one is already queued or running for the site. Returns the job that is active."""
    existing = await active_sync_job(session, conn.site_id)
    if existing is not None:
        return existing
    if conn.status is not ConnectionStatus.connected or not conn.property_url:
        return None
    job = await create_sync_job(session, conn, mode, created_by)
    await enqueue_sync(session, job)
    return job


async def connections_to_sync(session: AsyncSession) -> list[IntegrationConnection]:
    rows = await session.scalars(
        sa.select(IntegrationConnection).where(
            IntegrationConnection.provider == Provider.google,
            IntegrationConnection.status == ConnectionStatus.connected,
            IntegrationConnection.property_url.is_not(None),
        )
    )
    return list(rows)
