"""Golden test: crawl the fixture site and compare the audit with fixtures/site/EXPECTED_ISSUES.json exactly.

The expected set is (check_id, URL path). Site-wide issues are keyed to "/". Checks the fixture cannot show are
listed under "not_exercised" with a reason; they are covered by unit tests (or by the bare-site test below).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from searchos.audit.registry import registered_checks
from searchos.core.db import get_engine
from searchos.crawl.render import PlaywrightRenderer
from searchos.devtools.fixture_server import FixtureServer, default_fixture_dir
from searchos.models import Crawl, CrawlStatus, Issue, Page
from searchos.worker.crawl_job import crawl_handler
from searchos.worker.jobs import run_job
from tests.helpers import make_crawl

EXPECTED_PATH = default_fixture_dir() / "EXPECTED_ISSUES.json"
JS_CHECK = "JS_DEPENDENT_CONTENT"


def load_expected() -> dict:  # type: ignore[type-arg]
    return json.loads(EXPECTED_PATH.read_text(encoding="utf-8"))


async def browser_available() -> bool:
    renderer = PlaywrightRenderer(frozenset({"localhost"}))
    ok = await renderer.start()
    await renderer.aclose()
    if not ok and os.environ.get("REQUIRE_BROWSER") == "1":
        pytest.fail(f"REQUIRE_BROWSER=1 but Chromium could not start: {renderer.error}")
    return ok


async def produced(session: AsyncSession, crawl: Crawl, base: str) -> set[tuple[str, str]]:
    rows = await session.execute(
        sa.select(Issue.check_id, Page.normalised_url)
        .select_from(Issue)
        .outerjoin(Page, Page.id == Issue.page_id)
        .where(Issue.crawl_id == crawl.id)
    )
    return {(check, "/" if url is None else url.removeprefix(base) or "/") for check, url in rows}


def test_expected_file_is_well_formed_and_accounts_for_every_registered_check() -> None:
    expected = load_expected()
    exercised = {i["check_id"] for i in expected["issues"]}
    listed = {n["check_id"] for n in expected["not_exercised"]}
    registered = set(registered_checks())
    assert not exercised & listed, "a check is both exercised and listed as not exercised"
    assert exercised | listed == registered, {
        "unaccounted": sorted(registered - exercised - listed),
        "unknown": sorted((exercised | listed) - registered),
    }
    assert all(n["reason"].strip() for n in expected["not_exercised"])
    keys = [(i["check_id"], i["url"]) for i in expected["issues"]]
    assert len(keys) == len(set(keys)), "duplicate expected issue"
    assert all(i["url"].startswith("/") for i in expected["issues"])


async def test_fixture_crawl_reports_exactly_the_planted_issues(
    session: AsyncSession, fixture_site: FixtureServer
) -> None:
    expected = load_expected()
    with_browser = await browser_available()
    _, _, crawl, job = await make_crawl(session, fixture_site, extra_payload={"js_check": with_browser})
    await run_job(job.id, crawl_handler, engine=get_engine())
    await session.refresh(crawl)
    assert crawl.status is CrawlStatus.done, crawl.error

    want = {(i["check_id"], i["url"]) for i in expected["issues"]}
    if not with_browser:
        want = {w for w in want if w[0] != JS_CHECK}  # needs Chromium; CI sets REQUIRE_BROWSER=1
    got = await produced(session, crawl, fixture_site.base_url)
    assert got - want == set(), "unexpected issues (false positives)"
    assert want - got == set(), "planted issues that were not found (false negatives)"
    assert got == want


PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><title>{title} - The Small Bare Test Site</title>
<meta name="description" content="{title} is a page of the bare test site that has no robots.txt or sitemap.">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="canonical" href="{{{{BASE_URL}}}}{path}"></head><body><h1>{title}</h1>
{links}<p>{words}</p></body></html>"""


async def test_site_without_robots_txt_and_sitemap_gets_both_issues(
    session: AsyncSession, tmp_path: Path
) -> None:
    words = " ".join(f"word{i}" for i in range(320))
    (tmp_path / "index.html").write_text(
        PAGE.format(title="Home", path="/", links='<a href="/other.html">Other page</a>', words=words)
    )
    (tmp_path / "other.html").write_text(
        PAGE.format(title="Other", path="/other.html", links='<a href="/">Home</a>', words=words)
    )
    with FixtureServer(root=tmp_path) as server:
        _, _, crawl, job = await make_crawl(session, server, extra_payload={"js_check": False})
        await run_job(job.id, crawl_handler, engine=get_engine())
        await session.refresh(crawl)
        assert crawl.status is CrawlStatus.done, crawl.error
        assert crawl.pages_crawled == 2
        assert crawl.site_info["robots"]["status"] == "missing"
        assert crawl.site_info["sitemap"]["found"] is False
        got = await produced(session, crawl, server.base_url)
    # Plain http, no organisation markup and no llms.txt are the only other findings; with no robots.txt
    # nothing is blocked, so there are no AI-bot issues.
    assert got == {
        ("ROBOTS_MISSING", "/"),
        ("SITEMAP_MISSING", "/"),
        ("HTTPS_MISSING", "/"),
        ("ORG_SCHEMA_MISSING", "/"),
        ("LLMS_TXT", "/"),
    }
