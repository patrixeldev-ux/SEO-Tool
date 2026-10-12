from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from searchos.crawl.engine import CrawlAborted, CrawlConfig, Crawler, first_reachable
from searchos.crawl.graph import compute_graph
from searchos.crawl.records import ListSink, PageRecord
from searchos.devtools.fixture_server import FixtureServer, default_fixture_dir


def fixture_config(server: FixtureServer, **kwargs: object) -> CrawlConfig:
    return CrawlConfig(
        start_url=server.base_url + "/",
        allowed_hosts=frozenset({server.netloc}),
        rate_per_host=1000,
        allow_private_hosts=True,
        **kwargs,  # type: ignore[arg-type]
    )


def fixture_html_urls() -> set[str]:
    root = default_fixture_dir()
    urls = set()
    for f in root.rglob("*.html"):
        rel = f.relative_to(root).as_posix()
        urls.add(
            "/" + (rel[: -len("index.html")] if rel == "index.html" or rel.endswith("/index.html") else rel)
        )
    return urls


# URLs on the fixture that are not .html files on disk, but are linked and therefore crawled.
VIRTUAL_URLS = {"/gone.html", "/server-error.html", "/redir-single", "/redir-chain-a", "/redir-loop-a"}


async def crawl_fixture(server: FixtureServer, **kwargs: object) -> tuple[dict[str, PageRecord], object]:
    sink = ListSink()
    outcome = await Crawler(fixture_config(server, **kwargs), sink).run()
    base = server.base_url
    return {p.url.removeprefix(base): p for p in sink.pages}, outcome


async def test_fixture_crawl_stores_every_page_with_correct_depth(fixture_site: FixtureServer) -> None:
    pages, outcome = await crawl_fixture(fixture_site)
    assert set(pages) == fixture_html_urls() | VIRTUAL_URLS
    assert outcome.stop_reason == "complete"  # type: ignore[attr-defined]

    base = fixture_site.base_url
    graph = outcome.graph()  # type: ignore[attr-defined]
    depth = {path: graph.depth.get(base + path) for path in pages}
    assert depth["/"] == 0
    assert depth["/about.html"] == 1
    assert depth["/products/"] == 1
    assert depth["/products/espresso-beans.html"] == 2
    assert [depth[f"/deep/level-{i}.html"] for i in range(1, 6)] == [1, 2, 3, 4, 5]
    assert depth["/defects/broken-link.html"] == 2
    assert depth["/gone.html"] == 3
    assert depth["/redir-target.html"] == 3
    assert depth["/defects/orphan.html"] is None  # only reachable through the sitemap
    assert depth["/private/report.html"] is None
    assert graph.inlinks[base + "/defects/orphan.html"] == 0
    assert graph.inlinks[base + "/gone.html"] == 1
    assert graph.inlinks[base + "/chain-final.html"] == 1  # reached through the redirect chain


async def test_status_redirects_and_errors_are_recorded(fixture_site: FixtureServer) -> None:
    pages, _ = await crawl_fixture(fixture_site)
    assert pages["/gone.html"].status_code == 404
    assert pages["/server-error.html"].status_code == 500
    chain = pages["/redir-chain-a"]
    assert [h["status"] for h in chain.redirect_chain] == [301, 301]
    assert chain.final_url == fixture_site.base_url + "/chain-final.html"
    assert chain.title is None  # content belongs to the target page, not the redirect source
    assert pages["/chain-final.html"].status_code == 200
    assert pages["/chain-final.html"].redirect_chain == []
    loop = pages["/redir-loop-a"]
    assert loop.fetch_error == "redirect_loop"
    assert len(loop.redirect_chain) == 2
    assert [h["status"] for h in pages["/redir-single"].redirect_chain] == [301]
    assert "/redir-loop-b" not in pages and "/redir-chain-b" not in pages  # intermediate hops are not pages


async def test_robots_disallowed_urls_are_never_requested(fixture_site: FixtureServer) -> None:
    fixture_site.requests.clear()
    pages, outcome = await crawl_fixture(fixture_site)
    assert not [p for p in fixture_site.requested_paths() if p.startswith("/private/")]
    stub = pages["/private/report.html"]
    assert stub.fetch_error == "blocked_by_robots"
    assert stub.status_code is None
    assert stub.in_sitemap is True
    assert outcome.blocked_by_robots == 1  # type: ignore[attr-defined]


