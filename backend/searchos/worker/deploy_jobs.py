"""Celery-side work for Phase 4: verify a deployment, sweep for missed ones, poll pull requests, run autopilot."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from searchos.core.config import get_settings
from searchos.core.db import make_engine
from searchos.deploy.verify import VerifyOutcome, verify_by_id

log = logging.getLogger(__name__)


async def with_sessions[T](fn: Callable[[async_sessionmaker[AsyncSession]], Awaitable[T]]) -> T:
    """Run `fn` with a session factory bound to a throw-away engine (each Celery task has its own event loop)."""
    engine = make_engine(get_settings().database_url, null_pool=True)
    try:
        return await fn(async_sessionmaker(engine, expire_on_commit=False))
    finally:
        await engine.dispose()


async def run_verify(deployment_id: uuid.UUID) -> VerifyOutcome:
    async def go(sessions: async_sessionmaker[AsyncSession]) -> VerifyOutcome:
        async with sessions() as session:
            return await verify_by_id(session, deployment_id)

    return await with_sessions(go)


async def sweep_unverified() -> dict[str, Any]:
    """Queue verification for deployments that are `applied` but have no check running (a lost task, a restart)
    and look at open pull requests."""
    from searchos.deploy.github import poll_pull_requests
    from searchos.deploy.schedule import enqueue_verifications
    from searchos.models import Deployment, DeploymentStatus

    async def go(sessions: async_sessionmaker[AsyncSession]) -> dict[str, Any]:
        async with sessions() as session:
            ids = (
                await session.scalars(
                    sa.select(Deployment.id).where(
                        Deployment.status == DeploymentStatus.applied,
                        Deployment.created_at < sa.func.now() - sa.text("interval '15 minutes'"),
                    )
                )
            ).all()
        merged = await poll_pull_requests(sessions)
        enqueue_verifications(list(ids) + merged)
        return {"requeued": len(ids), "merged": len(merged)}

    return await with_sessions(go)
