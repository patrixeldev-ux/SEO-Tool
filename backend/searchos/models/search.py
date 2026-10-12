"""Search performance data pulled from Google Search Console (and later Bing, AI reports, opportunities).

These tables carry only `site_id`; the API reaches them through a site that `OrgContext` has verified.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

import sqlalchemy as sa
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
