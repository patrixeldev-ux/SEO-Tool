"""Deploying approved actions: ownership proof, connections to the three deployers, batches, rollback.

Every endpoint here needs the admin role (an owner counts) except the read-only history, which any member
may read. No endpoint that changes a site works until its ownership is verified; rollback is the one exception.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal, NoReturn

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from searchos.core.config import get_settings
from searchos.core.crypto import decrypt_json
from searchos.core.deps import OrgContext, require_role
from searchos.deploy import connections as conns
from searchos.deploy import ownership, rollback, schedule, service
from searchos.deploy.base import DeployerError
from searchos.deploy.cloudflare import EDGE_HEADER  # noqa: F401
from searchos.deploy.github import REPO_RE
from searchos.deploy.github import clean_path_rules as github_clean_rules
from searchos.deploy.github import clean_root as github_clean_root
from searchos.deploy.ops import DEPLOYABLE_TYPES
from searchos.deploy.signing import new_signing_secret, new_site_token
from searchos.deploy.wordpress import check_site_url
from searchos.models import (
    Action,
    BatchStatus,
    BatchTrigger,
    ConnectionStatus,
    DeployBatch,
    Deployment,
    DeploymentStatus,
    DeployMethod,
    IntegrationConnection,
    Role,
    Site,
)
from searchos.planner.catalog import ActionStatus

router = APIRouter(prefix="/sites/{site_id}/deploy", tags=["deploy"])

AdminCtx = Annotated[OrgContext, Depends(require_role(Role.admin))]
ReadCtx = Annotated[OrgContext, Depends(require_role(Role.member))]


# ---- ownership ---------------------------------------------------------------------------------------------
class OwnershipOut(BaseModel):
    verified: bool
    verified_at: datetime | None
    method: str | None
    checked_at: datetime | None
    error: str | None
    token: str
    dns_host: str
    dns_value: str
    file_path: str
    file_url: str
    file_content: str


class OwnershipCheckIn(BaseModel):
    method: Literal["dns", "file", "auto"] = "auto"


def ownership_out(site: Site) -> OwnershipOut:
    token = ownership.ensure_token(site)
    host = f"www.{site.domain}" if site.uses_www else site.domain
    return OwnershipOut(
        verified=ownership.is_verified(site),
        verified_at=site.verified_at,
        method=site.verify_method,
        checked_at=site.verify_checked_at,
        error=site.verify_error,
        token=token,
        dns_host=site.domain,
        dns_value=ownership.dns_value(token),
        file_path=ownership.file_path(token),
        file_url=f"https://{host}{ownership.file_path(token)}",
        file_content=token,
    )


async def site_or_404(ctx: OrgContext, site_id: uuid.UUID) -> Site:
    return await ctx.get_or_404(Site, site_id, "Site")


@router.get("/ownership", response_model=OwnershipOut)
async def get_ownership(site_id: uuid.UUID, ctx: AdminCtx) -> OwnershipOut:
    site = await site_or_404(ctx, site_id)
    out = ownership_out(site)
    await ctx.session.commit()  # the token is created on first view
    return out


@router.post("/ownership/check", response_model=OwnershipOut)
async def check_ownership_now(
    site_id: uuid.UUID, ctx: AdminCtx, body: OwnershipCheckIn | None = None
) -> OwnershipOut:
    site = await site_or_404(ctx, site_id)
    method = (body or OwnershipCheckIn()).method
    methods: tuple[ownership.Method, ...] = ("dns", "file") if method == "auto" else (method,)
    await ownership.run_check(site, ownership.default_checkers(), methods=methods)
    out = ownership_out(site)
    await ctx.session.commit()
    return out


# ---- deployments ---------------------------------------------------------------------------------------------
class DeploymentOut(BaseModel):
    id: uuid.UUID
    action_id: uuid.UUID
    batch_id: uuid.UUID
    method: DeployMethod
    url: str
    action_type: str
    field: str
    status: DeploymentStatus
    error: str | None
    ops: list[dict[str, Any]]
    before_snapshot: dict[str, Any] | None
    after_snapshot: dict[str, Any] | None
    pr_url: str | None
    can_rollback: bool
    verify_attempts: int
    created_at: datetime
    deployed_at: datetime | None
    verified_at: datetime | None
    reverted_at: datetime | None


class BatchOut(BaseModel):
    id: uuid.UUID
    method: DeployMethod
    trigger: BatchTrigger
    status: BatchStatus
    created_by: uuid.UUID | None
    created_at: datetime
    finished_at: datetime | None
    summary: dict[str, Any]
    counts: dict[str, int]


class SkippedOut(BaseModel):
    action_id: uuid.UUID
    reason: str


class DeployResultOut(BaseModel):
    batch: BatchOut | None
    deployments: list[DeploymentOut]
    skipped: list[SkippedOut]


class DeployIn(BaseModel):
    action_ids: list[uuid.UUID] = Field(default_factory=list, max_length=service.MAX_BATCH)
    # True = every approved, deployable action of the site (highest priority first, at most MAX_BATCH)
    all_approved: bool = False


def deployment_out(dep: Deployment) -> DeploymentOut:
    return DeploymentOut(
        id=dep.id,
        action_id=dep.action_id,
        batch_id=dep.batch_id,
        method=dep.method,
        url=dep.url,
        action_type=dep.action_type,
        field=dep.field,
        status=dep.status,
        error=dep.error,
        ops=list(dep.payload.get("ops", [])),
        before_snapshot=dep.before_snapshot,
        after_snapshot=dep.after_snapshot,
        pr_url=dep.meta.get("pr_url"),
        can_rollback=rollback.can_rollback(dep),
        verify_attempts=dep.verify_attempts,
        created_at=dep.created_at,
        deployed_at=dep.deployed_at,
        verified_at=dep.verified_at,
        reverted_at=dep.reverted_at,
    )


async def batch_out(session: AsyncSession, batch: DeployBatch) -> BatchOut:
    rows = (
        await session.execute(
            sa.select(Deployment.status, sa.func.count())
            .where(Deployment.batch_id == batch.id)
            .group_by(Deployment.status)
        )
    ).all()
    return BatchOut(
        id=batch.id,
        method=batch.method,
        trigger=batch.trigger,
        status=batch.status,
        created_by=batch.created_by,
        created_at=batch.created_at,
        finished_at=batch.finished_at,
        summary=batch.summary,
        counts={status.value: n for status, n in rows},
    )


def raise_for(exc: service.DeployError) -> NoReturn:
    raise HTTPException(exc.status_code, str(exc)) from None


@router.post("", response_model=DeployResultOut, status_code=201)
async def deploy_selected(site_id: uuid.UUID, body: DeployIn, ctx: AdminCtx) -> DeployResultOut:
    """Deploy the chosen approved actions now. Nothing happens unless the site's ownership is verified."""
    site = await site_or_404(ctx, site_id)
    ids = list(body.action_ids)
    if body.all_approved:
        ids += list(
            (
                await ctx.session.scalars(
                    sa.select(Action.id)
                    .where(
                        Action.site_id == site.id,
                        Action.status == ActionStatus.approved,
                        Action.type.in_(sorted(DEPLOYABLE_TYPES)),
                    )
                    .order_by(Action.priority.desc(), Action.id)
                    .limit(service.MAX_BATCH)
                )
            ).all()
        )
        ids = list(dict.fromkeys(ids))[: service.MAX_BATCH]
    try:
        result = await service.deploy_actions(ctx.session, site, ids, user_id=ctx.user.id)
    except service.DeployError as exc:
        raise_for(exc)
    schedule.enqueue_verifications([d.id for d in result.deployments if d.status is DeploymentStatus.applied])
    return DeployResultOut(
        batch=await batch_out(ctx.session, result.batch) if result.batch else None,
        deployments=[deployment_out(d) for d in result.deployments],
        skipped=[SkippedOut(action_id=s.action_id, reason=s.reason) for s in result.skipped],
    )


