"""Creating a crawl (the `crawls` row plus its job) for a site and handing it to Celery.

The start URL is always derived from the Site (https first, plain http as a fallback), never taken from a
request. Tests and the dev helper may pass `start_url` / `allowed_hosts` to point at the local fixture server.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from searchos.models import Crawl, CrawlStatus, Job, JobStatus, Site

DEFAULT_PAGE_LIMIT = 500
MAX_PAGE_LIMIT = 10_000
# A crawl that has been "running" for longer than this lost its worker; it no longer blocks a new one.
STALE_AFTER = timedelta(hours=3)


def site_urls(site: Site) -> tuple[list[str], frozenset[str]]:
    """Start URL candidates (https, then http) and the hosts the crawl may fetch from."""
    host = f"www.{site.domain}" if site.uses_www else site.domain
    allowed = frozenset({site.domain, f"www.{site.domain}"})
    return [f"https://{host}/", f"http://{host}/"], allowed


async def active_crawl(session: AsyncSession, site_id: Any) -> Crawl | None:
    """The queued or running crawl of this site, if any (stale ones are marked failed and ignored)."""
    now = datetime.now(UTC)
    rows = (
        await session.scalars(
            sa.select(Crawl).where(
                Crawl.site_id == site_id, Crawl.status.in_([CrawlStatus.queued, CrawlStatus.running])
            )
        )
    ).all()
    live: Crawl | None = None
    for crawl in rows:
        if now - crawl.created_at > STALE_AFTER:
            crawl.status = CrawlStatus.failed
            crawl.error = "This audit was interrupted before it finished."
            crawl.finished_at = now
        else:
            live = crawl
    await session.flush()
    return live


async def create_crawl(
    session: AsyncSession,
    site: Site,
    *,
    created_by: Any,
    page_limit: int = DEFAULT_PAGE_LIMIT,
    start_url: str | None = None,
    payload_extra: dict[str, Any] | None = None,
) -> tuple[Crawl, Job]:
    """Adds the rows and commits (the worker must be able to see them before it is enqueued)."""
    candidates, allowed = site_urls(site)
    crawl = Crawl(
        org_id=site.org_id,
        site_id=site.id,
        created_by=created_by,
        start_url=start_url or candidates[0],
        page_limit=max(1, min(page_limit, MAX_PAGE_LIMIT)),
    )
    session.add(crawl)
    await session.flush()
    payload: dict[str, Any] = {
        "crawl_id": str(crawl.id),
        "allowed_hosts": sorted(allowed),
        "start_url_candidates": [] if start_url else candidates,
    }
    payload.update(payload_extra or {})
    job = Job(org_id=site.org_id, site_id=site.id, created_by=created_by, type="crawl", payload=payload)
    session.add(job)
    await session.flush()
    crawl.job_id = job.id
    await session.commit()
    return crawl, job


async def enqueue_crawl(session: AsyncSession, crawl: Crawl, job: Job) -> bool:
    """Send the job to Celery. On a broker failure both rows are marked failed and False is returned."""
    from searchos.worker.tasks import crawl_task

    try:
        crawl_task.delay(str(job.id))
    except Exception:
        job.status = JobStatus.failed
        job.error = "Could not enqueue job: broker unavailable"
        crawl.status = CrawlStatus.failed
        crawl.error = "The job queue is not available right now. Please try again in a moment."
        crawl.finished_at = datetime.now(UTC)
        await session.commit()
        return False
    return True
