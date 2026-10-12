"""Search performance data pulled from Google Search Console (and later Bing, AI reports, opportunities).

These tables carry only `site_id`; the API reaches them through a site that `OrgContext` has verified.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from searchos.models.base import Base


class GscRow(Base):
    """One Search Console row: a (day, page, query, country, device) with its clicks and impressions.

    Google drops anonymised queries from query-level rows, so these rows do NOT add up to the site's real
    totals. Totals come from `gsc_daily_totals`.
    """

    __tablename__ = "gsc_rows"

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True, autoincrement=True)
    site_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("sites.id", ondelete="CASCADE"), nullable=False
    )
    date: Mapped[date] = mapped_column(sa.Date, nullable=False)
    page: Mapped[str] = mapped_column(sa.Text, nullable=False)
    query: Mapped[str] = mapped_column(sa.Text, nullable=False)
    country: Mapped[str] = mapped_column(sa.String(8), nullable=False)
    device: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    clicks: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    impressions: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    ctr: Mapped[float] = mapped_column(sa.Float, nullable=False)
    position: Mapped[float] = mapped_column(sa.Float, nullable=False)
    # Heuristic: the query looks like an AI-Mode follow-up ("yes", "tell me more"), not a real search.
    is_conversational: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, default=False, server_default=sa.false()
    )
    ingested_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.clock_timestamp()
    )

    __table_args__ = (
        # The six dimensions are the natural key. md5() keeps the index entries small and bounded whatever the
        # length of the URL or query.
        sa.Index(
            "uq_gsc_rows_dims",
            "site_id",
            "date",
            sa.func.md5(sa.column("page")),
            sa.func.md5(sa.column("query")),
            "country",
            "device",
            unique=True,
        ),
        sa.Index("ix_gsc_rows_site_id_date", "site_id", "date"),
        # Page drill-down: WHERE site_id = ? AND md5(page) = md5(?) AND date BETWEEN ...
        sa.Index("ix_gsc_rows_site_id_page_date", "site_id", sa.func.md5(sa.column("page")), "date"),
    )


class GscDailyTotal(Base):
    """Site-wide clicks/impressions per day from a date-only query (includes anonymised queries)."""

    __tablename__ = "gsc_daily_totals"

    site_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("sites.id", ondelete="CASCADE"), primary_key=True
    )
    date: Mapped[date] = mapped_column(sa.Date, primary_key=True)
    clicks: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    impressions: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    ctr: Mapped[float] = mapped_column(sa.Float, nullable=False)
    # NULL on a day with no impressions.
    position: Mapped[float | None] = mapped_column(sa.Float)
    ingested_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.clock_timestamp()
    )


class Opportunity(Base):
    """A finding from the opportunity finders, recomputed after every sync (the previous set is replaced)."""

    __tablename__ = "opportunities"

    id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, primary_key=True, default=uuid.uuid4, server_default=sa.text("gen_random_uuid()")
    )
    site_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("sites.id", ondelete="CASCADE"), nullable=False
    )
    # STRIKING_DISTANCE | CTR_GAP | DECAY | CANNIBALISATION
    type: Mapped[str] = mapped_column(sa.String(24), nullable=False)
    page: Mapped[str | None] = mapped_column(sa.Text)
    query: Mapped[str | None] = mapped_column(sa.Text)
    evidence: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=sa.text("'{}'::jsonb")
    )
    score: Mapped[float] = mapped_column(sa.Float, nullable=False)
    window_start: Mapped[date] = mapped_column(sa.Date, nullable=False)
    window_end: Mapped[date] = mapped_column(sa.Date, nullable=False)
    computed_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.clock_timestamp()
    )

    __table_args__ = (sa.Index("ix_opportunities_site_id_type_score", "site_id", "type", "score"),)


class SearchInsights(Base):
    """What the last recompute used: window, the site's CTR curve, per-type counts and anything it skipped."""

    __tablename__ = "search_insights"

    site_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("sites.id", ondelete="CASCADE"), primary_key=True
    )
    computed_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.clock_timestamp()
    )
    window_start: Mapped[date] = mapped_column(sa.Date, nullable=False)
    window_end: Mapped[date] = mapped_column(sa.Date, nullable=False)
    curve: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=sa.text("'{}'::jsonb")
    )
    summary: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=sa.text("'{}'::jsonb")
    )


class AiReportImport(Base):
    """One uploaded CSV of a first-party AI report (Search Console Generative AI / Bing AI Performance)."""

    __tablename__ = "ai_report_imports"

    id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, primary_key=True, default=uuid.uuid4, server_default=sa.text("gen_random_uuid()")
    )
    site_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("sites.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source: Mapped[str] = mapped_column(sa.String(16), nullable=False)  # gsc_genai | bing_ai
    filename: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    # Which dimensions the file had: "date", "date+page", "page+query", ...
    shape: Mapped[str] = mapped_column(sa.String(40), nullable=False)
    row_count: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    date_min: Mapped[date | None] = mapped_column(sa.Date)
    date_max: Mapped[date | None] = mapped_column(sa.Date)
    columns: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=sa.text("'[]'::jsonb")
    )
    mapping: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=sa.text("'{}'::jsonb")
    )
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid, sa.ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.clock_timestamp()
    )

    __table_args__ = (sa.CheckConstraint("source IN ('gsc_genai', 'bing_ai')", name="source"),)


class AiReportRow(Base):
    """One metric value of an imported AI report: (date?, page?, query?, metric, value)."""

    __tablename__ = "ai_report_rows"

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True, autoincrement=True)
    site_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("sites.id", ondelete="CASCADE"), nullable=False
    )
    source: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    import_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("ai_report_imports.id", ondelete="CASCADE"), nullable=False, index=True
    )
    date: Mapped[date | None] = mapped_column(sa.Date)
    page: Mapped[str | None] = mapped_column(sa.Text)
    query: Mapped[str | None] = mapped_column(sa.Text)
    metric: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    value: Mapped[float] = mapped_column(sa.Float, nullable=False)

    __table_args__ = (sa.Index("ix_ai_report_rows_site_id_source_date", "site_id", "source", "date"),)
