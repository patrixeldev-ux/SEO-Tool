from __future__ import annotations

import socket
from collections.abc import Callable

import httpx
import pytest

from searchos.crawl.fetcher import Fetcher, FetcherConfig
from searchos.crawl.sitemap import discover_sitemaps, parse_sitemap
from searchos.devtools.fixture_server import FixtureServer

HOST = "site.test"
Handler = Callable[[httpx.Request], httpx.Response]


def make_fetcher(handler: Handler, **kwargs: object) -> Fetcher:
    config = FetcherConfig.for_hosts(
        [HOST, f"www.{HOST}"],
        rate_per_host=1000,
        allow_private_hosts=True,
        **kwargs,  # type: ignore[arg-type]
    )
    return Fetcher(config, transport=httpx.MockTransport(handler))


def routes(table: dict[str, httpx.Response | Handler]) -> Handler:
    def handler(request: httpx.Request) -> httpx.Response:
        entry = table.get(request.url.path)
        if entry is None:
            return httpx.Response(404, text="nope")
        return entry(request) if callable(entry) else entry

    return handler


def redirect(location: str, status: int = 301) -> httpx.Response:
    return httpx.Response(status, headers={"Location": location})


async def test_plain_html_fetch_records_status_headers_and_body() -> None:
    seen: list[httpx.Request] = []

    def page(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, html="<html>hi</html>", headers={"X-Robots-Tag": "noindex"})

    async with make_fetcher(routes({"/": page})) as f:
        res = await f.fetch(f"https://{HOST}/")
    assert res.status_code == 200
    assert res.body == b"<html>hi</html>"
    assert res.headers["x-robots-tag"] == "noindex"
    assert res.is_html
    assert res.hops == []
    assert res.ttfb_ms is not None
    assert seen[0].headers["user-agent"].startswith("SearchOSBot/")


async def test_redirect_chain_is_recorded_hop_by_hop() -> None:
    table = {
        "/a": redirect(f"https://{HOST}/b"),
        "/b": redirect("/c", 302),
        "/c": httpx.Response(200, html="ok"),
    }
    async with make_fetcher(routes(table)) as f:
        res = await f.fetch(f"https://{HOST}/a")
    assert [(h.url, h.status, h.location) for h in res.hops] == [
        (f"https://{HOST}/a", 301, f"https://{HOST}/b"),
        (f"https://{HOST}/b", 302, f"https://{HOST}/c"),
    ]
    assert res.final_url == f"https://{HOST}/c"
    assert res.status_code == 200
    assert res.error is None


async def test_redirect_loop_is_detected() -> None:
    table = {"/a": redirect("/b"), "/b": redirect("/a")}
    async with make_fetcher(routes(table)) as f:
        res = await f.fetch(f"https://{HOST}/a")
    assert res.error == "redirect_loop"
    assert len(res.hops) == 2
    assert res.status_code is None


async def test_redirect_to_self_is_a_loop() -> None:
    async with make_fetcher(routes({"/a": redirect("/a")})) as f:
        res = await f.fetch(f"https://{HOST}/a")
    assert res.error == "redirect_loop"


async def test_more_than_five_redirects_is_refused() -> None:
    table = {f"/r{i}": redirect(f"/r{i + 1}") for i in range(10)}
    async with make_fetcher(routes(table)) as f:
        res = await f.fetch(f"https://{HOST}/r0")
    assert res.error == "too_many_redirects"
    assert len(res.hops) == 6


async def test_exactly_five_redirects_is_allowed() -> None:
    table: dict[str, httpx.Response | Handler] = {f"/r{i}": redirect(f"/r{i + 1}") for i in range(5)}
    table["/r5"] = httpx.Response(200, html="end")
    async with make_fetcher(routes(table)) as f:
        res = await f.fetch(f"https://{HOST}/r0")
    assert res.error is None
    assert len(res.hops) == 5
    assert res.final_url == f"https://{HOST}/r5"


