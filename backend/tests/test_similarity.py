"""TF-IDF similarity, anchors and the internal-link builder: pure, deterministic."""

from __future__ import annotations

import math
import statistics

import pytest

from searchos.planner.builders import BUILDERS
from searchos.planner.links import (
    MAX_LINKS_PER_SOURCE,
    MAX_SOURCES_PER_TARGET,
    build_add_internal_links,
)
from searchos.planner.similarity import (
    SIMILARITY_THRESHOLD,
    TextIndex,
    pick_anchor,
    ranked_sources,
    term_weights,
    tokens,
)
from tests.data.topical_pages import COFFEE, HOSTING, PAGES
from tests.planner_helpers import audit, context, opp, page

TEXTS = {url: f"{title} {title} {body}" for url, (title, body) in PAGES.items()}


def test_tokens_drop_stop_words_digits_and_short_words() -> None:
    assert tokens("The Quick brown fox, 42 of it!") == ["quick", "brown", "fox"]
    assert tokens("To be or not to be") == []


def test_tfidf_matches_a_hand_computed_cosine() -> None:
    docs = {"a": "coffee beans coffee", "b": "coffee grinder", "c": "hosting server"}
    index = TextIndex.build(docs)
    n = 3

    def idf(df: int) -> float:
        return math.log((1 + n) / (1 + df)) + 1

    # a = {coffee: (1+ln2)*idf(2), beans: idf(1)}, b = {coffee: idf(2), grinder: idf(1)}
    a = {"coffee": (1 + math.log(2)) * idf(2), "beans": idf(1)}
    b = {"coffee": idf(2), "grinder": idf(1)}
    dot = a["coffee"] * b["coffee"]
    expected = dot / (math.hypot(*a.values()) * math.hypot(*b.values()))
    scores = index.similarity_to("a")
    assert scores["b"] == pytest.approx(expected)
    assert scores["c"] == 0.0
    assert "a" not in scores
    assert index.similarity_to("missing") == {}


def test_vectors_are_normalised_and_the_result_is_deterministic() -> None:
    first = TextIndex.build(TEXTS)
    second = TextIndex.build(dict(reversed(list(TEXTS.items()))))
    assert first.urls == second.urls
    for url in first.urls:
        assert first.similarity_to(url) == second.similarity_to(url)
        row = first.matrix[first.row_of[url]]
        assert row.multiply(row).sum() == pytest.approx(1.0)


def test_top_terms_are_the_heaviest_and_stable() -> None:
    index = TextIndex.build(TEXTS)
    terms = index.top_terms("https://t.example/coffee/grinders", 5)
    assert terms == index.top_terms("https://t.example/coffee/grinders", 5)
    assert len(terms) == 5
    assert "grinder" in terms or "grind" in terms
    assert index.top_terms("nope") == []


def test_empty_documents_do_not_break_the_index() -> None:
    index = TextIndex.build({"a": "", "b": "coffee beans"})
    assert index.similarity_to("a") == {"b": 0.0}


def test_threshold_separates_the_two_topics() -> None:
    index = TextIndex.build(TEXTS)
    within: list[float] = []
    across: list[float] = []
    for url in index.urls:
        for other, score in index.similarity_to(url).items():
            same = url.split("/")[-2] == other.split("/")[-2]
            (within if same else across).append(score)
    assert max(across) < SIMILARITY_THRESHOLD
    assert statistics.median(within) > SIMILARITY_THRESHOLD
    assert sum(1 for s in within if s >= SIMILARITY_THRESHOLD) / len(within) >= 0.5


def test_ranked_sources_filters_sorts_and_breaks_ties_by_url() -> None:
    scores = {"b": 0.4, "a": 0.4, "c": 0.9, "d": 0.01, "e": 0.8}
    assert ranked_sources(scores, ["a", "b", "c", "d"], 0.05) == [("c", 0.9), ("a", 0.4), ("b", 0.4)]
    assert ranked_sources(scores, [], 0.05) == []


def test_pick_anchor_copies_words_from_the_source() -> None:
    text = "Our guide to espresso machines covers pressure. A burr grinder helps too."
    weights = term_weights(["Espresso machines"], [])
    assert pick_anchor(text, weights) == "espresso machines"
    assert pick_anchor(text, weights) in text


def test_pick_anchor_does_not_span_punctuation_or_start_with_stop_words() -> None:
    text = "Espresso, machines."
    weights = {"espresso": 2.0, "machines": 2.0}
    assert pick_anchor(text, weights) is None  # the two words are separated by punctuation
    text = "the espresso machines"
    anchor = pick_anchor(text, weights)
    assert anchor == "espresso machines"


def test_pick_anchor_needs_a_match_and_two_content_words() -> None:
    assert pick_anchor("Nothing relevant lives here at all.", {"espresso": 2.0}) is None
    assert pick_anchor("espresso", {"espresso": 2.0}) is None


