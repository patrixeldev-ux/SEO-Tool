from __future__ import annotations

import re

import pytest

from searchos.audit.ai_bots import BotDataError, load_ai_bots, parse_bots

PLAN_LIST = {
    "OAI-SearchBot",
    "ChatGPT-User",
    "GPTBot",
    "Claude-SearchBot",
    "Claude-User",
    "ClaudeBot",
    "PerplexityBot",
    "Perplexity-User",
    "Google-Extended",
    "Bingbot",
    "Applebot-Extended",
}


def test_bot_list_covers_the_planned_bots_with_sources() -> None:
    bots = load_ai_bots()
    assert {b.name for b in bots} == PLAN_LIST
    for bot in bots:
        assert bot.source_url.startswith("https://"), bot.name
        assert bot.verified_on is None or re.fullmatch(r"\d{4}-\d{2}-\d{2}", bot.verified_on), bot.name


def test_purposes_match_vendor_documentation() -> None:
    purposes = {b.name: b.purpose for b in load_ai_bots()}
    assert purposes["OAI-SearchBot"] == "search"
    assert purposes["GPTBot"] == "training"
    assert purposes["ChatGPT-User"] == "user_fetch"
    assert purposes["ClaudeBot"] == "training"
    assert purposes["Claude-SearchBot"] == "search"
    assert purposes["Claude-User"] == "user_fetch"
    assert purposes["PerplexityBot"] == "search"
    assert purposes["Google-Extended"] == "training"
    assert purposes["Applebot-Extended"] == "training"
    assert purposes["Bingbot"] == "search"


def test_bad_bot_data_is_rejected() -> None:
    with pytest.raises(BotDataError):
        parse_bots('[{"name": "X", "vendor": "V", "purpose": "spying", "source_url": "https://x"}]')
    with pytest.raises(BotDataError):
        parse_bots('[{"name": "X", "vendor": "V", "purpose": "search"}]')
    twice = '{"name": "X", "vendor": "V", "purpose": "search", "source_url": "https://x"}'
    with pytest.raises(BotDataError):
        parse_bots(f"[{twice},{twice}]")
