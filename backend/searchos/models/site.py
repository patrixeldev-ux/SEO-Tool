from __future__ import annotations

import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from searchos.models.base import Base, CreatedAtMixin, IdMixin


class Site(IdMixin, CreatedAtMixin, Base):
    __tablename__ = "sites"
    __table_args__ = (sa.UniqueConstraint("org_id", "domain", name="uq_sites_org_id_domain"),)

    org_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Normalised: lowercase, no scheme, no leading "www.", IDNA/punycode (ASCII).
    domain: Mapped[str] = mapped_column(sa.String(253), nullable=False)
    # The "www." prefix is kept separately so we can still fetch the canonical host the owner uses.
    uses_www: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, default=False, server_default=sa.false()
    )
    display_name: Mapped[str] = mapped_column(sa.String(120), nullable=False)
    settings: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=sa.text("'{}'::jsonb")
    )
    autopilot_enabled: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, default=False, server_default=sa.false()
    )