async def test_crawler_identifies_itself(fixture_site: FixtureServer) -> None:
    fixture_site.requests.clear()
    await crawl_fixture(fixture_site)
    agents = {r.user_agent for r in fixture_site.requests}
    assert agents == {"SearchOSBot/0.1 (+https://patrixel.com/searchos-bot)"}


async def test_sitemap_membership_and_parsed_fields(fixture_site: FixtureServer) -> None:
    pages, outcome = await crawl_fixture(fixture_site)
    assert pages["/about.html"].in_sitemap is True
    assert pages["/defects/title-missing.html"].in_sitemap is False
    assert pages["/defects/xrobots-noindex.html"].x_robots_tag == "noindex"
    assert pages["/defects/slow.html"].ttfb_ms is not None and pages["/defects/slow.html"].ttfb_ms >= 1200
    assert pages["/about.html"].main_text_method == "trafilatura"
    assert pages["/about.html"].structured_data["jsonld"][0]["@type"] == "Organization"
    assert outcome.llms_txt_present is False  # type: ignore[attr-defined]
    assert outcome.robots.status == "ok"  # type: ignore[attr-defined]


async def test_page_limit_stops_the_crawl_and_says_so(fixture_site: FixtureServer) -> None:
    pages, outcome = await crawl_fixture(fixture_site, page_limit=10)
    assert outcome.stop_reason == "page_limit"  # type: ignore[attr-defined]
    assert outcome.urls_not_crawled > 0  # type: ignore[attr-defined]
    assert any("page limit" in w for w in outcome.warnings)  # type: ignore[attr-defined]
    assert len(pages) <= 14  # 10 fetches (+ redirect targets that came for free)
    assert "/" in pages


async def test_cancellation_stops_early(fixture_site: FixtureServer) -> None:
    sink = ListSink()
    calls = 0

    async def cancel() -> bool:
        nonlocal calls
        calls += 1
        return len(sink.pages) >= 5

    outcome = await Crawler(
        fixture_config(fixture_site, cancel_check_interval=0), sink, is_cancelled=cancel
    ).run()
    assert outcome.stop_reason == "cancelled"
    assert 5 <= len(sink.pages) < 40


async def test_progress_callback_reports_counts(fixture_site: FixtureServer) -> None:
    seen: list[tuple[int, int]] = []

    async def progress(done: int, discovered: int) -> None:
        seen.append((done, discovered))

    await Crawler(fixture_config(fixture_site, page_limit=8), ListSink(), on_progress=progress).run()
    assert seen
    assert [d for d, _ in seen] == sorted(d for d, _ in seen)
    assert seen[-1][0] >= 8


# ---- scenarios with a scripted transport ---------------------------------------------------------
def scripted(pages: dict[str, tuple[int, str]]) -> tuple[httpx.MockTransport, list[str]]:
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        status, body = pages.get(request.url.path, (404, "nope"))
        return httpx.Response(status, html=body, headers={"Content-Type": "text/html"})

    return httpx.MockTransport(handler), requested


def config_for(start: str, *hosts: str, **kwargs: object) -> CrawlConfig:
    return CrawlConfig(
        start_url=start,
        allowed_hosts=frozenset(hosts),
        rate_per_host=1000,
        allow_private_hosts=True,
        **kwargs,  # type: ignore[arg-type]
    )


async def test_other_hosts_are_never_fetched() -> None:
    transport, requested = scripted(
        {
            "/": (
                200,
                '<a href="https://evil.example/x">x</a><a href="/a">a</a><link rel="canonical" href="https://evil.example/">',
            )
        }
    )
    sink = ListSink()
    await Crawler(config_for("https://site.test/", "site.test"), sink, transport=transport).run()
    assert all("evil.example" not in u for u in requested)
    assert any(
        link.to_url == "https://evil.example/x" and not link.is_internal
        for p in sink.pages
        for link in p.links
    )