async def test_redirect_to_another_host_is_recorded_but_not_followed() -> None:
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return redirect("https://elsewhere.example/landing")

    async with make_fetcher(handler) as f:
        res = await f.fetch(f"https://{HOST}/a")
    assert res.error == "external_redirect"
    assert res.hops[-1].location == "https://elsewhere.example/landing"
    assert all("elsewhere.example" not in u for u in requested)


async def test_start_url_outside_allowed_hosts_is_never_fetched() -> None:
    called = False

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200)

    async with make_fetcher(handler) as f:
        res = await f.fetch("https://other.example/")
    assert res.error == "outside_allowed_hosts"
    assert called is False


async def test_www_alias_is_allowed_and_robots_are_per_host() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            body = "User-agent: *\nDisallow: /x\n" if request.url.host == HOST else ""
            return httpx.Response(200, text=body)
        return httpx.Response(200, html="ok")

    async with make_fetcher(handler) as f:
        assert (await f.fetch(f"https://{HOST}/x")).error == "blocked_by_robots"
        assert (await f.fetch(f"https://www.{HOST}/x")).status_code == 200


async def test_robots_disallow_blocks_without_requesting_the_page() -> None:
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(request.url.path)
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /private/\n")
        return httpx.Response(200, html="ok")

    async with make_fetcher(handler) as f:
        res = await f.fetch(f"https://{HOST}/private/a")
        assert res.error == "blocked_by_robots"
        assert (await f.fetch(f"https://{HOST}/public")).status_code == 200
        await f.fetch(f"https://{HOST}/public2")
    assert "/private/a" not in requested
    assert requested.count("/robots.txt") == 1  # fetched once per host, then cached


async def test_robots_5xx_blocks_everything() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503) if request.url.path == "/robots.txt" else httpx.Response(200, html="ok")

    async with make_fetcher(handler) as f:
        assert (await f.fetch(f"https://{HOST}/")).error == "blocked_by_robots"


async def test_redirect_into_a_disallowed_path_is_blocked() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /secret\n")
        return redirect("/secret") if request.url.path == "/a" else httpx.Response(200, html="s")

    async with make_fetcher(handler) as f:
        res = await f.fetch(f"https://{HOST}/a")
    assert res.error == "blocked_by_robots"
    assert len(res.hops) == 1


async def test_non_html_bodies_are_not_downloaded() -> None:
    table = {
        "/doc.pdf": httpx.Response(
            200, content=b"%PDF" + b"x" * 1000, headers={"Content-Type": "application/pdf"}
        )
    }
    async with make_fetcher(routes(table)) as f:
        res = await f.fetch(f"https://{HOST}/doc.pdf")
    assert res.status_code == 200
    assert res.content_type == "application/pdf"
    assert res.body is None
    assert not res.is_html


async def test_html_body_is_capped() -> None:
    table = {"/big": httpx.Response(200, content=b"<p>" + b"a" * 5000, headers={"Content-Type": "text/html"})}
    async with make_fetcher(routes(table), max_html_bytes=1000) as f:
        res = await f.fetch(f"https://{HOST}/big")
    assert res.truncated is True
    assert res.body is not None
    assert len(res.body) == 1000


async def test_error_statuses_are_returned_not_raised() -> None:
    table = {"/e": httpx.Response(500, html="boom"), "/g": httpx.Response(404, html="no")}
    async with make_fetcher(routes(table)) as f:
        assert (await f.fetch(f"https://{HOST}/e")).status_code == 500
        assert (await f.fetch(f"https://{HOST}/g")).status_code == 404


async def test_timeouts_and_connection_errors_become_error_codes() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        if request.url.path == "/slow":
            raise httpx.ReadTimeout("slow", request=request)
        raise httpx.ConnectError("refused", request=request)

    async with make_fetcher(handler) as f:
        assert (await f.fetch(f"https://{HOST}/slow")).error == "timeout"
        assert (await f.fetch(f"https://{HOST}/down")).error == "connect_error"


