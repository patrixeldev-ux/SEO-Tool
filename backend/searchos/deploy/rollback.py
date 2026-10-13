"""Rollback: restore the before snapshot of a deployment, or of a whole batch.

Always allowed, even when the site's ownership proof has lapsed: the way back must never depend on anything
but the deployer's own credentials. Cloudflare removes the page's rule, WordPress clears the override and puts
the old alt text back, GitHub closes the pull request (not merged yet) or opens a revert pull request (merged).
"""

from __future__ import annotations

import logging
import uuid
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from searchos.deploy import htmlview
from searchos.deploy.base import Deployer, DeployerError, ItemResult
from searchos.deploy.livefetch import PageFetcher, default_fetcher
from searchos.deploy.registry import get_deployer
from searchos.deploy.service import (
    DEPLOY_PROVIDERS,
    DeployError,
    Skipped,
    clean_error,
    log_event,
    make_context,
    refresh_batch,
)
from searchos.models import (
    Action,
    DeployBatch,
    Deployment,
    DeploymentStatus,
    DeployMethod,
    IntegrationConnection,
    Site,
)
from searchos.planner.catalog import ActionStatus

log = logging.getLogger(__name__)

ROLLBACKABLE = (
    DeploymentStatus.applied,
    DeploymentStatus.verified,
    DeploymentStatus.pending_merge,
    DeploymentStatus.failed,
)


@dataclass(slots=True)
class RollbackResult:
    reverted: list[Deployment] = field(default_factory=list)
    skipped: list[Skipped] = field(default_factory=list)


def can_rollback(dep: Deployment) -> bool:
    if dep.status is DeploymentStatus.failed:
        return bool(dep.meta.get("live"))
    return dep.status in ROLLBACKABLE


async def _all_connections(
    session: AsyncSession, site_id: uuid.UUID
) -> dict[DeployMethod, IntegrationConnection]:
    """Connections in any status: one that needs attention may still be usable to roll back."""
    rows = (
        await session.scalars(
            sa.select(IntegrationConnection).where(
                IntegrationConnection.site_id == site_id, IntegrationConnection.provider.in_(DEPLOY_PROVIDERS)
            )
        )
    ).all()
    return {DeployMethod(c.provider.value): c for c in rows}


async def rollback_deployments(
    session: AsyncSession,
    site: Site,
    deployments: Sequence[Deployment],
    *,
    user_id: uuid.UUID | None,
    reason: str = "manual",
    fetcher: PageFetcher | None = None,
    now: datetime | None = None,
) -> RollbackResult:
    now = now or datetime.now(UTC)
    fetcher = fetcher or default_fetcher()
    result = RollbackResult()
    todo: list[Deployment] = []
    for dep in deployments:
        if can_rollback(dep):
            todo.append(dep)
        else:
            result.skipped.append(
                Skipped(
                    dep.action_id, f"A {dep.status.value.replace('_', ' ')} deployment cannot be rolled back."
                )
            )
    if not todo:
        return result
    conns = await _all_connections(session, site.id)
    by_method: dict[DeployMethod, list[Deployment]] = defaultdict(list)
    for dep in todo:
        by_method[dep.method].append(dep)
    batches: dict[uuid.UUID, DeployBatch] = {}
    for method, items in by_method.items():
        for dep in items:
            log_event(
                session,
                site,
                "rollback_requested",
                batch_id=dep.batch_id,
                deployment_id=dep.id,
                actor_id=user_id,
                reason=reason,
            )
        conn = conns.get(method)
        if conn is None:
            for dep in items:
                result.skipped.append(
                    Skipped(
                        dep.action_id,
                        f"The {method.value} connection is gone; connect it again to roll back.",
                    )
                )
            continue
        deployer: Deployer = get_deployer(method)
        outcomes: dict[uuid.UUID, ItemResult]
        try:
            ctx = await make_context(session, site, conn, fetcher, now=now, exclude=items)
            outcomes = await deployer.rollback(ctx, items)
        except (DeployError, DeployerError) as exc:
            outcomes = {d.id: ItemResult(False, DeploymentStatus.failed, clean_error(exc)) for d in items}
        except Exception as exc:
            log.exception("rollback by %s crashed", method.value)
            outcomes = {
                d.id: ItemResult(
                    False,
                    DeploymentStatus.failed,
                    f"The rollback failed unexpectedly ({type(exc).__name__}).",
                )
                for d in items
            }
        for dep in items:
            out = outcomes.get(dep.id) or ItemResult(
                False, DeploymentStatus.failed, "The deployer gave no answer."
            )
            if not out.ok:
                result.skipped.append(Skipped(dep.action_id, out.error or "The rollback failed."))
                log_event(
                    session,
                    site,
                    "rollback_failed",
                    batch_id=dep.batch_id,
                    deployment_id=dep.id,
                    actor_id=user_id,
                    error=out.error,
                )
                continue
            dep.meta = {**dep.meta, **out.meta, "live": out.status is DeploymentStatus.reverting}
            dep.status = out.status
            if out.status is DeploymentStatus.reverted:
                dep.reverted_at = now
                action = await session.get(Action, dep.action_id)
                if action is not None and action.status is ActionStatus.deployed:
                    action.status = ActionStatus.rolled_back
            log_event(
                session,
                site,
                "reverted" if out.status is DeploymentStatus.reverted else "revert_opened",
                batch_id=dep.batch_id,
                deployment_id=dep.id,
                actor_id=user_id,
            )
            result.reverted.append(dep)
            if dep.batch_id not in batches:
                b = await session.get(DeployBatch, dep.batch_id)
                if b is not None:
                    batches[dep.batch_id] = b
    await session.flush()
    for dep in result.reverted:
        await _record_restored(dep, fetcher, now)
    for b in batches.values():
        await refresh_batch(session, b, now)
    await session.commit()
    return result


async def _record_restored(dep: Deployment, fetcher: PageFetcher, now: datetime) -> None:
    """One look at the live page after a rollback: is the original value back? (A CDN delay may say 'not yet'.)"""
    if dep.status is not DeploymentStatus.reverted:
        return
    try:
        resp = await fetcher.get(dep.url, no_cache=True)
    except Exception:
        return
    if resp.error or resp.status != 200:
        return
    observed = htmlview.observed_value(dep.action_type, dep.field, resp.text, dep.url)
    before = (dep.before_snapshot or {}).get("value")
    dep.after_snapshot = {
        **(dep.after_snapshot or {}),
        "after_rollback": observed,
        "restored": htmlview.same_value(observed, before),
        "checked_at": now.isoformat(),
    }


async def rollback_batch(session: AsyncSession, site: Site, batch: DeployBatch, **kw: Any) -> RollbackResult:
    deps = (await session.scalars(sa.select(Deployment).where(Deployment.batch_id == batch.id))).all()
    return await rollback_deployments(session, site, deps, **kw)
