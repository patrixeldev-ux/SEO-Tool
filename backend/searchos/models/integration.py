"""Per-site connections to outside data providers (Google Search Console, Bing Webmaster, PageSpeed)."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from searchos.models.base import Base, CreatedAtMixin, IdMixin, enum_check, str_enum


class Provider(enum.StrEnum):
    google = "google"
    bing = "bing"
    psi = "psi"


class ConnectionStatus(enum.StrEnum):
    pending = "pending"  # authorised, but no property chosen yet
    connected = "connected"
    needs_reauth = "needs_reauth"  # Google said invalid_grant: the owner must connect again
    error = "error"


class IntegrationConnection(IdMixin, CreatedAtMixin, Base):
    __tablename__ = "integration_connections"
    __table_args__ = (
        sa.UniqueConstraint("site_id", "provider", name="uq_integration_connections_site_id_provider"),
        enum_check("provider", Provider, "provider"),
        enum_check("status", ConnectionStatus, "status"),
    )

    org_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    site_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("sites.id", ondelete="CASCADE"), nullable=False
    )
    provider: Mapped[Provider] = mapped_column(str_enum(Provider, "provider"), nullable=False)
    # Fernet token of a JSON object (OAuth tokens or an API key). Never sent to clients.
    credentials_encrypted: Mapped[str | None] = mapped_column(sa.Text)
    account_label: Mapped[str | None] = mapped_column(sa.String(255))
    # Google: "sc-domain:example.com" or "https://example.com/". Bing: the site URL registered in Bing.
    property_url: Mapped[str | None] = mapped_column(sa.String(500))
    status: Mapped[ConnectionStatus] = mapped_column(
        str_enum(ConnectionStatus, "connection_status"),
        nullable=False,
        default=ConnectionStatus.pending,
        server_default=ConnectionStatus.pending.value,
    )
    last_sync_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(sa.Text)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid, sa.ForeignKey("users.id", ondelete="SET NULL")
    )
    # Small provider-specific facts: backfill job id, first/last day ingested, ...
    meta: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=sa.text("'{}'::jsonb")
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        server_default=sa.func.clock_timestamp(),
        onupdate=sa.func.clock_timestamp(),
        nullable=False,
    )
