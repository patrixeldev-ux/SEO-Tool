"""Post-deploy verification: is the change in the raw HTML a crawler without JavaScript receives?

Each attempt re-fetches the page with a cache-busting query parameter and `Cache-Control: no-cache`. A CDN may
still serve the old copy for a while, so a miss is retried with growing pauses for up to 10 minutes; only then is
the deployment marked failed, with the value that was actually seen.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from sqlalchemy.ext.asyncio import AsyncSession

from searchos.deploy import htmlview
from searchos.deploy.livefetch import PageFetcher, default_fetcher
from searchos.deploy.service import log_event, refresh_batch
from searchos.models import Action, DeployBatch, Deployment, DeploymentStatus, Site
from searchos.planner.catalog import ActionStatus

WINDOW = timedelta(minutes=10)
# Seconds to wait before attempt 2, 3, ...: 20+40+80+160+300 s, so the last attempt falls just inside 10 minutes.
RETRY_DELAYS: tuple[int, ...] = (20, 40, 80, 160, 300)


@dataclass(frozen=True, slots=True)
class VerifyOutcome:
    state: Literal["verified", "retry", "failed", "skipped"]
    next_delay: int | None = None
    observed: Any = None


async def _refresh(session: AsyncSession, dep: Deployment, now: datetime) -> None:
    batch = await session.get(DeployBatch, dep.batch_id)
    if batch is not None:
        await session.flush()
        await refresh_batch(session, batch, now)


async def verify_deployment(
    session: AsyncSession,
    dep: Deployment,
    *,
    fetcher: PageFetcher | None = None,
    now: datetime | None = None,
) -> VerifyOutcome:
    """One verification attempt. The caller (a Celery task or a test) schedules the next one when told to."""
    now = now or datetime.now(UTC)
    if dep.status is not DeploymentStatus.applied:
        return VerifyOutcome("skipped")
    site = await session.get(Site, dep.site_id)
    action = await session.get(Action, dep.action_id)
    if site is None:
        return VerifyOutcome("skipped")
    fetcher = fetcher or default_fetcher()
    if dep.verify_started_at is None:
        dep.verify_started_at = now
    dep.verify_attempts += 1
    proposed = dep.payload.get("proposed")

    resp = await fetcher.get(dep.url, no_cache=True)
    present = False
    observed: Any = None
    problem: str | None = None
    if resp.error or resp.status != 200:
        problem = resp.error or f"HTTP {resp.status}"
    else:
        present, observed = htmlview.proposed_present(
            dep.action_type, dep.field, proposed, resp.text, dep.url
        )

    if present:
        dep.status = DeploymentStatus.verified
        dep.verified_at = now
        dep.error = None
        dep.after_snapshot = {
            "value": observed,
            "http_status": resp.status,
            "fetched_at": now.isoformat(),
            "attempts": dep.verify_attempts,
        }
        log_event(
            session,
            site,
            "verified",
            batch_id=dep.batch_id,
            deployment_id=dep.id,
            attempts=dep.verify_attempts,
        )
        await _refresh(session, dep, now)
        await session.commit()
        return VerifyOutcome("verified", observed=observed)

    out_of_time = now - dep.verify_started_at >= WINDOW - timedelta(seconds=1)
    out_of_tries = dep.verify_attempts > len(RETRY_DELAYS)
    if out_of_time or out_of_tries:
        seen = f"The page could not be read ({problem})." if problem else f"The page shows: {observed!r}"
        dep.status = DeploymentStatus.failed
        dep.error = f"After {dep.verify_attempts} checks over 10 minutes the new value is not in the page's HTML. {seen}"[
            :600
        ]
        dep.after_snapshot = {
            "value": observed,
            "problem": problem,
            "fetched_at": now.isoformat(),
            "attempts": dep.verify_attempts,
        }
        if action is not None and action.status is ActionStatus.deployed:
            action.status = ActionStatus.approved  # it can be deployed again, or the change rolled back first
        log_event(
            session, site, "verify_failed", batch_id=dep.batch_id, deployment_id=dep.id, error=dep.error
        )
        await _refresh(session, dep, now)
        await session.commit()
        return VerifyOutcome("failed", observed=observed)

    delay = RETRY_DELAYS[min(dep.verify_attempts - 1, len(RETRY_DELAYS) - 1)]
    log_event(
        session,
        site,
        "verify_retry",
        batch_id=dep.batch_id,
        deployment_id=dep.id,
        attempt=dep.verify_attempts,
        next_in_seconds=delay,
    )
    await session.commit()
    return VerifyOutcome("retry", next_delay=delay, observed=observed)


async def verify_by_id(
    session: AsyncSession,
    deployment_id: uuid.UUID,
    *,
    fetcher: PageFetcher | None = None,
    now: datetime | None = None,
) -> VerifyOutcome:
    dep = await session.get(Deployment, deployment_id)
    if dep is None:
        return VerifyOutcome("skipped")
    return await verify_deployment(session, dep, fetcher=fetcher, now=now)
