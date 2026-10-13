"""Deploying approved actions, verifying them, and rolling them back.

The order of a deploy, and the reason for each check:
1. the site's ownership is proven (re-checked when older than 7 days);
2. each action is approved, deployable by this route (never-automated list again), and its page is on the site;
3. the live raw HTML is fetched NOW and compared with what the action was written for - if the page was edited
   since the audit the action becomes `stale` and is not deployed (we never overwrite someone's newer edit);
4. the live value is stored as the before snapshot, the deployer is called, and every step is written to
   `deployment_events`;
5. verification (deploy/verify.py) later confirms the change is in the raw HTML.
"""

from __future__ import annotations

import logging
import uuid
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from searchos.core.config import Settings, get_settings
from searchos.core.crypto import DecryptionError, decrypt_json
from searchos.deploy import htmlview, ownership
from searchos.deploy.base import DeployContext, Deployer, DeployerError, ItemResult
from searchos.deploy.livefetch import PageFetcher, default_fetcher
from searchos.deploy.ops import NotDeployableError, assert_deployable, ops_for_action
from searchos.deploy.registry import get_deployer
from searchos.models import (
    Action,
    BatchStatus,
    BatchTrigger,
    ConnectionStatus,
    DeployBatch,
    Deployment,
    DeploymentEvent,
    DeploymentStatus,
    DeployMethod,
    IntegrationConnection,
    Provider,
    Site,
)
from searchos.planner.catalog import ActionStatus

log = logging.getLogger(__name__)

DEPLOY_PROVIDERS: tuple[Provider, ...] = (Provider.cloudflare, Provider.wordpress, Provider.github)
MAX_BATCH = 50
# Statuses that mean "the change is on the site (or in an open pull request) right now".
ACTIVE = (DeploymentStatus.applied, DeploymentStatus.verified, DeploymentStatus.pending_merge)


class DeployError(Exception):
    """The request cannot be carried out at all (no deployer connected, too many actions, ...)."""

    def __init__(self, message: str, status_code: int = 409) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(slots=True)
class Skipped:
    action_id: uuid.UUID
    reason: str

    def as_dict(self) -> dict[str, str]:
        return {"action_id": str(self.action_id), "reason": self.reason}


@dataclass(slots=True)
class BatchResult:
    batch: DeployBatch | None
    deployments: list[Deployment] = field(default_factory=list)
    skipped: list[Skipped] = field(default_factory=list)


# ---- bookkeeping ---------------------------------------------------------------------------------------------
def log_event(
    session: AsyncSession,
    site: Site,
    event: str,
    *,
    batch_id: uuid.UUID | None = None,
    deployment_id: uuid.UUID | None = None,
    actor_id: uuid.UUID | None = None,
    **detail: Any,
) -> None:
    session.add(
        DeploymentEvent(
            org_id=site.org_id,
            site_id=site.id,
            batch_id=batch_id,
            deployment_id=deployment_id,
            actor_id=actor_id,
            event=event,
            detail=detail,
        )
    )


def batch_status(statuses: Iterable[DeploymentStatus]) -> BatchStatus:
    """One word for a batch, from its deployments."""
    s = list(statuses)
    if not s:
        return BatchStatus.pending
    if all(x is DeploymentStatus.reverted for x in s):
        return BatchStatus.reverted
    live = [x for x in s if x is not DeploymentStatus.reverted]
    if all(x is DeploymentStatus.failed for x in live):
        return BatchStatus.failed
    if any(x is DeploymentStatus.failed for x in live):
        return BatchStatus.partial
    if all(x is DeploymentStatus.verified for x in live):
        return BatchStatus.verified
    if any(x is DeploymentStatus.pending for x in live):
        return BatchStatus.pending
    return BatchStatus.applied


async def refresh_batch(session: AsyncSession, batch: DeployBatch, now: datetime | None = None) -> None:
    rows = (await session.scalars(sa.select(Deployment.status).where(Deployment.batch_id == batch.id))).all()
    batch.status = batch_status(rows)
    terminal = {BatchStatus.verified, BatchStatus.failed, BatchStatus.partial, BatchStatus.reverted}
    if batch.status in terminal and batch.finished_at is None:
        batch.finished_at = now or datetime.now(UTC)
    elif batch.status not in terminal:
        batch.finished_at = None


