from __future__ import annotations

from searchos.crawl.ratelimit import HostRateLimiter
from searchos.crawl.robots import RobotsRules, rules_from_response

ROBOTS = """
User-agent: *
Disallow: /private/
Disallow: /*.pdf$
Disallow: /search?
Allow: /private/public.html
Crawl-delay: 3

User-agent: SearchOSBot
Disallow: /bot-only/

User-agent: GPTBot
Disallow: /

Sitemap: https://e.com/sitemap.xml
Sitemap: https://e.com/news-sitemap.xml
"""


def test_specific_group_wins_over_wildcard() -> None:
    rules = RobotsRules.from_text(ROBOTS)
    assert not rules.can_fetch("https://e.com/bot-only/x")
    # SearchOSBot has its own group, so the "*" rules (/private/) do not apply to it.
    assert rules.can_fetch("https://e.com/private/x")


def test_wildcard_group_is_the_fallback_for_other_bots() -> None:
    rules = RobotsRules.from_text(ROBOTS)
    assert not rules.can_fetch("https://e.com/private/x", "SomeOtherBot")
    assert rules.can_fetch("https://e.com/private/public.html", "SomeOtherBot")  # longest match: Allow wins
    assert not rules.can_fetch("https://e.com/files/a.pdf", "SomeOtherBot")  # wildcard + end anchor
    assert rules.can_fetch("https://e.com/files/a.pdf?x=1", "SomeOtherBot")  # `$` anchors the end
    assert not rules.can_fetch("https://e.com/search?q=1", "SomeOtherBot")
    assert rules.can_fetch("https://e.com/about", "SomeOtherBot")


def test_named_bot_group_blocks_whole_site() -> None:
    rules = RobotsRules.from_text(ROBOTS)
    assert not rules.can_fetch("https://e.com/", "GPTBot")


def test_sitemaps_and_crawl_delay() -> None:
    rules = RobotsRules.from_text(ROBOTS)
    assert rules.sitemaps == ["https://e.com/sitemap.xml", "https://e.com/news-sitemap.xml"]
    assert rules.crawl_delay("SomeOtherBot") == 3.0
    assert rules.crawl_delay("SearchOSBot") is None


def test_status_mapping_follows_google_semantics() -> None:
    ok = rules_from_response(200, b"User-agent: *\nDisallow: /x/\n")
    assert ok.status == "ok"
    assert not ok.can_fetch("https://e.com/x/1")
    for status in (404, 410, 403, 401):
        missing = rules_from_response(status, b"")
        assert missing.status == "missing"
        assert missing.can_fetch("https://e.com/anything")
    for status in (500, 503, 429):
        err = rules_from_response(status, b"")
        assert err.status == "error"
        assert not err.can_fetch("https://e.com/anything")
    assert rules_from_response(None, None).status == "error"


def test_empty_robots_allows_everything() -> None:
    assert RobotsRules.from_text("").can_fetch("https://e.com/anything")


def test_invalid_utf8_is_tolerated() -> None:
    rules = rules_from_response(200, b"User-agent: *\nDisallow: /a/\n\xff\xfe junk\n")
    assert not rules.can_fetch("https://e.com/a/x")


async def test_rate_limiter_spaces_requests_per_host() -> None:
    now = [0.0]
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)
        now[0] += seconds

    limiter = HostRateLimiter(2.0, clock=lambda: now[0], sleep=fake_sleep)
    for _ in range(4):
        await limiter.wait("a.example")
    assert slept == [0.5, 0.5, 0.5]  # first request is free, then 2 per second
    await limiter.wait("b.example")  # another host has its own budget
    assert slept == [0.5, 0.5, 0.5]


async def test_crawl_delay_only_slows_the_limiter_down() -> None:
    now = [0.0]
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)
        now[0] += seconds

    limiter = HostRateLimiter(2.0, clock=lambda: now[0], sleep=fake_sleep)
    limiter.set_min_interval("a.example", 3)
    await limiter.wait("a.example")
    await limiter.wait("a.example")
    assert slept == [3.0]
    limiter.set_min_interval("a.example", 0.1)  # faster than our own rate: ignored
    assert limiter.interval_for("a.example") == 0.5
