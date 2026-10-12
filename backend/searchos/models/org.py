from __future__ import annotations

import enum
import uuid

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from searchos.models.base import Base, CreatedAtMixin, IdMixin, enum_check, str_enum


class Role(enum.StrEnum):
    """Ordered from most to least privileged; see ROLE_RANK."""

    owner = "owner"
    admin = "admin"
    member = "member"
    client_viewer = "client_viewer"


ROLE_RANK: dict[Role, int] = {
    Role.client_viewer: 0,
    Role.member: 1,
    Role.admin: 2,
    Role.owner: 3,
}


def role_at_least(role: Role, minimum: Role) -> bool:
    return ROLE_RANK[role] >= ROLE_RANK[minimum]


class Org(IdMixin, CreatedAtMixin, Base):
    __tablename__ = "orgs"

    name: Mapped[str] = mapped_column(sa.String(120), nullable=False)
    # Monthly AI token budget; NULL = the default (LLM_DEFAULT_MONTHLY_TOKEN_BUDGET, 2,000,000).
    llm_token_budget: Mapped[int | None] = mapped_column(sa.Integer)


class Membership(IdMixin, CreatedAtMixin, Base):
    __tablename__ = "memberships"
    __table_args__ = (
        sa.UniqueConstraint("org_id", "user_id", name="uq_memberships_org_id_user_id"),
        enum_check("role", Role, "role"),
    )

    org_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[Role] = mapped_column(str_enum(Role, "membership_role"), nullable=False)
