"""Steps that run after the crawl has been stored: JS rendering check, audit, scoring, snapshot."""

from __future__ import annotations

import uuid
from typing import Any

from searchos.crawl.engine import CrawlConfig
from searchos.worker.jobs import JobContext


async def run_post_crawl_steps(
    ctx: JobContext, crawl_id: uuid.UUID, site_info: dict[str, Any], config: CrawlConfig
) -> list[str]:
    """Returns warnings to show with the audit."""
    return []
