"""3.2: the mapping table covers every check and opportunity; builders turn findings into candidates."""

from __future__ import annotations

import pytest

from searchos.audit.catalog import load_catalog
from searchos.planner import builders
from searchos.planner.catalog import ACTION_TYPES, RiskClass
from searchos.planner.engine import build_candidates, findings_by_action, price
from searchos.planner.mapping import MAPPINGS, OPPORTUNITY_TYPES, mapping_for, sources_for_action
from searchos.stats.ctr_curve import default_curve
from searchos.stats.opportunities import find_opportunities
from tests.planner_helpers import ORIGIN, audit, context, gsc_page, opp, page


# ---- the table -----------------------------------------------------------------------------------
def test_every_catalog_check_is_mapped() -> None:
    missing = sorted(set(load_catalog()) - set(MAPPINGS))
    assert missing == [], f"checks the planner does not know what to do with: {missing}"


def test_every_opportunity_type_is_mapped() -> None:
    assert set(OPPORTUNITY_TYPES) <= set(MAPPINGS)
    # and the stats module really produces exactly these four
    assert set(find_opportunities([], None, default_curve())) == set(OPPORTUNITY_TYPES)


def test_no_mapping_points_at_an_unknown_source_or_action_type() -> None:
    known = set(load_catalog()) | set(OPPORTUNITY_TYPES)
    assert set(MAPPINGS) <= known
    for m in MAPPINGS.values():
        for action in m.actions:
            assert action in ACTION_TYPES


def test_no_action_entries_carry_a_reason_and_actions_carry_a_why() -> None:
    no_action = {m.source for m in MAPPINGS.values() if not m.actions}
    assert no_action == {"AI_TRAINING_BOT_BLOCKED", "LLMS_TXT"}
    for m in MAPPINGS.values():
        if m.actions:
            assert m.why.strip() and m.no_action_reason is None
        else:
            assert m.no_action_reason and len(m.no_action_reason) > 20


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("TITLE_MISSING", {"rewrite_title"}),
        ("TITLE_LENGTH", {"rewrite_title"}),
        ("TITLE_DUPLICATE", {"rewrite_title"}),
        ("CTR_GAP", {"rewrite_title", "rewrite_meta"}),
        ("META_DESC_MISSING", {"rewrite_meta"}),
        ("META_DESC_DUPLICATE", {"rewrite_meta"}),
        ("IMG_ALT_MISSING", {"add_alt"}),
        ("SCHEMA_PARSE_ERROR", {"fix_jsonld"}),
        ("SCHEMA_REQUIRED_PROP_MISSING", {"fix_jsonld"}),
        ("ORG_SCHEMA_MISSING", {"add_jsonld"}),
        ("ORPHAN_PAGE", {"add_internal_links"}),
        ("STRIKING_DISTANCE", {"add_internal_links"}),
        ("CANONICAL_MISSING", {"fix_canonical"}),
        ("CANONICAL_TO_NON200", {"fix_canonical"}),
        ("CANONICAL_MISMATCH", {"fix_canonical"}),
        ("REDIRECT_CHAIN", {"collapse_redirect"}),
        ("BROKEN_INTERNAL_LINK", {"fix_link"}),
        ("THIN_CONTENT", {"refresh_content"}),
        ("DECAY", {"refresh_content"}),
        ("CANNIBALISATION", {"consolidate_pages"}),
        ("JS_DEPENDENT_CONTENT", {"ssr_task"}),
        ("AI_SEARCH_BOT_BLOCKED", {"robots_review"}),
        ("NOINDEX_IN_SITEMAP", {"noindex_review"}),
    ],
)
def test_required_mappings(source: str, expected: set[str]) -> None:
    mapping = mapping_for(source)
    assert mapping is not None and set(mapping.actions) == expected


