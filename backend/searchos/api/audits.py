"""Audit endpoints: start a crawl + audit for a site, follow it, read the results.

Crawl, page, link and issue rows are only ever reached through a crawl that `OrgContext` has verified
belongs to the caller's organisation (the child tables carry no org id of their own).
"""

from __future__ import annotations

import csv
import io
import json
import uuid
from datetime import datetime
from typing import Annotated, Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field

from searchos.audit.catalog import CheckInfo, load_catalog
from searchos.audit.diff import IssueKey, diff_issues
from searchos.audit.schema_rules import types_of
from searchos.core.deps import OrgContext, require_role
from searchos.models import (
    AuditSnapshot,
    Crawl,
    CrawlStatus,
    Issue,
    Job,
    JobStatus,
    Link,
    Page,
    PsiResult,
    Role,
    Site,
)
from searchos.worker.launch import (
    DEFAULT_PAGE_LIMIT,
    MAX_PAGE_LIMIT,
    active_crawl,
    create_crawl,
    enqueue_crawl,
)

router = APIRouter(tags=["audits"])

ReadCtx = Annotated[OrgContext, Depends(require_role(Role.client_viewer))]
WriteCtx = Annotated[OrgContext, Depends(require_role(Role.member))]

URLS_PER_GROUP = 200  # affected URLs listed per check; the CSV export has all of them
# Checks that need data from outside the crawl; with none, they are listed as "not run" instead of passed.
NOT_RUN_REASONS = {
    "CWV_LCP_POOR": "No PageSpeed Insights data for this audit (needs a public site; see Settings > Integrations).",
    "CWV_INP_POOR": "No real-user (field) Interaction to Next Paint data for any tested page.",
    "CWV_CLS_POOR": "No PageSpeed Insights data for this audit (needs a public site; see Settings > Integrations).",
}
SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}


# ---- schemas ----------------------------------------------------------------------------------------
class AuditStart(BaseModel):
    model_config = ConfigDict(extra="forbid")  # no start URL, host or anything else from the caller

    page_limit: Annotated[int, Field(ge=1, le=MAX_PAGE_LIMIT)] = DEFAULT_PAGE_LIMIT


class AuditOut(BaseModel):
    id: uuid.UUID
    site_id: uuid.UUID
    job_id: uuid.UUID | None
    status: CrawlStatus
    progress: int
    start_url: str
    page_limit: int
    pages_crawled: int
    urls_discovered: int
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    error: str | None
    warnings: list[str]
    score: float | None
    subscores: dict[str, float | None]
    issue_counts: dict[str, Any]
    stop_reason: str | None


class IssueUrl(BaseModel):
    page_id: uuid.UUID | None
    url: str | None  # None = a site-wide issue
    details: dict[str, Any]


class IssueGroup(BaseModel):
    check_id: str
    title: str
    severity: str
    category: str
    why_it_matters: str
    how_to_fix: str
    count: int
    items: list[IssueUrl]
    truncated: bool


class PassedCheck(BaseModel):
    check_id: str
    title: str
    severity: str
    category: str
    applicable: int


class NotRunCheck(BaseModel):
    check_id: str
    title: str
    severity: str
    category: str
    reason: str


class IssuesOut(BaseModel):
    groups: list[IssueGroup]
    passed: list[PassedCheck]
    not_run: list[NotRunCheck] = []


class PageRow(BaseModel):
    id: uuid.UUID
    url: str
    status_code: int | None
    fetch_error: str | None
    depth: int | None
    title: str | None
    word_count: int
    inlinks_count: int
    issue_count: int


class PagesOut(BaseModel):
    total: int
    items: list[PageRow]


class LinkOut(BaseModel):
    url: str
    anchor: str
    rel: str | None
    page_id: uuid.UUID | None = None


class PageIssue(BaseModel):
    check_id: str
    title: str
    severity: str
    why_it_matters: str
    how_to_fix: str
    details: dict[str, Any]


class PsiOut(BaseModel):
    """Core Web Vitals from PageSpeed Insights. `*_source` says field (real users) or lab (one simulated load)."""

    lcp_ms: float | None
    lcp_source: str | None
    inp_ms: float | None
    inp_source: str | None
    cls: float | None
    cls_source: str | None
    performance_score: float | None
    error: str | None
    fetched_at: datetime