# ---- connections ---------------------------------------------------------------------------------------------
async def connections(session: AsyncSession, site_id: uuid.UUID) -> dict[DeployMethod, IntegrationConnection]:
    rows = (
        await session.scalars(
            sa.select(IntegrationConnection).where(
                IntegrationConnection.site_id == site_id,
                IntegrationConnection.provider.in_(DEPLOY_PROVIDERS),
                IntegrationConnection.status == ConnectionStatus.connected,
            )
        )
    ).all()
    return {DeployMethod(c.provider.value): c for c in rows}


async def chosen_method(session: AsyncSession, site: Site) -> tuple[DeployMethod, IntegrationConnection]:
    conns = await connections(session, site.id)
    if not conns:
        raise DeployError(
            "No deployer is connected for this site. Connect Cloudflare, WordPress or GitHub in Settings > Deploy."
        )
    if site.deploy_method:
        method = DeployMethod(site.deploy_method)
        if method not in conns:
            raise DeployError(f"The chosen deployer ({method.value}) is not connected any more.")
        return method, conns[method]
    for method in (DeployMethod.cloudflare, DeployMethod.wordpress, DeployMethod.github):
        if method in conns:
            return method, conns[method]
    raise DeployError("No deployer is connected for this site.")  # pragma: no cover


def credentials_of(conn: IntegrationConnection, settings: Settings | None = None) -> dict[str, Any]:
    if not conn.credentials_encrypted:
        raise DeployError("The deployer has no stored credentials; connect it again.")
    try:
        return decrypt_json(conn.credentials_encrypted, settings)
    except DecryptionError:
        raise DeployError("The stored credentials cannot be read; connect the deployer again.") from None


async def live_deployments(session: AsyncSession, site_id: uuid.UUID) -> dict[str, list[Deployment]]:
    rows = (
        await session.scalars(
            sa.select(Deployment)
            .where(
                Deployment.site_id == site_id,
                sa.or_(
                    Deployment.status.in_(ACTIVE),
                    # a deployment that failed verification but was applied is still live until rolled back
                    sa.and_(
                        Deployment.status == DeploymentStatus.failed,
                        Deployment.meta["live"].astext == "true",
                    ),
                ),
            )
            .order_by(Deployment.created_at, Deployment.id)
        )
    ).all()
    out: dict[str, list[Deployment]] = defaultdict(list)
    for d in rows:
        out[d.url].append(d)
    return dict(out)


async def make_context(
    session: AsyncSession,
    site: Site,
    conn: IntegrationConnection,
    fetcher: PageFetcher,
    *,
    batch_id: uuid.UUID | None = None,
    now: datetime | None = None,
    exclude: Sequence[Deployment] = (),
) -> DeployContext:
    settings = get_settings()
    live = await live_deployments(session, site.id)
    skip = {d.id for d in exclude}
    live = {u: [d for d in ds if d.id not in skip] for u, ds in live.items()}
    return DeployContext(
        site=site,
        connection=conn,
        credentials=credentials_of(conn, settings),
        settings=settings,
        fetcher=fetcher,
        live={u: ds for u, ds in live.items() if ds},
        batch_id=batch_id,
        version=int((now or datetime.now(UTC)).timestamp()),
    )


