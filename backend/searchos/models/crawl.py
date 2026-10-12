"""Crawl results: one `crawls` row per audit run, with its pages, links, issues and score snapshot."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from searchos.models.base import Base, CreatedAtMixin, IdMixin, enum_check, str_enum


class CrawlStatus(enum.StrEnum):
    queued = "queued"
    running = "running"
    done = "done"
    failed = "failed"
    cancelled = "cancelled"


_EMPTY_LIST = sa.text("'[]'::jsonb")
_EMPTY_OBJ = sa.text("'{}'::jsonb")


class Crawl(IdMixin, CreatedAtMixin, Base):
    __tablename__ = "crawls"

    org_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    site_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("sites.id", ondelete="CASCADE"), nullable=False
    )
    job_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid, sa.ForeignKey("jobs.id", ondelete="SET NULL"))
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid, sa.ForeignKey("users.id", ondelete="SET NULL")
    )
    start_url: Mapped[str] = mapped_column(sa.Text, nullable=False)
    status: Mapped[CrawlStatus] = mapped_column(
        str_enum(CrawlStatus, "crawl_status"),
        nullable=False,
        default=CrawlStatus.queued,
        server_default=CrawlStatus.queued.value,
    )
    page_limit: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    pages_crawled: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0, server_default="0")
    urls_discovered: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0, server_default="0")
    started_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(sa.Text)
    # Human-readable notes shown with the audit ("page limit reached", "JS rendering check unavailable", ...).
    warnings: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=_EMPTY_LIST
    )
    # Site-wide facts the audit needs: robots.txt text/status, sitemap files, llms.txt, start scheme, ...
    site_info: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=_EMPTY_OBJ
    )

    __table_args__ = (
        sa.CheckConstraint("page_limit >= 1 AND page_limit <= 10000", name="page_limit_range"),
        enum_check("status", CrawlStatus, "status"),
        sa.Index("ix_crawls_site_id_created_at", "site_id", "created_at"),
    )


class Page(IdMixin, Base):
    __tablename__ = "pages"

    crawl_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("crawls.id", ondelete="CASCADE"), nullable=False
    )
    url: Mapped[str] = mapped_column(sa.Text, nullable=False)
    normalised_url: Mapped[str] = mapped_column(sa.Text, nullable=False)
    # This URL's own response; 3xx for a redirecting URL. NULL when nothing was fetched (see fetch_error).
    status_code: Mapped[int | None] = mapped_column(sa.Integer)
    final_url: Mapped[str | None] = mapped_column(sa.Text)
    redirect_chain: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=_EMPTY_LIST
    )
    # blocked_by_robots, redirect_loop, too_many_redirects, external_redirect, timeout, connect_error, ...
    fetch_error: Mapped[str | None] = mapped_column(sa.String(40))
    # Clicks from the start page along internal links (NULL = not reachable by links).
    depth: Mapped[int | None] = mapped_column(sa.Integer)
    content_type: Mapped[str | None] = mapped_column(sa.String(120))
    ttfb_ms: Mapped[int | None] = mapped_column(sa.Integer)
    title: Mapped[str | None] = mapped_column(sa.Text)
    meta_description: Mapped[str | None] = mapped_column(sa.Text)
    h1: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list, server_default=_EMPTY_LIST)
    canonical: Mapped[str | None] = mapped_column(sa.Text)
    robots_meta: Mapped[str | None] = mapped_column(sa.Text)
    x_robots_tag: Mapped[str | None] = mapped_column(sa.Text)
    lang: Mapped[str | None] = mapped_column(sa.String(40))
    has_viewport: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, default=False, server_default=sa.false()
    )
    hreflang: Mapped[list[dict[str, str]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=_EMPTY_LIST
    )
    word_count: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0, server_default="0")
    main_text_method: Mapped[str | None] = mapped_column(sa.String(20))
    # The page's main text (first 30,000 characters). The planner reads it to write suggestions and to find
    # related pages; NULL for crawls stored before Phase 3.
    main_text: Mapped[str | None] = mapped_column(sa.Text)
    structured_data: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=_EMPTY_OBJ
    )
    in_sitemap: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, default=False, server_default=sa.false()
    )
    inlinks_count: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0, server_default="0")
    # Everything else the checks read: h2/h3, images, http:// sub-resources, question headings, article
    # signals, rendered word count (JS check).
    extra: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=_EMPTY_OBJ
    )

    __table_args__ = (
        sa.UniqueConstraint("crawl_id", "normalised_url", name="uq_pages_crawl_id_normalised_url"),
        sa.Index("ix_pages_crawl_id_status_code", "crawl_id", "status_code"),
    )


class Link(Base):
    __tablename__ = "links"

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True, autoincrement=True)
    crawl_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("crawls.id", ondelete="CASCADE"), nullable=False
    )
    from_page_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("pages.id", ondelete="CASCADE"), nullable=False
    )
    to_url: Mapped[str] = mapped_column(sa.Text, nullable=False)
    to_page_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid, sa.ForeignKey("pages.id", ondelete="SET NULL")
    )
    anchor: Mapped[str] = mapped_column(sa.Text, nullable=False, default="", server_default="")
    rel: Mapped[str | None] = mapped_column(sa.String(120))
    is_internal: Mapped[bool] = mapped_column(sa.Boolean, nullable=False)

    __table_args__ = (
        sa.Index("ix_links_crawl_id_from_page_id", "crawl_id", "from_page_id"),
        sa.Index("ix_links_to_page_id", "to_page_id"),
        sa.Index("ix_links_crawl_id_to_url", "crawl_id", "to_url"),
    )


class Issue(IdMixin, Base):
    __tablename__ = "issues"

    crawl_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("crawls.id", ondelete="CASCADE"), nullable=False
    )
    check_id: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    # NULL for site-level issues (robots.txt, sitemap, ...).
    page_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid, sa.ForeignKey("pages.id", ondelete="CASCADE"))
    severity: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    category: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=_EMPTY_OBJ
    )

    __table_args__ = (
        sa.Index("ix_issues_crawl_id_check_id", "crawl_id", "check_id"),
        sa.Index("ix_issues_page_id", "page_id"),
    )


class AuditSnapshot(IdMixin, CreatedAtMixin, Base):
    __tablename__ = "audit_snapshots"

    crawl_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("crawls.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    site_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("sites.id", ondelete="CASCADE"), nullable=False
    )
    # NULL when nothing could be scored (no applicable weighted check).
    score: Mapped[float | None] = mapped_column(sa.Float)
    subscores: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=_EMPTY_OBJ
    )
    issue_counts: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=_EMPTY_OBJ
    )

    __table_args__ = (sa.Index("ix_audit_snapshots_site_id_created_at", "site_id", "created_at"),)