class PageDetail(BaseModel):
    id: uuid.UUID
    url: str
    final_url: str | None
    status_code: int | None
    fetch_error: str | None
    redirect_chain: list[dict[str, Any]]
    depth: int | None
    content_type: str | None
    ttfb_ms: int | None
    title: str | None
    meta_description: str | None
    h1: list[str]
    canonical: str | None
    robots_meta: str | None
    x_robots_tag: str | None
    lang: str | None
    word_count: int
    main_text_method: str | None
    in_sitemap: bool
    inlinks_count: int
    schema_types: list[str]
    psi: PsiOut | None = None
    issues: list[PageIssue]
    inlinks: list[LinkOut]
    outlinks: list[LinkOut]
    outlinks_total: int


class ChangeItem(BaseModel):
    check_id: str
    title: str
    severity: str
    url: str | None


class ChangesOut(BaseModel):
    previous_audit_id: uuid.UUID | None
    previous_score: float | None
    score: float | None
    score_delta: float | None
    new: list[ChangeItem]
    fixed: list[ChangeItem]
    persisting_count: int
    not_rechecked_count: int


# ---- helpers ----------------------------------------------------------------------------------------
async def _audit_out(ctx: OrgContext, crawl: Crawl) -> AuditOut:
    progress = 0
    if crawl.job_id is not None:
        progress = await ctx.session.scalar(sa.select(Job.progress).where(Job.id == crawl.job_id)) or 0
    if crawl.status is CrawlStatus.done:
        progress = 100
    snap = await ctx.session.scalar(sa.select(AuditSnapshot).where(AuditSnapshot.crawl_id == crawl.id))
    return _build_out(crawl, progress, snap)


def _build_out(crawl: Crawl, progress: int, snap: AuditSnapshot | None) -> AuditOut:
    counts = dict(snap.issue_counts) if snap else {}
    counts.pop("checks", None)  # per-check detail is served by /issues
    return AuditOut(
        id=crawl.id,
        site_id=crawl.site_id,
        job_id=crawl.job_id,
        status=crawl.status,
        progress=progress,
        start_url=crawl.start_url,
        page_limit=crawl.page_limit,
        pages_crawled=crawl.pages_crawled,
        urls_discovered=crawl.urls_discovered,
        created_at=crawl.created_at,
        started_at=crawl.started_at,
        finished_at=crawl.finished_at,
        error=crawl.error,
        warnings=list(crawl.warnings or []),
        score=snap.score if snap else None,
        subscores=dict(snap.subscores) if snap else {},
        issue_counts=counts,
        stop_reason=(crawl.site_info or {}).get("stop_reason"),
    )


def _info(check_id: str) -> CheckInfo | None:
    return load_catalog().get(check_id)


def _page_issue(issue: Issue) -> PageIssue:
    info = _info(issue.check_id)
    return PageIssue(
        check_id=issue.check_id,
        title=info.title if info else issue.check_id,
        severity=issue.severity,
        why_it_matters=info.why_it_matters if info else "",
        how_to_fix=info.how_to_fix if info else "",
        details=issue.details,
    )


async def _issue_keys(ctx: OrgContext, crawl_id: uuid.UUID) -> list[tuple[str, str | None, str]]:
    """(check_id, page url or None, severity) for every issue of a crawl."""
    result = await ctx.session.execute(
        sa.select(Issue.check_id, Page.normalised_url, Issue.severity)
        .select_from(Issue)
        .outerjoin(Page, Page.id == Issue.page_id)
        .where(Issue.crawl_id == crawl_id)
    )
    return [(c, u, s) for c, u, s in result.all()]


def _csv_safe(value: object) -> str:
    """Cells starting with = + - @ would run as formulas in Excel; page URLs and titles come from the web."""
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


# ---- endpoints on a site ----------------------------------------------------------------------------
@router.post("/sites/{site_id}/audits", response_model=AuditOut, status_code=status.HTTP_202_ACCEPTED)
async def start_audit(site_id: uuid.UUID, ctx: WriteCtx, body: AuditStart | None = None) -> AuditOut:
    site = await ctx.get_or_404(Site, site_id, "Site")
    if await active_crawl(ctx.session, site.id) is not None:
        await ctx.session.commit()
        raise HTTPException(status.HTTP_409_CONFLICT, "An audit is already running for this site")
    crawl, job = await create_crawl(
        ctx.session,
        site,
        created_by=ctx.user.id,
        page_limit=(body or AuditStart()).page_limit,
    )
    if not await enqueue_crawl(ctx.session, crawl, job):
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Job queue unavailable")
    return await _audit_out(ctx, crawl)


