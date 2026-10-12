"""Deploying approved actions: ownership proof, connections to the three deployers, batches, rollback.

Every endpoint here needs the admin role (an owner counts) except the read-only history, which any member
may read. No endpoint that changes a site works until its ownership is verified; rollback is the one exception.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from searchos.core.deps import OrgContext, require_role
from searchos.deploy import ownership
from searchos.models import Role, Site

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


async def verified_site_or_409(ctx: OrgContext, site_id: uuid.UUID) -> Site:
    """The gate for every endpoint that changes a site: 409 with a plain sentence when not verified."""
    site = await site_or_404(ctx, site_id)
    try:
        await ownership.require_verified(site, ownership.default_checkers())
    except ownership.NotVerifiedError as exc:
        await ctx.session.commit()  # keep the re-check outcome
        raise HTTPException(409, str(exc)) from None
    await ctx.session.commit()
    return site
