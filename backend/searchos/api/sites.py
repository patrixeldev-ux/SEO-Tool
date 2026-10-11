from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.exc import IntegrityError

from searchos.api.schemas import SiteCreate, SiteOut, SiteUpdate
from searchos.core.deps import OrgContext, require_role
from searchos.core.domain import InvalidDomainError, normalize_domain
from searchos.models import Role, Site

router = APIRouter(prefix="/sites", tags=["sites"])

ReadCtx = Annotated[OrgContext, Depends(require_role(Role.client_viewer))]
WriteCtx = Annotated[OrgContext, Depends(require_role(Role.member))]
AdminCtx = Annotated[OrgContext, Depends(require_role(Role.admin))]


@router.get("", response_model=list[SiteOut])
async def list_sites(ctx: ReadCtx) -> list[Site]:
    result = await ctx.session.scalars(ctx.select(Site).order_by(Site.created_at, Site.id))
    return list(result)


@router.post("", response_model=SiteOut, status_code=status.HTTP_201_CREATED)
async def create_site(body: SiteCreate, ctx: WriteCtx) -> Site:
    try:
        normalized = normalize_domain(body.domain)
    except InvalidDomainError as exc:
        raise HTTPException(422, str(exc)) from None
    site = Site(
        org_id=ctx.org_id,
        domain=normalized.domain,
        uses_www=normalized.uses_www,
        display_name=body.display_name or normalized.domain,
    )
    ctx.session.add(site)
    try:
        await ctx.session.commit()
    except IntegrityError:
        await ctx.session.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT, "This site already exists in the organisation"
        ) from None
    return site


@router.get("/{site_id}", response_model=SiteOut)
async def get_site(site_id: uuid.UUID, ctx: ReadCtx) -> Site:
    return await ctx.get_or_404(Site, site_id, "Site")


@router.patch("/{site_id}", response_model=SiteOut)
async def update_site(site_id: uuid.UUID, body: SiteUpdate, ctx: WriteCtx) -> Site:
    site = await ctx.get_or_404(Site, site_id, "Site")
    changes = body.model_dump(exclude_unset=True)
    if "autopilot_enabled" in changes and not ctx.has_role(Role.admin):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only admins can change autopilot")
    for key in ("display_name", "settings", "autopilot_enabled"):
        if key in changes and changes[key] is not None:
            setattr(site, key, changes[key])
    await ctx.session.commit()
    return site


@router.delete("/{site_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_site(site_id: uuid.UUID, ctx: AdminCtx) -> Response:
    site = await ctx.get_or_404(Site, site_id, "Site")
    await ctx.session.delete(site)
    await ctx.session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