async def test_private_addresses_are_refused_unless_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_getaddrinfo(host: str, *_a: object, **_k: object) -> list[tuple[object, ...]]:
        ip = "10.0.0.5" if host == HOST else "93.184.216.34"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0))]

    import asyncio

    loop = asyncio.get_running_loop()
    monkeypatch.setattr(loop, "getaddrinfo", fake_getaddrinfo)
    config = FetcherConfig.for_hosts([HOST, "pub.test"], rate_per_host=1000, respect_robots=False)
    async with Fetcher(config, transport=httpx.MockTransport(lambda r: httpx.Response(200, html="ok"))) as f:
        assert (await f.fetch(f"https://{HOST}/")).error == "private_address"
        assert (await f.fetch("https://pub.test/")).status_code == 200


async def test_crawl_delay_slows_the_limiter() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nCrawl-delay: 4\n")
        return httpx.Response(200, html="ok")

    async with make_fetcher(handler) as f:
        await f.fetch(f"https://{HOST}/")
        assert f.limiter.interval_for(HOST) == 4.0


# ---- sitemaps -----------------------------------------------------------------------------------
def test_parse_sitemap_urlset_index_and_garbage() -> None:
    urlset = b'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc> https://e.com/a </loc></url></urlset>'
    index = b"<sitemapindex><sitemap><loc>https://e.com/s1.xml</loc></sitemap></sitemapindex>"
    assert parse_sitemap(urlset).locs == ["https://e.com/a"]
    assert parse_sitemap(index).kind == "index"
    assert parse_sitemap(b"<html></html>").kind == "invalid"
    assert parse_sitemap(b"not xml at all").kind == "invalid"
    assert parse_sitemap(b"").kind == "invalid"


def test_parse_sitemap_does_not_expand_entities() -> None:
    evil = b'<?xml version="1.0"?><!DOCTYPE urlset [<!ENTITY x SYSTEM "file:///etc/passwd">]><urlset><url><loc>&x;</loc></url></urlset>'
    parsed = parse_sitemap(evil)
    assert all("root:" not in loc for loc in parsed.locs)


async def test_sitemap_discovery_on_the_fixture_site(fixture_site: FixtureServer) -> None:
    config = FetcherConfig.for_hosts([fixture_site.netloc], rate_per_host=1000, allow_private_hosts=True)
    async with Fetcher(config) as f:
        robots = await f.robots_for(fixture_site.base_url + "/")
        result = await discover_sitemaps(f, fixture_site.base_url + "/", robots)
    base = fixture_site.base_url
    assert result.found
    assert result.declared == [f"{base}/sitemap.xml"]
    assert len(result.files) == 3  # index + plain child + gzip child
    assert f"{base}/about.html" in result.urls
    assert f"{base}/defects/orphan.html" in result.urls  # came out of the .gz file
    assert f"{base}/private/report.html" in result.urls
    assert result.errors == []


async def test_sitemap_guess_and_foreign_hosts() -> None:
    xml = (
        b"<urlset><url><loc>https://site.test/a</loc></url><url><loc>https://evil.test/b</loc></url></urlset>"
    )
    table = {"/sitemap.xml": httpx.Response(200, content=xml)}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return routes(table)(request)

    async with make_fetcher(handler) as f:
        robots = await f.robots_for(f"https://{HOST}/")
        result = await discover_sitemaps(f, f"https://{HOST}/", robots)
    assert result.found
    assert result.urls == ["https://site.test/a"]  # the foreign URL is dropped


async def test_missing_sitemap_is_not_an_error() -> None:
    async with make_fetcher(routes({})) as f:
        robots = await f.robots_for(f"https://{HOST}/")
        result = await discover_sitemaps(f, f"https://{HOST}/", robots)
    assert not result.found
    assert result.errors == []
    assert result.urls == []


async def test_sitemap_index_cycles_and_depth_are_bounded() -> None:
    idx = b"<sitemapindex><sitemap><loc>https://site.test/s.xml</loc></sitemap></sitemapindex>"
    table = {"/sitemap.xml": httpx.Response(200, content=idx), "/s.xml": httpx.Response(200, content=idx)}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404) if request.url.path == "/robots.txt" else routes(table)(request)

    async with make_fetcher(handler) as f:
        robots = await f.robots_for(f"https://{HOST}/")
        result = await discover_sitemaps(f, f"https://{HOST}/", robots)
    assert result.urls == []
    assert len(result.files) == 2  # no infinite loop
