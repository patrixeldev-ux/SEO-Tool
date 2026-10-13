"""Phase 4: what was changed on a site, by which method, and the way back."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from searchos.models.base import Base, CreatedAtMixin, IdMixin, enum_check, str_enum


class DeployMethod(enum.StrEnum):
    cloudflare = "cloudflare"
    wordpress = "wordpress"
    github = "github"


class DeploymentStatus(enum.StrEnum):
    pending = "pending"  # row written, nothing sent yet
    applied = "applied"  # the deployer accepted it; not yet seen in the page
    pending_merge = "pending_merge"  # GitHub: a pull request is open, nothing is live until it is merged
    verified = "verified"  # seen in the raw HTML of the live page
    failed = "failed"
    reverting = "reverting"  # GitHub: a revert pull request is open
    reverted = "reverted"


# Statuses in which the change is (or is about to be) on the site: these count towards autopilot limits.
LIVE_STATUSES: tuple[DeploymentStatus, ...] = (
    DeploymentStatus.applied,
    DeploymentStatus.pending_merge,
    DeploymentStatus.verified,
    DeploymentStatus.reverting,
)


class BatchTrigger(enum.StrEnum):
    manual = "manual"
    autopilot = "autopilot"


class BatchStatus(enum.StrEnum):
    pending = "pending"
    applied = "applied"
    verified = "verified"
    partial = "partial"  # some verified / applied, some failed
    failed = "failed"
    reverted = "reverted"


class DeployBatch(IdMixin, CreatedAtMixin, Base):
    __tablename__ = "deploy_batches"
    __table_args__ = (
        enum_check("method", DeployMethod, "method"),
        enum_check("trigger", BatchTrigger, "trigger"),
        enum_check("status", BatchStatus, "status"),
        sa.Index("ix_deploy_batches_site_id_created_at", "site_id", "created_at"),
    )

    org_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    site_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("sites.id", ondelete="CASCADE"), nullable=False
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid, sa.ForeignKey("users.id", ondelete="SET NULL")
    )
    method: Mapped[DeployMethod] = mapped_column(str_enum(DeployMethod, "method"), nullable=False)
    trigger: Mapped[BatchTrigger] = mapped_column(
        str_enum(BatchTrigger, "trigger"), nullable=False, default=BatchTrigger.manual
    )
    status: Mapped[BatchStatus] = mapped_column(
        str_enum(BatchStatus, "status"), nullable=False, default=BatchStatus.pending
    )
    finished_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    # What was asked for and what was skipped: {"requested": n, "skipped": [{"action_id", "reason"}]}
    summary: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=sa.text("'{}'::jsonb")
    )


class Deployment(IdMixin, CreatedAtMixin, Base):
    __tablename__ = "deployments"
    __table_args__ = (
        enum_check("method", DeployMethod, "method"),
        enum_check("status", DeploymentStatus, "status"),
        sa.Index("ix_deployments_site_id_created_at", "site_id", "created_at"),
        sa.Index("ix_deployments_batch_id", "batch_id"),
        sa.Index("ix_deployments_action_id", "action_id"),
    )

    org_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    site_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("sites.id", ondelete="CASCADE"), nullable=False
    )
    action_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("actions.id", ondelete="CASCADE"), nullable=False
    )
    batch_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("deploy_batches.id", ondelete="CASCADE"), nullable=False
    )
    method: Mapped[DeployMethod] = mapped_column(str_enum(DeployMethod, "method"), nullable=False)
    url: Mapped[str] = mapped_column(sa.Text, nullable=False)
    # Denormalised from the action so history survives re-planning: type, field, proposed value.
    action_type: Mapped[str] = mapped_column(sa.String(40), nullable=False)
    field: Mapped[str] = mapped_column(sa.String(300), nullable=False)
    # What the deployer was asked to do: {"ops": [...]} (see deploy/ops.py) and method-specific facts.
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=sa.text("'{}'::jsonb")
    )
    # The live raw-HTML value at deploy time, and what the page showed after the change / after the rollback.
    before_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    after_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    status: Mapped[DeploymentStatus] = mapped_column(
        str_enum(DeploymentStatus, "status"), nullable=False, default=DeploymentStatus.pending
    )
    error: Mapped[str | None] = mapped_column(sa.Text)
    # Method-specific bookkeeping: the pull request number and branch, WordPress post id, rule version, ...
    meta: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=sa.text("'{}'::jsonb")
    )
    verify_attempts: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0, server_default="0")
    verify_started_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    deployed_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    verified_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    reverted_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))


class DeploymentEvent(Base):
    """Audit log: who did what to which deployment, and what the deployer answered."""

    __tablename__ = "deployment_events"
    __table_args__ = (sa.Index("ix_deployment_events_site_id_created_at", "site_id", "created_at"),)

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True, autoincrement=True)
    org_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False
    )
    site_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("sites.id", ondelete="CASCADE"), nullable=False
    )
    batch_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid, sa.ForeignKey("deploy_batches.id", ondelete="CASCADE")
    )
    deployment_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid, sa.ForeignKey("deployments.id", ondelete="CASCADE")
    )
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid, sa.ForeignKey("users.id", ondelete="SET NULL")
    )  # NULL = the system (autopilot, verification)
    event: Mapped[str] = mapped_column(sa.String(40), nullable=False)
    detail: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=sa.text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.clock_timestamp()
    )


class Notification(IdMixin, CreatedAtMixin, Base):
    """An in-app message for the organisation's owners (e.g. "autopilot made 5 changes")."""

    __tablename__ = "notifications"
    __table_args__ = (sa.Index("ix_notifications_org_id_created_at", "org_id", "created_at"),)

    org_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False
    )
    site_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid, sa.ForeignKey("sites.id", ondelete="CASCADE"))
    batch_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid, sa.ForeignKey("deploy_batches.id", ondelete="CASCADE")
    )
    kind: Mapped[str] = mapped_column(sa.String(40), nullable=False)  # autopilot_batch
    title: Mapped[str] = mapped_column(sa.String(200), nullable=False)
    body: Mapped[str] = mapped_column(sa.Text, nullable=False, default="")
    read_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
