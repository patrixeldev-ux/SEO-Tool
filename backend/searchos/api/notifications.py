"""In-app notifications for the organisation (e.g. what autopilot did)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from searchos.core.deps import OrgContext, require_role
from searchos.models import Notification, Role

router = APIRouter(prefix="/notifications", tags=["notifications"])

ReadCtx = Annotated[OrgContext, Depends(require_role(Role.member))]
AdminCtx = Annotated[OrgContext, Depends(require_role(Role.admin))]


class NotificationOut(BaseModel):
    id: uuid.UUID
    site_id: uuid.UUID | None
    batch_id: uuid.UUID | None
    kind: str
    title: str
    body: str
    read: bool
    created_at: datetime


class NotificationsOut(BaseModel):
    unread: int
    items: list[NotificationOut]


def out(n: Notification) -> NotificationOut:
    return NotificationOut(
        id=n.id,
        site_id=n.site_id,
        batch_id=n.batch_id,
        kind=n.kind,
        title=n.title,
        body=n.body,
        read=n.read_at is not None,
        created_at=n.created_at,
    )


@router.get("", response_model=NotificationsOut)
async def list_notifications(
    ctx: ReadCtx, limit: Annotated[int, Query(ge=1, le=100)] = 30
) -> NotificationsOut:
    rows = (
        await ctx.session.scalars(
            ctx.select(Notification).order_by(Notification.created_at.desc()).limit(limit)
        )
    ).all()
    unread = await ctx.session.scalar(
        sa.select(sa.func.count())
        .select_from(Notification)
        .where(Notification.org_id == ctx.org_id, Notification.read_at.is_(None))
    )
    return NotificationsOut(unread=unread or 0, items=[out(n) for n in rows])


@router.post("/read-all", response_model=NotificationsOut)
async def read_all(ctx: ReadCtx) -> NotificationsOut:
    await ctx.session.execute(
        sa.update(Notification)
        .where(Notification.org_id == ctx.org_id, Notification.read_at.is_(None))
        .values(read_at=datetime.now(UTC))
    )
    await ctx.session.commit()
    return await list_notifications(ctx)


@router.post("/{notification_id}/read", response_model=NotificationOut)
async def read_one(notification_id: uuid.UUID, ctx: ReadCtx) -> NotificationOut:
    n = await ctx.get_or_404(Notification, notification_id, "Notification")
    if n.read_at is None:
        n.read_at = datetime.now(UTC)
        await ctx.session.commit()
    return out(n)