async def test_www_alias_is_followed_when_allowed() -> None:
    transport, requested = scripted(
        {"/": (200, '<a href="https://www.site.test/x">x</a>'), "/x": (200, "<p>hi</p>")}
    )
    sink = ListSink()
    await Crawler(
        config_for("https://site.test/", "site.test", "www.site.test"), sink, transport=transport
    ).run()
    assert "https://www.site.test/x" in requested


async def test_trailing_slash_and_fragment_variants_are_one_page() -> None:
    transport, requested = scripted(
        {
            "/": (200, '<a href="/a#top">1</a><a href="/a">2</a><a href="/A/../a">3</a>'),
            "/a": (200, "<p>x</p>"),
        }
    )
    await Crawler(config_for("https://site.test/", "site.test"), ListSink(), transport=transport).run()
    assert requested.count("https://site.test/a") == 1


async def test_unreadable_robots_aborts_the_crawl() -> None:
    transport = httpx.MockTransport(lambda r: httpx.Response(503))
    with pytest.raises(CrawlAborted, match=r"robots\.txt"):
        await Crawler(config_for("https://site.test/", "site.test"), ListSink(), transport=transport).run()


async def test_a_page_that_breaks_the_parser_does_not_stop_the_crawl() -> None:
    transport, _ = scripted(
        {
            "/": (200, '<a href="/bad">b</a><a href="/ok">o</a>'),
            "/bad": (200, "\x00<<"),
            "/ok": (200, "<p>ok</p>"),
        }
    )
    sink = ListSink()
    await Crawler(config_for("https://site.test/", "site.test"), sink, transport=transport).run()
    assert {p.url for p in sink.pages} >= {"https://site.test/ok", "https://site.test/bad"}


async def test_first_reachable_prefers_https_but_falls_back_to_http() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.scheme == "https":
            raise httpx.ConnectError("no tls", request=request)
        return httpx.Response(200, html="ok")

    cfg = config_for("https://site.test/", "site.test")
    picked = await first_reachable(
        ["https://site.test/", "http://site.test/"], cfg, transport=httpx.MockTransport(handler)
    )
    assert picked == "http://site.test/"
    nothing = await first_reachable(
        ["https://site.test/"],
        cfg,
        transport=httpx.MockTransport(lambda r: (_ for _ in ()).throw(httpx.ConnectError("x", request=r))),
    )
    assert nothing is None


def test_config_is_clamped_and_normalised() -> None:
    cfg = CrawlConfig(
        start_url="HTTP://Site.Test", allowed_hosts=frozenset({"Site.Test"}), page_limit=999_999
    )
    assert cfg.page_limit == 10_000
    assert cfg.start_url == "http://site.test/"
    assert cfg.allowed_hosts == frozenset({"site.test"})
    assert (
        CrawlConfig(start_url="http://a.test", allowed_hosts=frozenset({"a.test"}), page_limit=0).page_limit
        == 1
    )


# ---- the pure graph function ---------------------------------------------------------------------
def test_compute_graph_depth_and_inlinks() -> None:
    edges = {
        "/": {"/a", "/b", "/old"},
        "/a": {"/c", "/a", "/"},
        "/b": {"/c"},
        "/c": {"/d"},
        "/new": {"/e"},
    }
    redirects = {"/old": "/new"}
    pages = {"/", "/a", "/b", "/c", "/d", "/old", "/new", "/e", "/lonely"}
    g = compute_graph("/", edges, redirects, pages)
    assert g.depth == {"/": 0, "/a": 1, "/b": 1, "/old": 1, "/new": 1, "/c": 2, "/e": 2, "/d": 3}
    assert "/lonely" not in g.depth
    assert g.inlinks["/c"] == 2  # from /a and /b
    assert g.inlinks["/a"] == 1  # self-link ignored; linked from /
    assert g.inlinks["/new"] == 1  # the link to /old counts for /new
    assert g.inlinks["/old"] == 1
    assert g.inlinks["/lonely"] == 0
    assert g.inlinks["/"] == 1


def test_compute_graph_handles_redirect_loops_and_missing_start() -> None:
    g = compute_graph("/x", {}, {"/a": "/b", "/b": "/a"}, {"/a", "/b"})
    assert g.depth == {}
    assert Path(__file__).exists()
