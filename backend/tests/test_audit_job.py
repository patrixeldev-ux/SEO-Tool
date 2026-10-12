"""The audit stored for a crawl: issues, snapshot, re-running."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from searchos.audit.catalog import load_catalog
from searchos.core.db import get_engine, get_sessionmaker
from searchos.devtools.fixture_server import FixtureServer
from searchos.models import AuditSnapshot, CrawlStatus, Issue
from searchos.worker.audit_job import run_audit_for_crawl
from searchos.worker.crawl_job import crawl_handler
from searchos.worker.jobs import run_job
from tests.helpers import make_crawl

CATEGORIES = {"indexability", "on_page", "structured_data", "performance", "ai_readiness"}


async def test_crawl_stores_issues_and_one_snapshot_with_score_and_subscores(
    session: AsyncSession, fixture_site: FixtureServer
) -> None:
    _, site, crawl, job = await make_crawl(session, fixture_site, extra_payload={"js_check": False})
    await run_job(job.id, crawl_handler, engine=get_engine())
    await session.refresh(crawl)
    await session.refresh(site)
    assert crawl.status is CrawlStatus.done, crawl.error

    snaps = (await session.scalars(sa.select(AuditSnapshot).where(AuditSnapshot.crawl_id == crawl.id))).all()
    assert len(snaps) == 1
    snap = snaps[0]
    assert snap.site_id == site.id
    assert snap.score is not None and 0 < snap.score < 100
    assert set(snap.subscores) == CATEGORIES
    assert all(v is None or 0 <= v <= 100 for v in snap.subscores.values())

    issue_count = await session.scalar(
        sa.select(sa.func.count()).select_from(Issue).where(Issue.crawl_id == crawl.id)
    )
    assert issue_count == snap.issue_counts["total"] >= 30
    catalog = load_catalog()
    by_check = snap.issue_counts["by_check"]
    assert sum(by_check.values()) == issue_count
    assert set(by_check) <= set(catalog)
    # Per check "units looked at / failed" is kept so the UI can list the checks that passed.
    assert snap.issue_counts["checks"]["TITLE_MISSING"]["failing"] == by_check["TITLE_MISSING"]
    assert snap.issue_counts["checks"]["TITLE_MISSING"]["applicable"] > 20
    # Severity and category on each issue come from the catalog.
    rows = (await session.execute(sa.select(Issue.check_id, Issue.severity, Issue.category).distinct())).all()
    assert all(
        catalog[c].severity == sev and catalog[c].category == cat for c, sev, cat in rows if c in catalog
    )


async def test_rerunning_the_audit_replaces_issues_and_snapshot(
    session: AsyncSession, fixture_site: FixtureServer
) -> None:
    _, _, crawl, job = await make_crawl(
        session, fixture_site, page_limit=15, extra_payload={"js_check": False}
    )
    await session.refresh(crawl)
    crawl_id = crawl.id
    await run_job(job.id, crawl_handler, engine=get_engine())
    before = await session.scalar(
        sa.select(sa.func.count()).select_from(Issue).where(Issue.crawl_id == crawl_id)
    )
    await run_audit_for_crawl(get_sessionmaker(), crawl_id)
    await run_audit_for_crawl(get_sessionmaker(), crawl_id)
    session.expire_all()
    after = await session.scalar(
        sa.select(sa.func.count()).select_from(Issue).where(Issue.crawl_id == crawl_id)
    )
    snapshots = await session.scalar(
        sa.select(sa.func.count()).select_from(AuditSnapshot).where(AuditSnapshot.crawl_id == crawl_id)
    )
    assert after == before
    assert snapshots == 1
