"""Autopilot: while a site's autopilot is ON, deploy its low-risk (`auto`) changes by itself, slowly.

Limits (all enforced here, on top of the service's own gates):
* only a verified site with a connected deployer;
* only the `auto` risk class (`assert_deployable(..., via_autopilot=True)` is the second lock);
* at most `weekly_cap` changes per site in any 7 days (default 20, a per-site setting);
* never more than one change per URL in 28 days, so an experiment on a page stays readable;
* highest priority first.
Every run that deploys something writes one batch (`trigger=autopilot`) and one in-app notification.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from searchos.deploy import schedule, service
from searchos.deploy.base import Deployer
from searchos.deploy.livefetch import PageFetcher
from searchos.deploy.ops import DEPLOYABLE_TYPES, may_auto_deploy
from searchos.deploy.ownership import Checkers
from searchos.models import (
    Action,
    BatchTrigger,
    Deployment,
    DeploymentStatus,
    Notification,
    Site,
)
from searchos.models.deploy import LIVE_STATUSES
from searchos.planner.catalog import ActionStatus, RiskClass

log = logging.getLogger(__name__)

DEFAULT_WEEKLY_CAP = 20
MAX_WEEKLY_CAP = 200
URL_COOLDOWN = timedelta(days=28)
WEEK = timedelta(days=7)
CAP_KEY = "autopilot_weekly_cap"


def weekly_cap(site: Site) -> int:
    raw = (site.settings or {}).get(CAP_KEY)
    if isinstance(raw, bool) or not isinstance(raw, int):
        return DEFAULT_WEEKLY_CAP
    return max(0, min(raw, MAX_WEEKLY_CAP))


async def changes_in_last_week(session: AsyncSession, site: Site, now: datetime) -> int:
    return (
        await session.scalar(
            sa.select(sa.func.count())
            .select_from(Deployment)
            .where(
                Deployment.site_id == site.id,
                Deployment.status.in_(LIVE_STATUSES),
                Deployment.created_at >= now - WEEK,
            )
        )
        or 0
    )


@dataclass
class AutopilotResult:
    site_id: uuid.UUID
    deployed: int = 0
    reason: str = ""
    batch_id: uuid.UUID | None = None
    skipped: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "site_id": str(self.site_id),
            "deployed": self.deployed,
            "reason": self.reason,
            "batch_id": str(self.batch_id) if self.batch_id else None,
        }


async def _candidates(session: AsyncSession, site: Site, limit: int, now: datetime) -> list[Action]:
    """Best first, at most one per URL, none for a URL changed within 28 days."""
    recent_urls = set(
        (
            await session.scalars(
                sa.select(Deployment.url).where(
                    Deployment.site_id == site.id,
                    Deployment.status.in_(LIVE_STATUSES),
                    Deployment.created_at >= now - URL_COOLDOWN,
                )
            )
        ).all()
    )
    rows = (
        await session.scalars(
            sa.select(Action)
            .where(
                Action.site_id == site.id,
                Action.status.in_((ActionStatus.proposed, ActionStatus.approved)),
                Action.risk_class == RiskClass.auto,
                Action.type.in_(DEPLOYABLE_TYPES),
                Action.proposed_value.is_not(None),
                Action.target_url.is_not(None),
            )
            .order_by(Action.priority.desc(), Action.id)
        )
    ).all()
    picked: list[Action] = []
    urls: set[str] = set()
    for action in rows:
        url = action.target_url or ""
        if not may_auto_deploy(action.type) or url in recent_urls or url in urls:
            continue
        urls.add(url)
        picked.append(action)
        if len(picked) >= limit:
            break
    return picked


async def run_autopilot_for_site(
    session: AsyncSession,
    site: Site,
    *,
    now: datetime | None = None,
    fetcher: PageFetcher | None = None,
    deployer: Deployer | None = None,
    checkers: Checkers | None = None,
) -> AutopilotResult:
    now = now or datetime.now(UTC)
    result = AutopilotResult(site_id=site.id)
    if not site.autopilot_enabled:
        result.reason = "autopilot is off"
        return result
    if site.verified_at is None:
        result.reason = "site ownership is not verified"
        return result
    remaining = weekly_cap(site) - await changes_in_last_week(session, site, now)
    if remaining <= 0:
        result.reason = "weekly limit reached"
        return result
    picked = await _candidates(session, site, min(remaining, service.MAX_BATCH), now)
    if not picked:
        result.reason = "nothing to deploy"
        return result

    approved_here: set[uuid.UUID] = set()
    for action in picked:
        if action.status is ActionStatus.proposed:
            action.status = ActionStatus.approved
            action.decided_by = None
            action.decided_at = now
            action.updated_at = now
            action.evidence = {**action.evidence, "approved_by": "autopilot"}
            approved_here.add(action.id)
    await session.flush()
    try:
        batch_result = await service.deploy_actions(
            session,
            site,
            [a.id for a in picked],
            user_id=None,
            trigger=BatchTrigger.autopilot,
            fetcher=fetcher,
            deployer=deployer,
            now=now,
            checkers=checkers,
        )
    except service.DeployError as exc:
        # nothing was attempted: put back what autopilot approved itself
        await session.rollback()
        result.reason = str(exc)
        return result

    # actions autopilot approved but the service skipped go back to "proposed"; a person never saw them
    attempted = {d.action_id for d in batch_result.deployments}
    for action in picked:
        if (
            action.id in approved_here
            and action.id not in attempted
            and action.status is ActionStatus.approved
        ):
            action.status = ActionStatus.proposed
            action.decided_at = None
            action.evidence = {k: v for k, v in action.evidence.items() if k != "approved_by"}
    result.skipped = [s.reason for s in batch_result.skipped]
    batch = batch_result.batch
    if batch is not None and batch_result.deployments:
        result.deployed = len(batch_result.deployments)
        result.batch_id = batch.id
        await _notify(session, site, batch.id, picked, attempted)
    result.reason = "deployed" if result.deployed else "nothing could be deployed"
    await session.commit()
    schedule.enqueue_verifications(
        [d.id for d in batch_result.deployments if d.status is DeploymentStatus.applied]
    )
    return result


async def _notify(
    session: AsyncSession, site: Site, batch_id: uuid.UUID, picked: list[Action], attempted: set[uuid.UUID]
) -> None:
    done = [a for a in picked if a.id in attempted]
    n = len(done)
    lines = [f"- {a.type.replace('_', ' ')} on {a.target_url}" for a in done[:10]]
    if n > 10:
        lines.append(f"- and {n - 10} more")
    session.add(
        Notification(
            org_id=site.org_id,
            site_id=site.id,
            batch_id=batch_id,
            kind="autopilot_batch",
            title=f"Autopilot made {n} change{'s' if n != 1 else ''} on {site.display_name}",
            body="Each change is checked on the live page, and undone automatically if it does not show up. "
            "You can undo any of them from Deployments.\n" + "\n".join(lines),
        )
    )


async def run_autopilot_all(sessions: async_sessionmaker[AsyncSession]) -> dict[str, Any]:
    """Daily job: one pass over every site that has autopilot ON. A failing site never stops the others."""
    async with sessions() as session:
        site_ids = (
            await session.scalars(
                sa.select(Site.id).where(Site.autopilot_enabled.is_(True), Site.verified_at.is_not(None))
            )
        ).all()
    results: list[dict[str, Any]] = []
    for site_id in site_ids:
        try:
            async with sessions() as session:
                site = await session.get(Site, site_id)
                if site is None:
                    continue
                results.append((await run_autopilot_for_site(session, site)).as_dict())
        except Exception:
            log.exception("autopilot failed for site %s", site_id)
            results.append({"site_id": str(site_id), "deployed": 0, "reason": "error"})
    return {"sites": len(site_ids), "deployed": sum(r["deployed"] for r in results), "results": results}
