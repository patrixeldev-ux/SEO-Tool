"""Post-deploy verification: is the change in the raw HTML a crawler without JavaScript receives?

Each attempt re-fetches the page with a cache-busting query parameter and `Cache-Control: no-cache`. A cache may
still serve the old copy for a moment, so a miss is retried 3 times over about 2 minutes (a GitHub change, which
only appears after the owner merges and the site is rebuilt, gets up to 30 minutes after the merge). When the time
is up the deployment is marked failed with the value that was actually seen, and that change is rolled back
automatically: a change that cannot be seen by a crawler must not stay half-applied.
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
from searchos.models import Action, DeployBatch, Deployment, DeploymentStatus, DeployMethod, Site
from searchos.planner.catalog import ActionStatus


@dataclass(frozen=True, slots=True)
class Schedule:
    delays: tuple[int, ...]  # seconds to wait before attempt 2, 3, ...
    window: timedelta


# Attempts at 0, 15, 45 and 105 seconds: three retries inside two minutes.
DEFAULT_SCHEDULE = Schedule((15, 30, 60), timedelta(minutes=2))
# After the merge: 1, 3, 8, 18 and 33 minutes (site builds take a while).
GITHUB_SCHEDULE = Schedule((60, 120, 300, 600, 900), timedelta(minutes=40))
RETRY_DELAYS: tuple[int, ...] = DEFAULT_SCHEDULE.delays


def schedule_for(method: DeployMethod) -> Schedule:
    return GITHUB_SCHEDULE if method is DeployMethod.github else DEFAULT_SCHEDULE


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

    plan = schedule_for(dep.method)
    out_of_time = now - dep.verify_started_at >= plan.window - timedelta(seconds=1)
    out_of_tries = dep.verify_attempts > len(plan.delays)
    if out_of_time or out_of_tries:
        seen = f"The page could not be read ({problem})." if problem else f"The page shows: {observed!r}"
        dep.status = DeploymentStatus.failed
        dep.error = f"After {dep.verify_attempts} checks the new value is not in the page's HTML. {seen}"[
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
        await _auto_rollback(session, site, dep, fetcher, now)
        return VerifyOutcome("failed", observed=observed)

    delay = plan.delays[min(dep.verify_attempts - 1, len(plan.delays) - 1)]
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


async def _auto_rollback(
    session: AsyncSession, site: Site, dep: Deployment, fetcher: PageFetcher, now: datetime
) -> None:
    """Undo a change that verification could not find. The deployment keeps its error text; if the rollback
    itself fails, it stays `failed` and live so the person sees it and can use Roll back."""
    from searchos.deploy.rollback import rollback_deployments

    result = await rollback_deployments(
        session, site, [dep], user_id=None, reason="verification failed", fetcher=fetcher, now=now
    )
    if not result.reverted:
        reason = result.skipped[0].reason if result.skipped else "unknown reason"
        dep.error = f"{dep.error or ''} Automatic rollback failed: {reason}"[:900]
        log_event(
            session, site, "auto_rollback_failed", batch_id=dep.batch_id, deployment_id=dep.id, error=reason
        )
        await session.commit()


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
