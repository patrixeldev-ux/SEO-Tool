"""Job lifecycle helpers shared by all Celery tasks.

A task never talks to Celery's result backend. Instead it moves a `jobs` row through
queued -> running -> done | failed, and the API reads that row (GET /jobs/{id}).
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from searchos.core.config import get_settings
from searchos.core.db import make_engine
from searchos.models import Job, JobStatus

log = logging.getLogger(__name__)

MAX_ERROR_CHARS = 2000


@dataclass(slots=True)
class JobContext:
    job_id: uuid.UUID
    payload: dict[str, Any]
    _sessions: async_sessionmaker[AsyncSession]

    @property
    def sessions(self) -> async_sessionmaker[AsyncSession]:
        return self._sessions

    async def is_cancelled(self) -> bool:
        """True once someone (the API) has cancelled this job; handlers poll this to stop early."""
        async with self._sessions() as session:
            status = await session.scalar(sa.select(Job.status).where(Job.id == self.job_id))
        return status == JobStatus.cancelled

    async def set_progress(self, percent: int) -> None:
        """Persist progress immediately so API readers see it live."""
        value = max(0, min(100, int(percent)))
        async with self._sessions() as session:
            await session.execute(
                sa.update(Job)
                .where(Job.id == self.job_id, Job.status == JobStatus.running)
                .values(progress=value)
            )
            await session.commit()


JobHandler = Callable[[JobContext], Awaitable[dict[str, Any] | None]]


async def run_job(job_id: uuid.UUID, handler: JobHandler, *, engine: AsyncEngine | None = None) -> None:
    """Run `handler` with full job bookkeeping. Never raises for handler errors: the job row records them."""
    own_engine = engine is None
    engine = engine or make_engine(get_settings().database_url, null_pool=True)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as session:
            # Claim the job: only a queued job can start (guards against duplicate delivery).
            row = (
                await session.execute(
                    sa.update(Job)
                    .where(Job.id == job_id, Job.status == JobStatus.queued)
                    .values(status=JobStatus.running, started_at=datetime.now(UTC))
                    .returning(Job.payload)
                )
            ).first()
            await session.commit()
        if row is None:
            log.warning("job %s is not queued; skipping", job_id)
            return

        ctx = JobContext(job_id=job_id, payload=dict(row.payload), _sessions=sessions)
        try:
            result = await handler(ctx)
        except Exception as exc:
            log.exception("job %s failed", job_id)
            await _finish(sessions, job_id, JobStatus.failed, error=f"{type(exc).__name__}: {exc}")
        else:
            await _finish(sessions, job_id, JobStatus.done, result=result, progress=100)
    finally:
        if own_engine:
            await engine.dispose()


async def _finish(
    sessions: async_sessionmaker[AsyncSession],
    job_id: uuid.UUID,
    status: JobStatus,
    *,
    result: dict[str, Any] | None = None,
    error: str | None = None,
    progress: int | None = None,
) -> None:
    values: dict[str, Any] = {
        "status": status,
        "finished_at": datetime.now(UTC),
        "result": result,
        "error": error[:MAX_ERROR_CHARS] if error else None,
    }
    if progress is not None:
        values["progress"] = progress
    async with sessions() as session:
        # Only a running job can finish: a job cancelled meanwhile keeps its `cancelled` state.
        await session.execute(
            sa.update(Job).where(Job.id == job_id, Job.status == JobStatus.running).values(**values)
        )
        await session.commit()
