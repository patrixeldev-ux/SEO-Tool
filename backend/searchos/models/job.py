from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from searchos.models.base import Base, CreatedAtMixin, IdMixin, enum_check, str_enum


class JobStatus(enum.StrEnum):
    queued = "queued"
    running = "running"
    done = "done"
    failed = "failed"
    cancelled = "cancelled"


class Job(IdMixin, CreatedAtMixin, Base):
    """Generic background-job record. Celery tasks update it; the API exposes it read-only."""

    __tablename__ = "jobs"

    org_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    site_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid, sa.ForeignKey("sites.id", ondelete="CASCADE"), index=True
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid, sa.ForeignKey("users.id", ondelete="SET NULL")
    )
    type: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    status: Mapped[JobStatus] = mapped_column(
        str_enum(JobStatus, "job_status"),
        nullable=False,
        default=JobStatus.queued,
        server_default=JobStatus.queued.value,
    )
    # 0-100
    progress: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0, server_default=sa.text("0"))
    error: Mapped[str | None] = mapped_column(sa.Text)
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=sa.text("'{}'::jsonb")
    )
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    started_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))

    __table_args__ = (
        sa.CheckConstraint("progress >= 0 AND progress <= 100", name="progress_range"),
        enum_check("status", JobStatus, "status"),
    )