def test_pick_anchor_prefers_the_higher_weight_and_then_the_earlier_phrase() -> None:
    text = "Dark roast beans arrive. Light roast beans arrive."
    # a shorter phrase wins over a longer one with the same matches (a small penalty per word)
    assert pick_anchor(text, {"roast": 1.0, "beans": 1.0}) == "roast beans"
    assert pick_anchor(text, {"light": 2.0, "roast": 1.0, "beans": 1.0}) == "Light roast beans"


# ---- the builder -------------------------------------------------------------------------------------
def _site_pages() -> list:
    pages = []
    for i, (url, (title, body)) in enumerate(PAGES.items()):
        path = url.replace("https://t.example", "")
        pages.append(
            page(
                path,
                url=url.replace("t.example", "shop.example"),
                title=title,
                h1=(title,),
                text=body,
                inlinks=2,
                depth=1 + (i % 2),
            )
        )
    return pages


def _ctx(target_path: str, source: str = "ORPHAN_PAGE"):
    pages = _site_pages()
    target = next(p for p in pages if p.url.endswith(target_path))
    target = page(
        target_path, url=target.url, title=target.title, h1=target.h1, text=target.text, inlinks=0, depth=3
    )
    pages = [target if p.url == target.url else p for p in pages]
    pages.append(
        page("/", inlinks=0, depth=0, text="Welcome to the shop. Coffee equipment and hosting advice.")
    )
    return context(pages, [audit(source, target.url)]), target


def test_orphan_gets_links_from_same_topic_pages_only() -> None:
    ctx, target = _ctx("/coffee/grinders")
    out = build_add_internal_links(ctx, list(ctx.findings))
    assert out
    assert len(out) <= MAX_SOURCES_PER_TARGET
    for c in out:
        assert c.type == "add_internal_links" and c.field == "internal_links"
        assert "/coffee/" in (c.target_url or "")
        links = c.proposed_value["links"]
        assert 1 <= len(links) <= MAX_LINKS_PER_SOURCE
        source = ctx.pages[c.target_url]
        for link in links:
            assert link["to"] == target.url
            assert link["anchor"] in source.text
    # deterministic
    again = build_add_internal_links(ctx, list(ctx.findings))
    assert [c.proposed_value for c in again] == [c.proposed_value for c in out]


def test_pages_that_already_link_are_not_suggested_again() -> None:
    ctx, target = _ctx("/coffee/grinders")
    first = build_add_internal_links(ctx, list(ctx.findings))
    sources = {c.target_url for c in first}
    ctx.linked_from[target.url] = frozenset(sources)
    again = build_add_internal_links(ctx, list(ctx.findings))
    assert all(c.target_url not in sources for c in again)


def test_noindex_or_textless_targets_are_ignored() -> None:
    ctx, target = _ctx("/coffee/grinders")
    ctx.pages[target.url] = page(
        "/coffee/grinders",
        url=target.url,
        title=target.title,
        text=target.text,
        is_indexable=False,
        inlinks=0,
    )
    assert build_add_internal_links(ctx, list(ctx.findings)) == []


def test_unrelated_target_is_skipped_with_a_reason() -> None:
    pages = _site_pages()
    odd = page(
        "/odd",
        title="Zebra crossing",
        h1=("Zebra crossing",),
        text="Zebras cross stripes quickly.",
        inlinks=0,
    )
    ctx = context([*pages, odd], [audit("ORPHAN_PAGE", odd.url)])
    assert build_add_internal_links(ctx, list(ctx.findings)) == []
    assert any(s.get("url") == odd.url for s in ctx.skipped)


def test_striking_distance_is_measured_with_the_curve() -> None:
    ctx, target = _ctx("/coffee/espresso", source="STRIKING_DISTANCE")
    ctx.findings = [
        opp(
            "STRIKING_DISTANCE",
            target.url,
            "espresso machine basics",
            impressions=3000,
            position=9.0,
            ctr=0.01,
        )
    ]
    out = build_add_internal_links(ctx, list(ctx.findings))
    assert out
    assert all(c.gain_clicks is not None and c.gain_clicks > 0 for c in out)
    assert out[0].source_kind == "gsc"
    assert "add_internal_links" in BUILDERS


def test_each_source_page_gets_one_action_with_at_most_three_links() -> None:
    pages = _site_pages()
    targets = [
        page(
            f"/coffee/new-{i}",
            title=f"Coffee grinder {i}",
            h1=(f"Coffee grinder {i}",),
            text="Coffee grinder burr espresso pour over beans brew",
            inlinks=0,
        )
        for i in range(5)
    ]
    ctx = context([*pages, *targets], [audit("ORPHAN_PAGE", t.url) for t in targets])
    out = build_add_internal_links(ctx, list(ctx.findings))
    urls = [c.target_url for c in out]
    assert len(urls) == len(set(urls))
    assert all(len(c.proposed_value["links"]) <= MAX_LINKS_PER_SOURCE for c in out)
    assert COFFEE and HOSTING
