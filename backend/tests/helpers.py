"""Shared test helpers for crawl/audit tests that need database rows."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from searchos.devtools.fixture_server import FixtureServer
from searchos.models import Crawl, Job, Org, Site


async def make_crawl(
    session: AsyncSession,
    server: FixtureServer,
    *,
    page_limit: int = 500,
    start_path: str = "/",
    extra_payload: dict[str, Any] | None = None,
    domain: str = "fixture.example",
) -> tuple[Org, Site, Crawl, Job]:
    """An org + site + queued crawl + job pointing at the local fixture server (tests only)."""
    org = Org(name=f"Org {uuid.uuid4().hex[:6]}")
    session.add(org)
    await session.flush()
    site = Site(org_id=org.id, domain=domain, display_name=domain)
    session.add(site)
    await session.flush()
    crawl = Crawl(
        org_id=org.id, site_id=site.id, start_url=server.base_url + start_path, page_limit=page_limit
    )
    session.add(crawl)
    await session.flush()
    payload: dict[str, Any] = {
        "crawl_id": str(crawl.id),
        "allowed_hosts": [server.netloc],
        "rate_per_host": 1000,
        "allow_private_hosts": True,
    }
    payload.update(extra_payload or {})
    job = Job(org_id=org.id, site_id=site.id, type="crawl", payload=payload)
    session.add(job)
    await session.flush()
    crawl.job_id = job.id
    await session.commit()
    return org, site, crawl, job