# ---- deploy ----------------------------------------------------------------------------------------------------
def on_site(site: Site, url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    return host in (site.domain, f"www.{site.domain}")


def clean_error(exc: BaseException) -> str:
    return str(exc)[:500] or type(exc).__name__


async def deploy_actions(
    session: AsyncSession,
    site: Site,
    action_ids: Sequence[uuid.UUID],
    *,
    user_id: uuid.UUID | None,
    trigger: BatchTrigger = BatchTrigger.manual,
    fetcher: PageFetcher | None = None,
    deployer: Deployer | None = None,
    now: datetime | None = None,
    checkers: ownership.Checkers | None = None,
) -> BatchResult:
    """Deploy approved actions of one site. Raises DeployError when nothing can be attempted; per-action
    problems end up in `BatchResult.skipped` (and as `stale` actions)."""
    now = now or datetime.now(UTC)
    unique_ids = list(dict.fromkeys(action_ids))
    if not unique_ids:
        raise DeployError("Choose at least one action to deploy.", 422)
    if len(unique_ids) > MAX_BATCH:
        raise DeployError(f"Deploy at most {MAX_BATCH} actions at a time.", 422)
    try:
        await ownership.require_verified(site, checkers or ownership.default_checkers(), now=now)
    except ownership.NotVerifiedError as exc:
        await session.commit()  # keep the re-check outcome on the site
        raise DeployError(str(exc)) from None
    method, conn = await chosen_method(session, site)
    deployer = deployer or get_deployer(method)
    fetcher = fetcher or default_fetcher()
    via_autopilot = trigger is BatchTrigger.autopilot

    actions = (
        await session.scalars(sa.select(Action).where(Action.site_id == site.id, Action.id.in_(unique_ids)))
    ).all()
    by_id = {a.id: a for a in actions}
    result = BatchResult(batch=None)
    batch = DeployBatch(
        org_id=site.org_id, site_id=site.id, created_by=user_id, method=method, trigger=trigger
    )
    session.add(batch)
    await session.flush()
    html_cache: dict[str, tuple[int | None, str, str | None]] = {}
    chosen: list[Deployment] = []
    seen_slots: set[tuple[str, str]] = set()

    for action_id in unique_ids:
        action = by_id.get(action_id)
        if action is None:
            result.skipped.append(Skipped(action_id, "Action not found on this site."))
            continue
        reason = await _prepare(
            session,
            site,
            action,
            batch,
            via_autopilot,
            fetcher,
            html_cache,
            seen_slots,
            chosen,
            now,
            getattr(deployer, "unsupported_ops", {}),
        )
        if reason:
            result.skipped.append(Skipped(action_id, reason))
            log_event(
                session,
                site,
                "skipped",
                batch_id=batch.id,
                actor_id=user_id,
                action_id=str(action_id),
                reason=reason,
            )

    batch.summary = {"requested": len(unique_ids), "skipped": [s.as_dict() for s in result.skipped]}
    result.batch = batch
    if not chosen:
        batch.status = BatchStatus.failed
        batch.finished_at = now
        log_event(
            session, site, "batch_empty", batch_id=batch.id, actor_id=user_id, skipped=len(result.skipped)
        )
        await session.commit()
        return result

    log_event(
        session,
        site,
        "batch_created",
        batch_id=batch.id,
        actor_id=user_id,
        method=method.value,
        trigger=trigger.value,
        deployments=len(chosen),
    )
    await session.flush()
    ctx = await make_context(session, site, conn, fetcher, batch_id=batch.id, now=now)
    try:
        outcomes = await deployer.apply(ctx, chosen)
    except DeployerError as exc:
        outcomes = {d.id: ItemResult(False, DeploymentStatus.failed, clean_error(exc)) for d in chosen}
    except Exception as exc:  # a deployer bug must not lose the batch record
        log.exception("deployer %s crashed", method.value)
        outcomes = {
            d.id: ItemResult(
                False, DeploymentStatus.failed, f"The deployer failed unexpectedly ({type(exc).__name__})."
            )
            for d in chosen
        }
    for dep in chosen:
        out = outcomes.get(dep.id) or ItemResult(
            False, DeploymentStatus.failed, "The deployer gave no answer."
        )
        action = by_id[dep.action_id]
        dep.meta = {**dep.meta, **out.meta}
        if out.ok:
            dep.status = out.status
            dep.deployed_at = now
            dep.meta = {**dep.meta, "live": True}
            action.status = ActionStatus.deployed
            log_event(
                session,
                site,
                "applied",
                batch_id=batch.id,
                deployment_id=dep.id,
                actor_id=user_id,
                status=out.status.value,
            )
        else:
            dep.status = DeploymentStatus.failed
            dep.error = out.error
            action.status = ActionStatus.approved
            log_event(
                session,
                site,
                "apply_failed",
                batch_id=batch.id,
                deployment_id=dep.id,
                actor_id=user_id,
                error=out.error,
            )
    await refresh_batch(session, batch, now)
    await session.commit()
    result.deployments = chosen
    return result


async def _prepare(
    session: AsyncSession,
    site: Site,
    action: Action,
    batch: DeployBatch,
    via_autopilot: bool,
    fetcher: PageFetcher,
    html_cache: dict[str, tuple[int | None, str, str | None]],
    seen_slots: set[tuple[str, str]],
    chosen: list[Deployment],
    now: datetime,
    unsupported_ops: Mapping[str, str] | None = None,
) -> str | None:
    """Everything that must hold before a deployment row is written. Returns the reason to skip, or None."""
    if action.status is not ActionStatus.approved:
        return f"The action is {action.status.value.replace('_', ' ')}, not approved."
    try:
        assert_deployable(action.type, action.risk_class, via_autopilot=via_autopilot)
    except NotDeployableError as exc:
        return str(exc)
    url = action.target_url
    if not url or not on_site(site, url):
        return "The page is not on this site."
    slot = (url, action.field)
    if slot in seen_slots:
        return "Another action in this batch changes the same thing on this page."
    try:
        ops = ops_for_action(action.type, action.field, action.proposed_value, action.id)
    except NotDeployableError as exc:
        return str(exc)
    for op in ops:
        if unsupported_ops and op["op"] in unsupported_ops:
            return unsupported_ops[op["op"]]

    if url not in html_cache:
        resp = await fetcher.get(url, no_cache=True)
        html_cache[url] = (resp.status, resp.text, resp.error)
    status, html, error = html_cache[url]
    if error or status != 200:
        return f"The live page could not be read right now ({error or f'HTTP {status}'})."

    already_there, _ = htmlview.proposed_present(action.type, action.field, action.proposed_value, html, url)
    stale = (
        None
        if already_there  # e.g. an earlier run applied it and only the check failed: re-applying is harmless
        else htmlview.is_stale(
            action.type, action.field, action.current_value, action.proposed_value, html, url
        )
    )
    if stale:
        action.status = ActionStatus.stale
        action.evidence = {**action.evidence, "stale_reason": stale, "stale_at": now.isoformat()}
        log_event(session, site, "stale", batch_id=batch.id, action_id=str(action.id), reason=stale)
        return stale

    observed = htmlview.observed_value(action.type, action.field, html, url)
    seen_slots.add(slot)
    dep = Deployment(
        org_id=site.org_id,
        site_id=site.id,
        action_id=action.id,
        batch_id=batch.id,
        method=batch.method,
        url=url,
        action_type=action.type,
        field=action.field,
        payload={"ops": ops, "proposed": action.proposed_value},
        before_snapshot={"value": observed, "http_status": status, "fetched_at": now.isoformat()},
        status=DeploymentStatus.pending,
    )
    session.add(dep)
    await session.flush()
    await _supersede_old(session, site, action, dep)
    chosen.append(dep)
    log_event(
        session, site, "deployment_created", batch_id=batch.id, deployment_id=dep.id, action_id=str(action.id)
    )
    return None


async def _supersede_old(session: AsyncSession, site: Site, action: Action, new: Deployment) -> None:
    """An earlier deployment of this same action that failed verification is replaced by the new one."""
    older = (
        await session.scalars(
            sa.select(Deployment).where(
                Deployment.action_id == action.id,
                Deployment.id != new.id,
                Deployment.status == DeploymentStatus.failed,
                Deployment.meta["live"].astext == "true",
            )
        )
    ).all()
    for old in older:
        old.status = DeploymentStatus.reverted
        old.reverted_at = datetime.now(UTC)
        old.meta = {**old.meta, "live": False, "superseded_by": str(new.id)}
        log_event(session, site, "superseded", deployment_id=old.id, by=str(new.id))