@router.get("/sites/{site_id}/audits", response_model=list[AuditOut])
async def list_audits(
    site_id: uuid.UUID, ctx: ReadCtx, limit: Annotated[int, Query(ge=1, le=100)] = 20
) -> list[AuditOut]:
    site = await ctx.get_or_404(Site, site_id, "Site")
    crawls = (
        await ctx.session.scalars(
            ctx.select(Crawl)
            .where(Crawl.site_id == site.id)
            .order_by(Crawl.created_at.desc(), Crawl.id)
            .limit(limit)
        )
    ).all()
    return [await _audit_out(ctx, c) for c in crawls]


# ---- endpoints on one audit -------------------------------------------------------------------------
@router.get("/audits/{audit_id}", response_model=AuditOut)
async def get_audit(audit_id: uuid.UUID, ctx: ReadCtx) -> AuditOut:
    return await _audit_out(ctx, await ctx.get_or_404(Crawl, audit_id, "Audit"))


@router.post("/audits/{audit_id}/cancel", response_model=AuditOut)
async def cancel_audit(audit_id: uuid.UUID, ctx: WriteCtx) -> AuditOut:
    crawl = await ctx.get_or_404(Crawl, audit_id, "Audit")
    if crawl.status not in (CrawlStatus.queued, CrawlStatus.running):
        raise HTTPException(status.HTTP_409_CONFLICT, "This audit has already finished")
    if crawl.job_id is not None:
        await ctx.session.execute(
            sa.update(Job)
            .where(Job.id == crawl.job_id, Job.status.in_([JobStatus.queued, JobStatus.running]))
            .values(status=JobStatus.cancelled)
        )
    if crawl.status is CrawlStatus.queued:
        crawl.status = CrawlStatus.cancelled  # a running crawl marks itself when its worker notices
    await ctx.session.commit()
    await ctx.session.refresh(crawl)
    return await _audit_out(ctx, crawl)


@router.get("/audits/{audit_id}/issues", response_model=IssuesOut)
async def audit_issues(audit_id: uuid.UUID, ctx: ReadCtx) -> IssuesOut:
    crawl = await ctx.get_or_404(Crawl, audit_id, "Audit")
    catalog = load_catalog()
    counts = dict(
        (
            await ctx.session.execute(
                sa.select(Issue.check_id, sa.func.count())
                .where(Issue.crawl_id == crawl.id)
                .group_by(Issue.check_id)
            )
        ).all()
    )
    groups: list[IssueGroup] = []
    for check_id, count in counts.items():
        info = catalog.get(check_id)
        rows = (
            await ctx.session.execute(
                sa.select(Issue.page_id, Page.normalised_url, Issue.details)
                .select_from(Issue)
                .outerjoin(Page, Page.id == Issue.page_id)
                .where(Issue.crawl_id == crawl.id, Issue.check_id == check_id)
                .order_by(Page.normalised_url.nulls_first(), Issue.id)
                .limit(URLS_PER_GROUP)
            )
        ).all()
        groups.append(
            IssueGroup(
                check_id=check_id,
                title=info.title if info else check_id,
                severity=info.severity if info else "info",
                category=info.category if info else "indexability",
                why_it_matters=info.why_it_matters if info else "",
                how_to_fix=info.how_to_fix if info else "",
                count=count,
                items=[IssueUrl(page_id=p, url=u, details=d) for p, u, d in rows],
                truncated=count > len(rows),
            )
        )
    groups.sort(key=lambda g: (SEVERITY_ORDER.get(g.severity, 9), -g.count, g.check_id))

    snap = await ctx.session.scalar(sa.select(AuditSnapshot).where(AuditSnapshot.crawl_id == crawl.id))
    passed: list[PassedCheck] = []
    not_run: list[NotRunCheck] = []
    if snap:
        for check_id, stats in (snap.issue_counts.get("checks") or {}).items():
            info = catalog.get(check_id)
            if info and stats.get("applicable", 0) == 0 and check_id in NOT_RUN_REASONS:
                not_run.append(
                    NotRunCheck(
                        check_id=check_id,
                        title=info.title,
                        severity=info.severity,
                        category=info.category,
                        reason=NOT_RUN_REASONS[check_id],
                    )
                )
            if info and stats.get("failing", 0) == 0 and stats.get("applicable", 0) > 0:
                passed.append(
                    PassedCheck(
                        check_id=check_id,
                        title=info.title,
                        severity=info.severity,
                        category=info.category,
                        applicable=stats["applicable"],
                    )
                )
        passed.sort(key=lambda p: (SEVERITY_ORDER.get(p.severity, 9), p.check_id))
    not_run.sort(key=lambda n: n.check_id)
    return IssuesOut(groups=groups, passed=passed, not_run=not_run)


