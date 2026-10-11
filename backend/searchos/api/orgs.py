from __future__ import annotations

import sqlalchemy as sa
from fastapi import APIRouter, status

from searchos.api.schemas import OrgCreate, OrgOut
from searchos.core.deps import CurrentUser, SessionDep
from searchos.models import Membership, Org, Role

router = APIRouter(prefix="/orgs", tags=["orgs"])


@router.get("", response_model=list[OrgOut])
async def list_my_orgs(user: CurrentUser, session: SessionDep) -> list[OrgOut]:
    """Organisations the caller belongs to (feeds the org switcher)."""
    rows = await session.execute(
        sa.select(Org.id, Org.name, Membership.role)
        .join(Membership, Membership.org_id == Org.id)
        .where(Membership.user_id == user.id)
        .order_by(Membership.created_at, Membership.id)
    )
    return [OrgOut(id=r.id, name=r.name, role=r.role) for r in rows]


@router.post("", response_model=OrgOut, status_code=status.HTTP_201_CREATED)
async def create_org(body: OrgCreate, user: CurrentUser, session: SessionDep) -> OrgOut:
    """Create an additional organisation (e.g. a client workspace); the caller becomes its owner."""
    org = Org(name=body.name)
    session.add(org)
    await session.flush()
    session.add(Membership(org_id=org.id, user_id=user.id, role=Role.owner))
    await session.commit()
    return OrgOut(id=org.id, name=org.name, role=Role.owner)