def test_risk_classes_of_the_mapped_actions() -> None:
    risk = {a: ACTION_TYPES[a].risk_class for a in ACTION_TYPES}
    for s in (
        "CANONICAL_MISSING",
        "REDIRECT_CHAIN",
        "BROKEN_INTERNAL_LINK",
        "THIN_CONTENT",
        "CANNIBALISATION",
    ):
        assert all(risk[a] is RiskClass.approve for a in MAPPINGS[s].actions)
    for s in ("JS_DEPENDENT_CONTENT", "AI_SEARCH_BOT_BLOCKED", "NOINDEX_IN_SITEMAP"):
        assert all(risk[a] is RiskClass.manual for a in MAPPINGS[s].actions)


def test_nothing_maps_to_a_robots_or_noindex_edit() -> None:
    """robots.txt is only ever reviewed by a person; no source maps to an automatic robots/noindex change."""
    for source in ("AI_SEARCH_BOT_BLOCKED", "BLOCKED_IN_SITEMAP", "ROBOTS_MISSING", "NOINDEX_IN_SITEMAP"):
        for action in MAPPINGS[source].actions:
            assert ACTION_TYPES[action].risk_class is RiskClass.manual
    assert set(sources_for_action("robots_review")) == {
        "BLOCKED_IN_SITEMAP",
        "ROBOTS_MISSING",
        "AI_SEARCH_BOT_BLOCKED",
    }


# ---- builders ------------------------------------------------------------------------------------
def test_title_candidate_merges_audit_and_search_findings_and_names_the_query() -> None:
    p = page("/a", title="Short")
    findings = [
        audit("TITLE_LENGTH", p.url, length=5, title="Short"),
        opp(
            "CTR_GAP",
            p.url,
            "buy coffee beans",
            impressions=1000,
            position=3.0,
            ctr=0.02,
            expected_ctr=0.1,
            missing_clicks=80.0,
        ),
    ]
    ctx = context([p], findings, gsc={})
    by_type = findings_by_action(findings)
    assert set(by_type) == {"rewrite_title", "rewrite_meta"}
    (title,) = builders.build_rewrite_title(ctx, by_type["rewrite_title"])
    assert title.field == "title" and title.target_url == p.url and title.current_value == "Short"
    assert title.sources == ["CTR_GAP", "TITLE_LENGTH"] and title.source_kind == "audit"
    assert title.evidence["target_query"] == "buy coffee beans"
    assert title.gain_clicks == pytest.approx(48.0)  # 80 x 0.6
    (meta,) = builders.build_rewrite_meta(ctx, by_type["rewrite_meta"])
    assert meta.gain_clicks == pytest.approx(32.0)  # 80 x 0.4: the two shares add up to the gap
    assert meta.source_kind == "gsc" and meta.current_value == "A description."


def test_snippet_candidates_skip_pages_that_are_not_indexable_or_not_crawled() -> None:
    noindex = page("/n", is_indexable=False)
    ctx = context([noindex], [])
    findings = [
        audit("TITLE_MISSING", noindex.url),
        audit("TITLE_MISSING", ORIGIN + "/unknown"),
        audit("TITLE_MISSING", None),
    ]
    assert builders.build_rewrite_title(ctx, findings) == []


def test_target_query_falls_back_to_the_pages_top_query() -> None:
    p = page("/a")
    ctx = context(
        [p],
        [],
        gsc={"shop.example/a": gsc_page(500, 20, ("best grinder", 300, 12, 6.0), ("grinder", 200, 8, 9.0))},
    )
    (c,) = builders.build_rewrite_title(ctx, [audit("TITLE_MISSING", p.url)])
    assert c.evidence["target_query"] == "best grinder"
    assert c.evidence["search"]["impressions_28d"] == 500 and c.impressions == 500


def test_alt_candidates_are_one_per_image_without_alt() -> None:
    p = page(
        "/a",
        images=(
            {"src": "https://shop.example/img/beans.jpg", "alt": None},
            {"src": "https://shop.example/img/ok.jpg", "alt": "Fine"},
            {"src": "https://shop.example/img/blank.jpg", "alt": ""},
            {"src": "https://shop.example/img/grinder.png?x=1", "alt": None},
        ),
    )
    cands = builders.build_add_alt(context([p], []), [audit("IMG_ALT_MISSING", p.url, count=2)])
    assert [c.field for c in cands] == [
        "alt:https://shop.example/img/beans.jpg",
        "alt:https://shop.example/img/grinder.png?x=1",
    ]
    assert cands[1].evidence["filename"] == "grinder.png"


