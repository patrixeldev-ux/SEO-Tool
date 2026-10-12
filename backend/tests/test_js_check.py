"""JS_DEPENDENT_CONTENT: render sampling, the job wiring with a fake browser, and a real-Chromium test."""

from __future__ import annotations

import os

import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from searchos.core.db import get_engine
from searchos.crawl.engine import CrawlConfig
from searchos.crawl.parser import count_words, extract_main_text
from searchos.crawl.render import MAX_RENDERED_PAGES, PlaywrightRenderer, sample_pages
from searchos.devtools.fixture_server import FixtureServer
from searchos.models import CrawlStatus, Issue, Page
from searchos.worker import audit_job
from searchos.worker.audit_job import JS_UNAVAILABLE
from searchos.worker.crawl_job import crawl_handler
from searchos.worker.jobs import run_job
from tests.helpers import make_crawl

JS_PAGE = "/defects/js-rendered.html"


class FakeRenderer:
    """Stands in for the browser: serves the raw HTML, except for the pages it is told 'scripts' fill in."""

    def __init__(self, base: str, rendered: dict[str, str], *, available: bool = True) -> None:
        self.base, self.rendered, self.available = base, rendered, available
        self.closed = False
        self.rendered_urls: list[str] = []

    async def start(self) -> bool:
        return self.available

    async def render(self, url: str) -> str | None:
        self.rendered_urls.append(url)
        path = url.removeprefix(self.base)
        if path in self.rendered:
            return self.rendered[path]
        async with httpx.AsyncClient() as client:
            return (await client.get(url)).text

    async def aclose(self) -> None:
        self.closed = True


def _long_article() -> str:
    paragraphs = "".join(f"<p>{' '.join(f'word{i}x{j}' for j in range(30))}.</p>" for i in range(20))
    return f"<html><head><title>Rendered</title></head><body><main><h1>Menu</h1>{paragraphs}</main></body></html>"


def test_sample_pages_is_all_pages_when_few_and_shallow_first() -> None:
    pages = [("https://a/b", 2), ("https://a/", 0), ("https://a/c", 1), ("https://a/z", None)]
    assert sample_pages(pages) == ["https://a/", "https://a/c", "https://a/b", "https://a/z"]


def test_sample_pages_caps_at_the_limit_and_is_repeatable() -> None:
    pages = [(f"https://a/p{i:03d}", i % 5) for i in range(200)]
    first = sample_pages(pages)
    assert len(first) == MAX_RENDERED_PAGES == len(set(first))
    assert first == sample_pages(list(reversed(pages)))


async def test_js_only_page_is_flagged_and_server_rendered_pages_are_not(
    session: AsyncSession, fixture_site: FixtureServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeRenderer(fixture_site.base_url, {JS_PAGE: _long_article()})
    monkeypatch.setattr(audit_job, "make_renderer", lambda _config: fake)
    _, _, crawl, job = await make_crawl(session, fixture_site)
    await run_job(job.id, crawl_handler, engine=get_engine())

    await session.refresh(crawl)
    assert crawl.status is CrawlStatus.done, crawl.error
    assert fake.closed and fake.rendered_urls
    assert crawl.site_info["js_check"]["available"] is True
    assert JS_UNAVAILABLE not in crawl.warnings
    flagged = (
        await session.scalars(
            sa.select(Page.normalised_url)
            .join(Issue, Issue.page_id == Page.id)
            .where(Issue.crawl_id == crawl.id, Issue.check_id == "JS_DEPENDENT_CONTENT")
        )
    ).all()
    assert [u.removeprefix(fixture_site.base_url) for u in flagged] == [JS_PAGE]
    js_row = await session.scalar(
        sa.select(Page).where(
            Page.crawl_id == crawl.id, Page.normalised_url == fixture_site.base_url + JS_PAGE
        )
    )
    assert js_row is not None and js_row.extra["rendered_word_count"] >= 600
    assert js_row.word_count < 300


async def test_missing_browser_is_a_warning_not_an_error(
    session: AsyncSession, fixture_site: FixtureServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeRenderer(fixture_site.base_url, {}, available=False)
    monkeypatch.setattr(audit_job, "make_renderer", lambda _config: fake)
    _, _, crawl, job = await make_crawl(session, fixture_site, page_limit=10)
    await run_job(job.id, crawl_handler, engine=get_engine())

    await session.refresh(crawl)
    assert crawl.status is CrawlStatus.done, crawl.error
    assert JS_UNAVAILABLE in crawl.warnings
    assert crawl.site_info["js_check"]["available"] is False
    assert fake.rendered_urls == []
    count = await session.scalar(
        sa.select(sa.func.count())
        .select_from(Issue)
        .where(Issue.crawl_id == crawl.id, Issue.check_id == "JS_DEPENDENT_CONTENT")
    )
    assert count == 0


async def test_js_check_can_be_switched_off(
    session: AsyncSession, fixture_site: FixtureServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    def must_not_be_called(_config: CrawlConfig) -> FakeRenderer:
        raise AssertionError("renderer should not be created")

    monkeypatch.setattr(audit_job, "make_renderer", must_not_be_called)
    _, _, crawl, job = await make_crawl(
        session, fixture_site, page_limit=5, extra_payload={"js_check": False}
    )
    await run_job(job.id, crawl_handler, engine=get_engine())
    await session.refresh(crawl)
    assert crawl.status is CrawlStatus.done, crawl.error
    assert crawl.site_info["js_check"]["disabled"] is True


@pytest.mark.browser
async def test_real_chromium_sees_the_content_that_javascript_adds(fixture_site: FixtureServer) -> None:
    renderer = PlaywrightRenderer(frozenset({fixture_site.netloc}))
    if not await renderer.start():
        if os.environ.get("REQUIRE_BROWSER") == "1":
            pytest.fail(f"REQUIRE_BROWSER=1 but Chromium could not start: {renderer.error}")
        pytest.skip(f"Chromium is not installed here ({renderer.error})")
    try:
        html = await renderer.render(fixture_site.base_url + JS_PAGE)
    finally:
        await renderer.aclose()
    assert html is not None
    async with httpx.AsyncClient() as client:
        raw = (await client.get(fixture_site.base_url + JS_PAGE)).text
    raw_words = count_words(extract_main_text(raw, fixture_site.base_url + JS_PAGE)[0])
    rendered_words = count_words(extract_main_text(html, fixture_site.base_url + JS_PAGE)[0])
    assert rendered_words >= 300 > raw_words
