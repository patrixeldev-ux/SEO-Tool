"""The action plan: one row per thing the planner wants done, and the log of AI token usage that pays for it."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from searchos.models.base import Base, CreatedAtMixin, IdMixin, enum_check, str_enum
from searchos.planner.catalog import OPEN_STATUSES, ActionStatus, RiskClass

_EMPTY_OBJ = sa.text("'{}'::jsonb")
_OPEN_SQL = ", ".join(f"'{s.value}'" for s in OPEN_STATUSES)


class Action(IdMixin, CreatedAtMixin, Base):
    __tablename__ = "actions"

    org_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    site_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("sites.id", ondelete="CASCADE"), nullable=False
    )
    # See searchos.planner.catalog.ACTION_TYPES.
    type: Mapped[str] = mapped_column(sa.String(40), nullable=False)
    # Copied from the catalog when the action is created; Phase 4 reads this column.
    risk_class: Mapped[RiskClass] = mapped_column(str_enum(RiskClass, "risk_class"), nullable=False)
    # NULL for site-wide actions (robots.txt review, link earning, ...).
    target_url: Mapped[str | None] = mapped_column(sa.Text)
    # What exactly is changed on the page: "title", "meta_description", "alt:<image src>", "jsonld:Organization", ...
    field: Mapped[str] = mapped_column(sa.String(300), nullable=False)
    current_value: Mapped[Any | None] = mapped_column(JSONB)
    # NULL while no value exists (status needs_ai / no_suggestion, or a brief-only action).
    proposed_value: Mapped[Any | None] = mapped_column(JSONB)
    # The first AI/system value, kept when a person edits proposed_value.
    original_value: Mapped[Any | None] = mapped_column(JSONB)
    edited_by_user: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, default=False, server_default=sa.false()
    )
    evidence: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=_EMPTY_OBJ
    )
    # Estimated extra clicks per 28 days. A prior (not a measurement) unless evidence["gain_basis"] says gsc.
    expected_gain: Mapped[float] = mapped_column(sa.Float, nullable=False, default=0.0, server_default="0")
    confidence: Mapped[float] = mapped_column(sa.Float, nullable=False, default=0.0, server_default="0")
    effort: Mapped[int] = mapped_column(sa.SmallInteger, nullable=False)
    priority: Mapped[float] = mapped_column(sa.Float, nullable=False, default=0.0, server_default="0")
    status: Mapped[ActionStatus] = mapped_column(
        str_enum(ActionStatus, "action_status"),
        nullable=False,
        default=ActionStatus.proposed,
        server_default=ActionStatus.proposed.value,
    )
    created_by: Mapped[str] = mapped_column(
        sa.String(8), nullable=False, default="system", server_default="system"
    )
    # audit | gsc | site: which input produced it (decides when the planner may close it as obsolete).
    source_kind: Mapped[str] = mapped_column(
        sa.String(8), nullable=False, default="audit", server_default="audit"
    )
    # The audit check ids / opportunity types behind it.
    sources: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=sa.text("'[]'::jsonb")
    )
    # How the value was produced: {"state": "ok|deferred|dropped", "attempts": n, "reason": "...", "provider": ..}
    generation: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=_EMPTY_OBJ
    )
    # Manual tasks: ids of the checklist items a person has ticked off (see planner/manual_tasks.py).
    checklist_done: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=sa.text("'[]'::jsonb")
    )
    decided_by: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid, sa.ForeignKey("users.id", ondelete="SET NULL")
    )
    decided_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    reject_reason: Mapped[str | None] = mapped_column(sa.Text)
    # priority when the action was rejected: a rejected finding returns only if its priority rises by 50 %.
    priority_at_decision: Mapped[float | None] = mapped_column(sa.Float)
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.clock_timestamp()
    )

    __table_args__ = (
        enum_check("status", ActionStatus, "status"),
        enum_check("risk_class", RiskClass, "risk_class"),
        sa.CheckConstraint("effort >= 1 AND effort <= 5", name="effort_range"),
        sa.CheckConstraint("created_by IN ('system', 'user')", name="created_by"),
        # One open action per (site, type, target, field): re-planning updates it instead of adding another.
        sa.Index(
            "uq_actions_open_key",
            "site_id",
            "type",
            sa.func.coalesce(sa.column("target_url"), ""),
            "field",
            unique=True,
            postgresql_where=sa.text(f"status IN ({_OPEN_SQL})"),
        ),
        sa.Index("ix_actions_site_id_status_priority", "site_id", "status", "priority"),
    )


class LlmUsage(Base):
    """Tokens spent on AI calls; the monthly per-organisation budget is enforced from this table."""

    __tablename__ = "llm_usage"

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True, autoincrement=True)
    org_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False
    )
    site_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid, sa.ForeignKey("sites.id", ondelete="SET NULL"))
    provider: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    model: Mapped[str] = mapped_column(sa.String(80), nullable=False)
    purpose: Mapped[str] = mapped_column(sa.String(40), nullable=False)
    input_tokens: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0, server_default="0")
    output_tokens: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0, server_default="0")
    ok: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=True, server_default=sa.true())
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.clock_timestamp()
    )

    __table_args__ = (sa.Index("ix_llm_usage_org_id_created_at", "org_id", "created_at"),)