def test_collapse_redirect_and_canonical_values_are_deterministic() -> None:
    src = page(
        "/old",
        redirect_chain=({"url": ORIGIN + "/old"}, {"url": ORIGIN + "/mid"}),
        final_url=ORIGIN + "/new",
        is_indexable=False,
        is_success_html=False,
        status_code=301,
    )
    (red,) = builders.build_collapse_redirect(
        context([src], []),
        [audit("REDIRECT_CHAIN", src.url, hops=2, chain=[ORIGIN + "/old", ORIGIN + "/mid", ORIGIN + "/new"])],
    )
    assert red.proposed_value == {"from": ORIGIN + "/old", "to": ORIGIN + "/new", "status": 301}
    assert red.current_value == {"chain": [ORIGIN + "/old", ORIGIN + "/mid", ORIGIN + "/new"]}

    plain = page("/p", canonical=None)
    (canon,) = builders.build_fix_canonical(context([plain], []), [audit("CANONICAL_MISSING", plain.url)])
    assert canon.proposed_value == plain.url and canon.current_value is None

    mismatch = page("/m", canonical=ORIGIN + "/other")
    (mm,) = builders.build_fix_canonical(
        context([mismatch], []), [audit("CANONICAL_MISMATCH", mismatch.url, canonical=ORIGIN + "/other")]
    )
    assert mm.proposed_value == mismatch.url and "only if this page should be indexed" in mm.evidence["note"]

    moved = page(
        "/gone",
        redirect_chain=({"url": ORIGIN + "/gone"},),
        final_url=ORIGIN + "/here",
        is_indexable=False,
        is_success_html=False,
        status_code=301,
    )
    bad = page("/b", canonical=ORIGIN + "/gone")
    (to_final,) = builders.build_fix_canonical(
        context([bad, moved], []),
        [audit("CANONICAL_TO_NON200", bad.url, canonical=ORIGIN + "/gone", status=301)],
    )
    assert to_final.proposed_value == ORIGIN + "/here"


def test_fix_link_replaces_with_a_similar_page_or_proposes_removal() -> None:
    src = page("/blog")
    near = page("/coffee-guide")
    ctx = context([src, near, page("/about")], [])
    f = audit(
        "BROKEN_INTERNAL_LINK",
        src.url,
        count=2,
        links=[
            {"url": ORIGIN + "/cofee-guide", "status": 404},
            {"url": ORIGIN + "/zzzz-qqqq", "status": 404},
        ],
    )
    replace, remove = builders.build_fix_link(ctx, [f])
    assert replace.proposed_value == {"from": ORIGIN + "/cofee-guide", "to": near.url, "mode": "replace"}
    assert remove.proposed_value == {"from": ORIGIN + "/zzzz-qqqq", "to": None, "mode": "remove"}
    assert remove.field == "link:" + ORIGIN + "/zzzz-qqqq"


def test_jsonld_candidates() -> None:
    entity = {"@context": "https://schema.org", "@type": "Article", "headline": "H"}
    art = page("/post", jsonld=(entity,), extra={"is_article": True})
    ctx = context([art], [])
    (fix,) = builders.build_fix_jsonld(
        ctx,
        [
            audit(
                "SCHEMA_REQUIRED_PROP_MISSING",
                art.url,
                problems=[{"type": "Article", "missing": ["author", "datePublished"]}],
            )
        ],
    )
    assert fix.field == "jsonld:Article" and fix.current_value == entity
    assert fix.evidence["missing"] == ["author", "datePublished"] and fix.proposed_value is None

    broken = page("/b", jsonld_errors=("Expecting ',' delimiter",), extra={"is_article": True})
    (parse,) = builders.build_fix_jsonld(
        context([broken], []), [audit("SCHEMA_PARSE_ERROR", broken.url, errors=["Expecting ',' delimiter"])]
    )
    assert parse.field == "jsonld:parse_error" and parse.evidence["schema_type"] == "Article"

    home = page("/")
    (org,) = builders.build_add_jsonld(
        context([home], []), [audit("ORG_SCHEMA_MISSING", home.url, found_types=["WebSite"])]
    )
    assert org.field == "jsonld:Organization" and org.target_url == home.url

    # markup that only exists as microdata cannot be patched with JSON-LD: reported, not invented
    micro = page("/m")
    ctx2 = context([micro], [])
    assert (
        builders.build_fix_jsonld(
            ctx2,
            [
                audit(
                    "SCHEMA_REQUIRED_PROP_MISSING",
                    micro.url,
                    problems=[{"type": "Product", "missing": ["name"]}],
                )
            ],
        )
        == []
    )
    assert ctx2.skipped and "microdata" in ctx2.skipped[0]["reason"]