@router.get("/deployments", response_model=list[DeploymentOut])
async def list_deployments(
    site_id: uuid.UUID,
    ctx: ReadCtx,
    action_id: uuid.UUID | None = None,
    batch_id: uuid.UUID | None = None,
    status: Annotated[list[DeploymentStatus] | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[DeploymentOut]:
    await site_or_404(ctx, site_id)
    query = ctx.select(Deployment).where(Deployment.site_id == site_id)
    if action_id:
        query = query.where(Deployment.action_id == action_id)
    if batch_id:
        query = query.where(Deployment.batch_id == batch_id)
    if status:
        query = query.where(Deployment.status.in_(status))
    query = query.order_by(Deployment.created_at.desc(), Deployment.id).limit(limit).offset(offset)
    return [deployment_out(d) for d in (await ctx.session.scalars(query)).all()]


@router.get("/batches", response_model=list[BatchOut])
async def list_batches(
    site_id: uuid.UUID,
    ctx: ReadCtx,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[BatchOut]:
    await site_or_404(ctx, site_id)
    query = (
        ctx.select(DeployBatch)
        .where(DeployBatch.site_id == site_id)
        .order_by(DeployBatch.created_at.desc(), DeployBatch.id)
        .limit(limit)
        .offset(offset)
    )
    return [await batch_out(ctx.session, b) for b in (await ctx.session.scalars(query)).all()]


# ---- connections -----------------------------------------------------------------------------------------------
class ConnectionOut(BaseModel):
    method: DeployMethod
    status: ConnectionStatus
    label: str | None
    last_error: str | None
    last_tested_at: datetime | None
    meta: dict[str, Any]
    chosen: bool


class ConnectionsOut(BaseModel):
    deploy_method: DeployMethod | None
    connections: list[ConnectionOut]
    verified: bool


class TestOut(BaseModel):
    ok: bool
    message: str
    connection: ConnectionOut


def connection_out(conn: IntegrationConnection, site: Site) -> ConnectionOut:
    return ConnectionOut(
        method=DeployMethod(conn.provider.value),
        status=conn.status,
        label=conn.account_label,
        last_error=conn.last_error,
        last_tested_at=conn.last_sync_at,
        meta=conn.meta,
        chosen=site.deploy_method == conn.provider.value,
    )


@router.get("/connections", response_model=ConnectionsOut)
async def get_connections(site_id: uuid.UUID, ctx: ReadCtx) -> ConnectionsOut:
    site = await site_or_404(ctx, site_id)
    rows = await conns.list_connections(ctx.session, site.id)
    return ConnectionsOut(
        deploy_method=DeployMethod(site.deploy_method) if site.deploy_method else None,
        connections=[connection_out(c, site) for c in rows],
        verified=ownership.is_verified(site),
    )


class MethodIn(BaseModel):
    method: DeployMethod | None


@router.put("/method", response_model=ConnectionsOut)
async def choose_method(site_id: uuid.UUID, body: MethodIn, ctx: AdminCtx) -> ConnectionsOut:
    """Which connected deployer receives the changes. `null` = "none" (changes stay as hand-offs)."""
    site = await site_or_404(ctx, site_id)
    if body.method is not None:
        conn = await conns.get_connection(ctx.session, site.id, body.method)
        if conn is None or conn.status is not ConnectionStatus.connected:
            raise HTTPException(409, "Connect and test that deployer first.")
    site.deploy_method = body.method.value if body.method else None
    await ctx.session.commit()
    return await get_connections(site_id, ctx)


class CloudflareSetupOut(BaseModel):
    site_token: str
    signing_secret: str
    rules_url: str
    host: str
    route: str
    connection: ConnectionOut


def _cloudflare_setup(site: Site, conn: IntegrationConnection) -> CloudflareSetupOut:
    secret = str(decrypt_json(conn.credentials_encrypted or "")["signing_secret"])
    token = str(conn.meta["site_token"])
    return CloudflareSetupOut(
        site_token=token,
        signing_secret=secret,
        rules_url=f"{get_settings().public_api_url.rstrip('/')}/edge/rules/{token}",
        host=site.domain,
        route=f"{site.domain}/*",
        connection=connection_out(conn, site),
    )


class WordPressIn(BaseModel):
    site_url: str = Field(min_length=8, max_length=300)
    username: str = Field(min_length=1, max_length=120)
    application_password: str = Field(min_length=8, max_length=200)


@router.put("/connections/wordpress", response_model=TestOut)
async def connect_wordpress(site_id: uuid.UUID, body: WordPressIn, ctx: AdminCtx) -> TestOut:
    """Store the WordPress address, user and Application Password (encrypted) and test them right away."""
    site = await site_or_404(ctx, site_id)
    try:
        url = check_site_url(site, body.site_url, allow_private=get_settings().deploy_allow_private_hosts)
    except DeployerError as exc:
        raise HTTPException(422, str(exc)) from None
    conn = await conns.save_connection(
        ctx.session,
        site,
        DeployMethod.wordpress,
        user_id=ctx.user.id,
        credentials={
            "site_url": url,
            "username": body.username.strip(),
            "application_password": body.application_password.replace(" ", ""),
        },
        meta={"site_url": url, "username": body.username.strip()},
        label=f"WordPress ({body.username.strip()})",
    )
    result = await conns.run_test(ctx.session, site, conn)
    if (
        result.ok
        and site.deploy_method is None
        and len(await conns.list_connections(ctx.session, site.id)) == 1
    ):
        site.deploy_method = DeployMethod.wordpress.value
    await ctx.session.commit()
    return TestOut(ok=result.ok, message=result.message, connection=connection_out(conn, site))


class PathRuleIn(BaseModel):
    url: str = Field(max_length=200, description="Page address pattern, e.g. /blog/*")
    file: str = Field(max_length=300, description="File in the repository, e.g. blog/{1}.html")


class GithubIn(BaseModel):
    repo: str = Field(min_length=3, max_length=201, description="owner/name")
    token: str = Field(min_length=20, max_length=300)
    base_branch: str | None = Field(default=None, max_length=100)
    root: str = Field(default="", max_length=200, description="Folder with the built HTML files, e.g. public")
    path_rules: list[PathRuleIn] = Field(default_factory=list, max_length=50)


class GithubSettingsIn(BaseModel):
    base_branch: str | None = Field(default=None, max_length=100)
    root: str = Field(default="", max_length=200)
    path_rules: list[PathRuleIn] = Field(default_factory=list, max_length=50)


def _github_meta(repo: str, body: GithubSettingsIn | GithubIn) -> dict[str, Any]:
    try:
        return {
            "repo": repo,
            "base_branch": (body.base_branch or "").strip() or None,
            "root": github_clean_root(body.root),
            "path_rules": github_clean_rules([r.model_dump() for r in body.path_rules]),
        }
    except DeployerError as exc:
        raise HTTPException(422, str(exc)) from None


@router.put("/connections/github", response_model=TestOut)
async def connect_github(site_id: uuid.UUID, body: GithubIn, ctx: AdminCtx) -> TestOut:
    """Store the repository and a fine-grained token (encrypted) and test them right away."""
    site = await site_or_404(ctx, site_id)
    repo = body.repo.strip().removeprefix("https://github.com/").strip("/")
    if not REPO_RE.match(repo):
        raise HTTPException(422, "Enter the repository as owner/name, for example acme/website.")
    conn = await conns.save_connection(
        ctx.session,
        site,
        DeployMethod.github,
        user_id=ctx.user.id,
        credentials={"token": body.token.strip()},
        meta=_github_meta(repo, body),
        label=f"GitHub ({repo})",
    )
    result = await conns.run_test(ctx.session, site, conn)
    if (
        result.ok
        and site.deploy_method is None
        and len(await conns.list_connections(ctx.session, site.id)) == 1
    ):
        site.deploy_method = DeployMethod.github.value
    await ctx.session.commit()
    return TestOut(ok=result.ok, message=result.message, connection=connection_out(conn, site))


@router.put("/connections/github/settings", response_model=ConnectionOut)
async def github_settings(site_id: uuid.UUID, body: GithubSettingsIn, ctx: AdminCtx) -> ConnectionOut:
    """Change where pages live in the repository without entering the token again."""
    site = await site_or_404(ctx, site_id)
    conn = await conns.get_connection(ctx.session, site.id, DeployMethod.github)
    if conn is None:
        raise HTTPException(404, "GitHub is not set up for this site yet.")
    conn.meta = {**conn.meta, **_github_meta(str(conn.meta.get("repo", "")), body)}
    await ctx.session.commit()
    return connection_out(conn, site)


@router.put("/connections/cloudflare", response_model=CloudflareSetupOut)
async def connect_cloudflare(site_id: uuid.UUID, ctx: AdminCtx, rotate: bool = False) -> CloudflareSetupOut:
    """Create the Cloudflare connection (a site token and a signing secret for the Worker). Calling it again
    keeps the values unless `rotate=true` (then the Worker must be given the new ones)."""
    site = await site_or_404(ctx, site_id)
    conn = await conns.get_connection(ctx.session, site.id, DeployMethod.cloudflare)
    if conn is None or rotate or not conn.credentials_encrypted:
        old = dict(conn.meta) if conn else {}
        conn = await conns.save_connection(
            ctx.session,
            site,
            DeployMethod.cloudflare,
            user_id=ctx.user.id,
            credentials={"signing_secret": new_signing_secret()},
            meta={"site_token": new_site_token(), "kill_switch": bool(old.get("kill_switch"))},
            label="Cloudflare Worker",
        )
    await ctx.session.commit()
    return _cloudflare_setup(site, conn)


@router.get("/connections/cloudflare/setup", response_model=CloudflareSetupOut)
async def cloudflare_setup(site_id: uuid.UUID, ctx: AdminCtx) -> CloudflareSetupOut:
    site = await site_or_404(ctx, site_id)
    conn = await conns.get_connection(ctx.session, site.id, DeployMethod.cloudflare)
    if conn is None:
        raise HTTPException(404, "Cloudflare is not set up for this site yet.")
    return _cloudflare_setup(site, conn)


class KillSwitchIn(BaseModel):
    off: bool


@router.post("/connections/cloudflare/kill-switch", response_model=ConnectionOut)
async def cloudflare_kill_switch(site_id: uuid.UUID, body: KillSwitchIn, ctx: AdminCtx) -> ConnectionOut:
    """Stop the Worker applying anything (within about 5 minutes); every page passes through unchanged."""
    site = await site_or_404(ctx, site_id)
    conn = await conns.get_connection(ctx.session, site.id, DeployMethod.cloudflare)
    if conn is None:
        raise HTTPException(404, "Cloudflare is not set up for this site yet.")
    conn.meta = {**conn.meta, "kill_switch": body.off}
    service.log_event(ctx.session, site, "kill_switch", actor_id=ctx.user.id, off=body.off)
    await ctx.session.commit()
    return connection_out(conn, site)


@router.post("/connections/{method}/test", response_model=TestOut)
async def test_connection(site_id: uuid.UUID, method: DeployMethod, ctx: AdminCtx) -> TestOut:
    site = await site_or_404(ctx, site_id)
    conn = await conns.get_connection(ctx.session, site.id, method)
    if conn is None:
        raise HTTPException(404, "That deployer is not set up for this site yet.")
    result = await conns.run_test(ctx.session, site, conn)
    if (
        result.ok
        and site.deploy_method is None
        and len(await conns.list_connections(ctx.session, site.id)) == 1
    ):
        site.deploy_method = method.value
    await ctx.session.commit()
    return TestOut(ok=result.ok, message=result.message, connection=connection_out(conn, site))


@router.delete("/connections/{method}", status_code=204)
async def disconnect(site_id: uuid.UUID, method: DeployMethod, ctx: AdminCtx) -> None:
    site = await site_or_404(ctx, site_id)
    conn = await conns.get_connection(ctx.session, site.id, method)
    if conn is None:
        raise HTTPException(404, "That deployer is not set up for this site.")
    live = await service.live_deployments(ctx.session, site.id)
    if any(d.method is method for ds in live.values() for d in ds):
        raise HTTPException(409, "Roll back this deployer's live changes before disconnecting it.")
    await conns.remove_connection(ctx.session, site, conn)
    await ctx.session.commit()


# ---- rollback -------------------------------------------------------------------------------------------------
class RollbackIn(BaseModel):
    deployment_ids: list[uuid.UUID] = Field(min_length=1, max_length=200)


class RollbackOut(BaseModel):
    reverted: list[DeploymentOut]
    skipped: list[SkippedOut]


def _rollback_out(result: rollback.RollbackResult) -> RollbackOut:
    return RollbackOut(
        reverted=[deployment_out(d) for d in result.reverted],
        skipped=[SkippedOut(action_id=s.action_id, reason=s.reason) for s in result.skipped],
    )


@router.post("/rollback", response_model=RollbackOut)
async def rollback_selected(site_id: uuid.UUID, body: RollbackIn, ctx: AdminCtx) -> RollbackOut:
    """Undo deployments. Allowed even if the ownership proof has lapsed. Idempotent: already reverted ones are
    simply reported as skipped."""
    site = await site_or_404(ctx, site_id)
    deps = (
        await ctx.session.scalars(
            sa.select(Deployment).where(Deployment.site_id == site.id, Deployment.id.in_(body.deployment_ids))
        )
    ).all()
    result = await rollback.rollback_deployments(ctx.session, site, deps, user_id=ctx.user.id)
    return _rollback_out(result)


@router.post("/batches/{batch_id}/rollback", response_model=RollbackOut)
async def rollback_whole_batch(site_id: uuid.UUID, batch_id: uuid.UUID, ctx: AdminCtx) -> RollbackOut:
    site = await site_or_404(ctx, site_id)
    batch = await ctx.session.scalar(
        sa.select(DeployBatch).where(DeployBatch.id == batch_id, DeployBatch.site_id == site.id)
    )
    if batch is None:
        raise HTTPException(404, "Batch not found")
    result = await rollback.rollback_batch(ctx.session, site, batch, user_id=ctx.user.id)
    return _rollback_out(result)


@router.get("/batches/{batch_id}", response_model=DeployResultOut)
async def get_batch(site_id: uuid.UUID, batch_id: uuid.UUID, ctx: ReadCtx) -> DeployResultOut:
    site = await site_or_404(ctx, site_id)
    batch = await ctx.session.scalar(
        sa.select(DeployBatch).where(DeployBatch.id == batch_id, DeployBatch.site_id == site.id)
    )
    if batch is None:
        raise HTTPException(404, "Batch not found")
    deps = (
        await ctx.session.scalars(
            sa.select(Deployment)
            .where(Deployment.batch_id == batch.id)
            .order_by(Deployment.created_at, Deployment.id)
        )
    ).all()
    skipped = [
        SkippedOut(action_id=uuid.UUID(s["action_id"]), reason=s["reason"])
        for s in batch.summary.get("skipped", [])
    ]
    return DeployResultOut(
        batch=await batch_out(ctx.session, batch),
        deployments=[deployment_out(d) for d in deps],
        skipped=skipped,
    )
