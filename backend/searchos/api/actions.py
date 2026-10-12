"""The action plan of a site: list it, summarise it, change the status of single actions or many at once."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from searchos.core.deps import OrgContext, require_role
from searchos.models import Action, Role, Site
from searchos.planner.catalog import ACTION_TYPES, ActionStatus, RiskClass

router = APIRouter(prefix="/sites/{site_id}/actions", tags=["actions"])

ReadCtx = Annotated[OrgContext, Depends(require_role(Role.client_viewer))]
MAX_LIMIT = 2000


class ActionOut(BaseModel):
    id: uuid.UUID
    site_id: uuid.UUID
    type: str
    type_label: str
    risk_class: RiskClass
    target_url: str | None
    field: str
    current_value: Any | None
    proposed_value: Any | None
    original_value: Any | None
    edited_by_user: bool
    evidence: dict[str, Any]
    expected_gain: float
    confidence: float
    effort: int
    priority: float
    status: ActionStatus
    created_by: str
    source_kind: str
    sources: list[str]
    generation: dict[str, Any]
    reject_reason: str | None
    decided_at: datetime | None
    created_at: datetime
    updated_at: datetime


def action_out(action: Action) -> ActionOut:
    info = ACTION_TYPES[action.type]
    return ActionOut(
        id=action.id,
        site_id=action.site_id,
        type=action.type,
        type_label=info.label,
        risk_class=action.risk_class,
        target_url=action.target_url,
        field=action.field,
        current_value=action.current_value,
        proposed_value=action.proposed_value,
        original_value=action.original_value,
        edited_by_user=action.edited_by_user,
        evidence=action.evidence,
        expected_gain=action.expected_gain,
        confidence=action.confidence,
        effort=action.effort,
        priority=action.priority,
        status=action.status,
        created_by=action.created_by,
        source_kind=action.source_kind,
        sources=list(action.sources),
        generation=action.generation,
        reject_reason=action.reject_reason,
        decided_at=action.decided_at,
        created_at=action.created_at,
        updated_at=action.updated_at,
    )


async def site_or_404(ctx: OrgContext, site_id: uuid.UUID) -> Site:
    return await ctx.get_or_404(Site, site_id, "Site")


@router.get("", response_model=list[ActionOut])
async def list_actions(
    site_id: uuid.UUID,
    ctx: ReadCtx,
    status: Annotated[list[ActionStatus] | None, Query()] = None,
    risk_class: Annotated[list[RiskClass] | None, Query()] = None,
    type: Annotated[list[str] | None, Query()] = None,
    manual: bool | None = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = 500,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[ActionOut]:
    """Actions of the site, highest priority first. Without a `status` filter, closed ones (obsolete) are left out."""
    await site_or_404(ctx, site_id)
    query = ctx.select(Action).where(Action.site_id == site_id)
    if status:
        query = query.where(Action.status.in_(status))
    else:
        query = query.where(Action.status != ActionStatus.obsolete)
    if risk_class:
        query = query.where(Action.risk_class.in_(risk_class))
    if type:
        unknown = [t for t in type if t not in ACTION_TYPES]
        if unknown:
            raise HTTPException(422, f"Unknown action type: {unknown[0]}")
        query = query.where(Action.type.in_(type))
    if manual is not None:
        is_manual = Action.risk_class == RiskClass.manual
        query = query.where(is_manual if manual else sa.not_(is_manual))
    query = query.order_by(Action.priority.desc(), Action.created_at, Action.id).limit(limit).offset(offset)
    rows = (await ctx.session.scalars(query)).all()
    return [action_out(a) for a in rows]