def test_briefs_never_contain_written_content() -> None:
    p = page("/thin", word_count=80)
    findings = [
        audit("THIN_CONTENT", p.url, words=80, min=250),
        audit(
            "ANSWER_BLOCK_MISSING",
            p.url,
            count=1,
            questions=[{"question": "How do I brew?", "next_paragraph_words": 150}],
        ),
        opp(
            "DECAY",
            p.url,
            "x",
            prior_clicks=200,
            recent_clicks=100,
            change_pct=-50.0,
            p_value=0.0001,
            clicks_lost=100,
        ),
    ]
    (brief,) = builders.build_refresh_content(context([p], []), findings)
    assert brief.type == "refresh_content" and brief.field == "content" and brief.target_url == p.url
    assert brief.proposed_value["kind"] == "brief" and len(brief.proposed_value["reasons"]) == 3
    assert "How do I brew?" in " ".join(brief.proposed_value["steps"])
    assert "nothing is written" in brief.evidence["note"]


def test_consolidation_names_the_leading_page() -> None:
    a, b = page("/a"), page("/b")
    f = opp(
        "CANNIBALISATION",
        None,
        "espresso machine",
        query_impressions=1000,
        impressions_on_other_pages=300,
        pages=[
            {"page": b.url, "impressions": 300, "clicks": 5, "position": 8, "share": 0.3},
            {"page": a.url, "impressions": 700, "clicks": 30, "position": 4, "share": 0.7},
        ],
    )
    (c,) = builders.build_consolidate_pages(context([a, b], [f]), [f])
    assert c.target_url == a.url and c.field == "query:espresso machine"
    assert c.proposed_value["leading_page"] == a.url and c.proposed_value["other_pages"] == [b.url]


def test_aggregated_tasks_list_urls_and_robots_is_advice_only() -> None:
    pages = [page(f"/p{i}") for i in range(3)]
    ctx = context(pages, [])
    dev = builders.build_dev_fix(
        ctx, [audit("MIXED_CONTENT", p.url, count=1) for p in pages] + [audit("HTTPS_MISSING", None)]
    )
    assert {d.field for d in dev} == {"check:MIXED_CONTENT", "check:HTTPS_MISSING"}
    mixed = next(d for d in dev if d.field == "check:MIXED_CONTENT")
    assert mixed.target_url is None and mixed.evidence["count"] == 3 and len(mixed.evidence["urls"]) == 3

    (robots,) = builders.build_robots_review(
        ctx,
        [
            audit(
                "AI_SEARCH_BOT_BLOCKED",
                None,
                bots=[{"name": "PerplexityBot", "vendor": "Perplexity", "purpose": "search"}],
            )
        ],
    )
    text = " ".join(robots.proposed_value["steps"])
    assert "PerplexityBot" in text and "never edits robots.txt" in robots.evidence["note"]
    assert robots.proposed_value["kind"] == "brief"
    assert builders.build_ssr_task(ctx, []) == [] and builders.build_noindex_review(ctx, []) == []


def test_candidates_are_unique_per_key_and_priced() -> None:
    p = page("/a", title=None)
    ctx = context([p], [audit("TITLE_MISSING", p.url), audit("TITLE_MISSING", p.url)])
    cands = build_candidates(ctx)
    keys = [c.key for c in cands]
    assert len(keys) == len(set(keys))
    priced = price(ctx, cands[0])
    assert priced.evidence["gain_basis"] == "prior" and priced.priority > 0
