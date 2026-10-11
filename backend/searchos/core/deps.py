"""FastAPI dependencies: settings, current user, and the org-scoping context.

All tenant isolation flows through `OrgContext`. Routes never accept an org id from the request body or
query string and never build their own `WHERE org_id = ...`; they use `ctx.select(Model)` / `ctx.get_or_404`.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated

import sqlalchemy as sa
from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select

from searchos.core import security
from searchos.core.config import Settings, get_settings
from searchos.core.db import get_session
from searchos.models import Base, Membership, Role, User, role_at_least

_bearer = HTTPBearer(auto_error=False)

SettingsDep = Annotated[Settings, Depends(get_settings)]
SessionDep = Annotated[AsyncSession, Depends(get_session)]


def _unauthorized(detail: str = "Not authenticated") -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    session: SessionDep,
    settings: SettingsDep,
) -> User:
    if credentials is None:
        raise _unauthorized()
    try:
        user_id = security.decode_access_token(credentials.credentials, settings)
    except security.InvalidAccessTokenError:
        raise _unauthorized("Invalid or expired token") from None
    user = await session.get(User, user_id)
    if user is None or not user.is_active:
        raise _unauthorized()
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


@dataclass(slots=True)
class OrgContext:
    """The authenticated user acting inside exactly one organisation, with their role there."""

    user: User
    org_id: uuid.UUID
    role: Role
    session: AsyncSession

    def has_role(self, minimum: Role) -> bool:
        return role_at_least(self.role, minimum)

    def select[M: Base](self, model: type[M]) -> Select[M]:
        """`SELECT model` restricted to this organisation. The only sanctioned way to query org-owned rows."""
        return sa.select(model).where(model.__table__.c.org_id == self.org_id)

    async def get_or_404[M: Base](self, model: type[M], obj_id: uuid.UUID, what: str = "Resource") -> M:
        obj = await self.session.scalar(self.select(model).where(model.__table__.c.id == obj_id))
        if obj is None:
            # Same response whether the row does not exist or belongs to another org: no existence oracle.
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"{what} not found")
        return obj


async def get_org_context(
    user: CurrentUser,
    session: SessionDep,
    x_org_id: Annotated[uuid.UUID | None, Header()] = None,
) -> OrgContext:
    """Resolve the active organisation from the `X-Org-Id` header (default: the user's oldest membership)."""
    query = sa.select(Membership).where(Membership.user_id == user.id)
    if x_org_id is not None:
        query = query.where(Membership.org_id == x_org_id)
    else:
        query = query.order_by(Membership.created_at, Membership.id).limit(1)
    membership = await session.scalar(query)
    if membership is None:
        if x_org_id is not None:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "You are not a member of this organisation")
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You do not belong to any organisation")
    return OrgContext(user=user, org_id=membership.org_id, role=membership.role, session=session)


def require_role(minimum: Role) -> Callable[..., Awaitable[OrgContext]]:
    """Dependency factory: the caller's role in the active org must be at least `minimum`.

    Order: client_viewer < member < admin < owner.
    """

    async def dependency(ctx: Annotated[OrgContext, Depends(get_org_context)]) -> OrgContext:
        if not ctx.has_role(minimum):
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Requires role '{minimum.value}' or higher")
        return ctx

    return dependency
