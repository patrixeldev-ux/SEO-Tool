"""The crawl job end to end against the fixture site, with real PostgreSQL (no Celery broker needed)."""

from __future__ import annotations

import asyncio

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from searchos.core.db import get_engine
from searchos.devtools.fixture_server import FixtureServer
from searchos.models import Crawl, CrawlStatus, Job, JobStatus, Link, Page
from searchos.worker.crawl_job import crawl_handler
from searchos.worker.jobs import run_job
from tests.helpers import make_crawl


async def test_fixture_crawl_is_stored_with_depths_links_and_job_progress(
    session: AsyncSession, fixture_site: FixtureServer
) -> None:
    _, _, crawl, job = await make_crawl(session, fixture_site)
    await run_job(job.id, crawl_handler, engine=get_engine())

    await session.refresh(job)
    await session.refresh(crawl)
    assert job.status is JobStatus.done, job.error
    assert job.progress == 100
    assert crawl.status is CrawlStatus.done, crawl.error
    assert crawl.pages_crawled == 53
    assert crawl.started_at and crawl.finished_at
    assert crawl.site_info["robots"]["status"] == "ok"
    assert "Disallow: /private/" in crawl.site_info["robots"]["text"]
    assert crawl.site_info["sitemap"]["found"] is True
    assert crawl.site_info["sitemap"]["url_count"] == 13
    assert crawl.site_info["homepage_scheme"] == "http"
    assert crawl.site_info["llms_txt_present"] is False

    base = fixture_site.base_url
    pages = {
        p.normalised_url.removeprefix(base): p
        for p in (await session.scalars(sa.select(Page).where(Page.crawl_id == crawl.id)))
    }
    assert len(pages) == 53
    assert pages["/"].depth == 0 and pages["/"].inlinks_count == 46
    assert pages["/deep/level-5.html"].depth == 5
    assert pages["/defects/orphan.html"].depth is None
    assert pages["/defects/orphan.html"].inlinks_count == 0
    assert pages["/defects/orphan.html"].in_sitemap is True
    assert pages["/about.html"].title == "About Harbor & Pine, Roasters Since 2014"
    assert pages["/about.html"].hreflang[0]["lang"] == "en"
    assert pages["/about.html"].word_count >= 300
    assert pages["/gone.html"].status_code == 404
    assert pages["/redir-chain-a"].redirect_chain[1]["location"] == base + "/chain-final.html"
    assert pages["/private/report.html"].fetch_error == "blocked_by_robots"

    # Links: every internal link whose target was crawled points at that page.
    total = await session.scalar(
        sa.select(sa.func.count()).select_from(Link).where(Link.crawl_id == crawl.id)
    )
    assert total and total > 200
    unresolved = (
        await session.scalars(
            sa.select(Link.to_url).where(
                Link.crawl_id == crawl.id,
                Link.is_internal.is_(True),
                Link.to_page_id.is_(None),
            )
        )
    ).all()
    assert all(
        u.removeprefix(base) in {"/private/report.html"}
        or u not in {p.normalised_url for p in pages.values()}
        for u in unresolved
    )
    about_id = pages["/about.html"].id
    inbound = await session.scalar(
        sa.select(sa.func.count(sa.distinct(Link.from_page_id))).where(
            Link.to_page_id == about_id, Link.from_page_id != about_id
        )
    )
    assert inbound == 46


async def test_crawl_respects_the_page_limit(session: AsyncSession, fixture_site: FixtureServer) -> None:
    _, _, crawl, job = await make_crawl(session, fixture_site, page_limit=12)
    await run_job(job.id, crawl_handler, engine=get_engine())
    await session.refresh(crawl)
    assert crawl.status is CrawlStatus.done
    assert 12 <= crawl.pages_crawled <= 16
    assert any("page limit" in w for w in crawl.warnings)
    assert crawl.site_info["stop_reason"] == "page_limit"


async def test_cancelled_job_stops_and_keeps_cancelled_state(
    session: AsyncSession, fixture_site: FixtureServer
) -> None:
    _, _, crawl, job = await make_crawl(session, fixture_site, extra_payload={"rate_per_host": 8})

    async def cancel_when_underway() -> None:
        # What the API does: flip the job to cancelled while the worker is busy.
        engine = get_engine()
        for _ in range(400):
            async with engine.connect() as conn:
                done = await conn.scalar(sa.select(Crawl.pages_crawled).where(Crawl.id == crawl.id))
            if done and done >= 3:
                break
            await asyncio.sleep(0.02)
        async with engine.begin() as conn:
            await conn.execute(sa.update(Job).where(Job.id == job.id).values(status=JobStatus.cancelled))

    canceller = asyncio.create_task(cancel_when_underway())
    await run_job(job.id, crawl_handler, engine=get_engine())
    await canceller
    await session.refresh(job)
    await session.refresh(crawl)
    assert job.status is JobStatus.cancelled  # run_job did not overwrite it with "done"
    assert crawl.status is CrawlStatus.cancelled
    assert crawl.pages_crawled < 53


async def test_unreachable_site_fails_with_a_plain_message(
    session: AsyncSession, fixture_site: FixtureServer
) -> None:
    _, _, crawl, job = await make_crawl(
        session,
        fixture_site,
        extra_payload={"start_url_candidates": ["http://127.0.0.1:1/"], "allowed_hosts": ["127.0.0.1:1"]},
    )
    await run_job(job.id, crawl_handler, engine=get_engine())
    await session.refresh(job)
    await session.refresh(crawl)
    assert job.status is JobStatus.failed
    assert crawl.status is CrawlStatus.failed
    assert crawl.error is not None and "could not connect" in crawl.error.lower()


async def test_start_url_falls_back_from_https_to_http(
    session: AsyncSession, fixture_site: FixtureServer
) -> None:
    https_url = fixture_site.base_url.replace("http://", "https://") + "/"
    _, _, crawl, job = await make_crawl(
        session,
        fixture_site,
        extra_payload={"start_url_candidates": [https_url, fixture_site.base_url + "/"]},
        page_limit=3,
    )
    await run_job(job.id, crawl_handler, engine=get_engine())
    await session.refresh(crawl)
    assert crawl.status is CrawlStatus.done, crawl.error
    assert crawl.start_url == fixture_site.base_url + "/"
    assert isinstance(await session.get(Crawl, crawl.id), Crawl)