@router.get("/audits/{audit_id}/pages", response_model=PagesOut)
async def audit_pages(
    audit_id: uuid.UUID,
    ctx: ReadCtx,
    q: Annotated[str | None, Query(max_length=200)] = None,
    with_issues: bool = False,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> PagesOut:
    crawl = await ctx.get_or_404(Crawl, audit_id, "Audit")
    issue_counts = (
        sa.select(Issue.page_id.label("page_id"), sa.func.count().label("n"))
        .where(Issue.crawl_id == crawl.id, Issue.page_id.is_not(None))
        .group_by(Issue.page_id)
        .subquery()
    )
    issue_n = sa.func.coalesce(issue_counts.c.n, 0)
    base = (
        sa.select(Page, issue_n.label("issue_count"))
        .outerjoin(issue_counts, issue_counts.c.page_id == Page.id)
        .where(Page.crawl_id == crawl.id)
    )
    if q:
        escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        base = base.where(Page.normalised_url.ilike(f"%{escaped}%", escape="\\"))
    if with_issues:
        base = base.where(issue_n > 0)
    total = await ctx.session.scalar(sa.select(sa.func.count()).select_from(base.subquery())) or 0
    rows = (await ctx.session.execute(base.order_by(Page.normalised_url).limit(limit).offset(offset))).all()
    return PagesOut(
        total=total,
        items=[
            PageRow(
                id=p.id,
                url=p.normalised_url,
                status_code=p.status_code,
                fetch_error=p.fetch_error,
                depth=p.depth,
                title=p.title,
                word_count=p.word_count,
                inlinks_count=p.inlinks_count,
                issue_count=n,
            )
            for p, n in rows
        ],
    )


@router.get("/audits/{audit_id}/pages/{page_id}", response_model=PageDetail)
async def audit_page(audit_id: uuid.UUID, page_id: uuid.UUID, ctx: ReadCtx) -> PageDetail:
    crawl = await ctx.get_or_404(Crawl, audit_id, "Audit")
    page = await ctx.session.scalar(sa.select(Page).where(Page.id == page_id, Page.crawl_id == crawl.id))
    if page is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Page not found")
    issues = (
        await ctx.session.scalars(sa.select(Issue).where(Issue.page_id == page.id).order_by(Issue.check_id))
    ).all()
    inlinks = (
        await ctx.session.execute(
            sa.select(Page.normalised_url, sa.func.min(Link.anchor), sa.func.min(Link.rel), Page.id)
            .select_from(Link)
            .join(Page, Page.id == Link.from_page_id)
            .where(Link.to_page_id == page.id, Link.from_page_id != page.id)
            .group_by(Page.normalised_url, Page.id)
            .order_by(Page.normalised_url)
            .limit(50)
        )
    ).all()
    outlinks_total = (
        await ctx.session.scalar(
            sa.select(sa.func.count()).select_from(Link).where(Link.from_page_id == page.id)
        )
        or 0
    )
    outlinks = (
        await ctx.session.execute(
            sa.select(Link.to_url, Link.anchor, Link.rel, Link.to_page_id)
            .where(Link.from_page_id == page.id)
            .order_by(Link.id)
            .limit(100)
        )
    ).all()
    psi_row = await ctx.session.scalar(
        sa.select(PsiResult).where(PsiResult.crawl_id == crawl.id, PsiResult.url == page.normalised_url)
    )
    sd = page.structured_data or {}
    types = sorted(
        {
            t
            for e in [*(sd.get("jsonld") or []), *(sd.get("microdata") or [])]
            if isinstance(e, dict)
            for t in types_of(e)
        }
    )
    return PageDetail(
        id=page.id,
        url=page.normalised_url,
        final_url=page.final_url,
        status_code=page.status_code,
        fetch_error=page.fetch_error,
        redirect_chain=list(page.redirect_chain or []),
        depth=page.depth,
        content_type=page.content_type,
        ttfb_ms=page.ttfb_ms,
        title=page.title,
        meta_description=page.meta_description,
        h1=list(page.h1 or []),
        canonical=page.canonical,
        robots_meta=page.robots_meta,
        x_robots_tag=page.x_robots_tag,
        lang=page.lang,
        word_count=page.word_count,
        main_text_method=page.main_text_method,
        in_sitemap=page.in_sitemap,
        inlinks_count=page.inlinks_count,
        schema_types=types,
        psi=PsiOut.model_validate(psi_row, from_attributes=True) if psi_row else None,
        issues=[_page_issue(i) for i in issues],
        inlinks=[LinkOut(url=u, anchor=a or "", rel=r, page_id=pid) for u, a, r, pid in inlinks],
        outlinks=[LinkOut(url=u, anchor=a, rel=r, page_id=pid) for u, a, r, pid in outlinks],
        outlinks_total=outlinks_total,
    )


@router.get("/audits/{audit_id}/export.csv")
async def export_csv(audit_id: uuid.UUID, ctx: ReadCtx) -> Response:
    crawl = await ctx.get_or_404(Crawl, audit_id, "Audit")
    catalog = load_catalog()
    rows = (
        await ctx.session.execute(
            sa.select(Issue.check_id, Issue.severity, Issue.category, Page.normalised_url, Issue.details)
            .select_from(Issue)
            .outerjoin(Page, Page.id == Issue.page_id)
            .where(Issue.crawl_id == crawl.id)
        )
    ).all()
    rows = sorted(rows, key=lambda r: (SEVERITY_ORDER.get(r[1], 9), r[0], r[3] or ""))
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["severity", "check_id", "category", "issue", "url", "details", "how_to_fix"])
    for check_id, severity, category, url, details in rows:
        info = catalog.get(check_id)
        writer.writerow(
            [
                _csv_safe(severity),
                _csv_safe(check_id),
                _csv_safe(category),
                _csv_safe(info.title if info else check_id),
                _csv_safe(url or "(whole site)"),
                _csv_safe(json.dumps(details, ensure_ascii=False, sort_keys=True) if details else ""),
                _csv_safe(info.how_to_fix if info else ""),
            ]
        )
    return Response(
        content="﻿" + out.getvalue(),  # BOM so Excel reads UTF-8
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="audit-{crawl.id}.csv"'},
    )


@router.get("/audits/{audit_id}/changes", response_model=ChangesOut)
async def audit_changes(audit_id: uuid.UUID, ctx: ReadCtx) -> ChangesOut:
    crawl = await ctx.get_or_404(Crawl, audit_id, "Audit")
    snap = await ctx.session.scalar(sa.select(AuditSnapshot).where(AuditSnapshot.crawl_id == crawl.id))
    previous = await ctx.session.scalar(
        ctx.select(Crawl)
        .where(
            Crawl.site_id == crawl.site_id,
            Crawl.status == CrawlStatus.done,
            Crawl.created_at < crawl.created_at,
            Crawl.id != crawl.id,
        )
        .order_by(Crawl.created_at.desc())
        .limit(1)
    )
    if previous is None or snap is None:
        return ChangesOut(
            previous_audit_id=None,
            previous_score=None,
            score=snap.score if snap else None,
            score_delta=None,
            new=[],
            fixed=[],
            persisting_count=0,
            not_rechecked_count=0,
        )
    prev_snap = await ctx.session.scalar(
        sa.select(AuditSnapshot).where(AuditSnapshot.crawl_id == previous.id)
    )
    now_rows = await _issue_keys(ctx, crawl.id)
    before_rows = await _issue_keys(ctx, previous.id)
    crawled = set(await ctx.session.scalars(sa.select(Page.normalised_url).where(Page.crawl_id == crawl.id)))

    def key(row: tuple[str, str | None, str]) -> IssueKey:
        return (row[0], row[1] or "")

    severity = {key(r): r[2] for r in [*before_rows, *now_rows]}
    diff = diff_issues([key(r) for r in before_rows], [key(r) for r in now_rows], crawled)

    def item(k: IssueKey) -> ChangeItem:
        info = _info(k[0])
        return ChangeItem(
            check_id=k[0],
            title=info.title if info else k[0],
            severity=severity.get(k, "info"),
            url=k[1] or None,
        )

    order = lambda k: (SEVERITY_ORDER.get(severity.get(k, "info"), 9), k)  # noqa: E731
    score = snap.score
    previous_score = prev_snap.score if prev_snap else None
    return ChangesOut(
        previous_audit_id=previous.id,
        previous_score=previous_score,
        score=score,
        score_delta=round(score - previous_score, 1)
        if score is not None and previous_score is not None
        else None,
        new=[item(k) for k in sorted(diff.new, key=order)],
        fixed=[item(k) for k in sorted(diff.fixed, key=order)],
        persisting_count=len(diff.persisting),
        not_rechecked_count=len(diff.not_rechecked),
    )
