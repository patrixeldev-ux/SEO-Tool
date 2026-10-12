"""3.5: the manual task generator - clear checklists from the site's own data, never automated."""

from __future__ import annotations

import re
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from searchos.core.db import get_sessionmaker
from searchos.crawl.urls import match_key
from searchos.devtools.fixture_server import FixtureServer
from searchos.models import Action
from searchos.planner import manual_tasks as mt
from searchos.planner.builders import BUILDERS
from searchos.planner.catalog import ACTION_TYPES, NEVER_AUTOMATED, ActionStatus, RiskClass, may_auto_deploy
from searchos.planner.engine import build_candidates, plan_site, price
from searchos.planner.lifecycle import transition_error
from tests.planner_helpers import ORIGIN, audit, context, gsc_page, page
from tests.test_planner_engine import actions_of, crawled_site

BIG = "Words about espresso machines and grinders. " * 80


def site(**kw: Any) -> Any:
    pages = [
        page("/", title="Beanly", text="Welcome", word_count=50),
        page(
            "/guides/espresso",
            title="Espresso guide",
            h1=("The espresso guide",),
            text=BIG,
            word_count=900,
            inlinks=1,
        ),
        page(
            "/guides/grinders",
            title="Grinders",
            h1=("Choosing a grinder",),
            text=BIG,
            word_count=1200,
            inlinks=6,
        ),
        page("/blog/short", title="Short", text="short", word_count=40),
        page("/contact", title="Contact", text=BIG, word_count=500),
        page("/cart", title="Cart", text=BIG, word_count=500),
    ]
    values: dict[str, Any] = {
        "gsc": {
            match_key(ORIGIN + "/guides/espresso"): gsc_page(
                9000, 300, ("espresso grinder", 5000, 200, 6.0), ("how to pull a shot", 4000, 100, 9.0)
            ),
            match_key(ORIGIN + "/guides/grinders"): gsc_page(1200, 40, ("burr grinder", 1200, 40, 7.0)),
        },
        "has_gsc": True,
        "site_name": "Beanly",
    }
    values.update(kw)
    return context(pages, kw.pop("findings", []), **values)


def checklist(candidate: Any) -> dict[str, Any]:
    value = candidate.proposed_value
    assert value["kind"] == "checklist"
    return value  # type: ignore[no-any-return]


# ---- the facts ---------------------------------------------------------------------------------------------
def test_linkable_assets_are_substantial_pages_ranked_by_search_demand() -> None:
    ctx = site()
    assets = mt.linkable_assets(ctx)
    assert [a.url for a in assets] == [ORIGIN + "/guides/espresso", ORIGIN + "/guides/grinders"]
    # not the homepage, not utility pages, not thin pages
    all_urls = {a.url for a in mt.linkable_assets(ctx, limit=50)}
    assert not {ORIGIN + "/", ORIGIN + "/cart", ORIGIN + "/contact", ORIGIN + "/blog/short"} & all_urls
    assert assets[0].as_dict()["impressions_28d"] == 9000


def test_assets_without_search_data_fall_back_to_length() -> None:
    ctx = site(gsc={}, has_gsc=False)
    assert [a.url for a in mt.linkable_assets(ctx)] == [
        ORIGIN + "/guides/grinders",
        ORIGIN + "/guides/espresso",
    ]


def test_top_queries_are_summed_across_pages() -> None:
    ctx = site()
    top = mt.top_queries(ctx)
    assert top[0] == {"query": "espresso grinder", "impressions_28d": 5000, "clicks_28d": 200}
    assert [q["query"] for q in top] == ["espresso grinder", "how to pull a shot", "burr grinder"]


def test_topic_is_the_best_query_then_the_best_page() -> None:
    assert mt.topic_of(site(), []) == "espresso grinder"
    ctx = site(gsc={}, has_gsc=False)
    assert mt.topic_of(ctx, mt.linkable_assets(ctx)) == "Choosing a grinder"
    assert mt.topic_of(context([], []), []) == "Shop"


# ---- earn_links --------------------------------------------------------------------------------------------
def test_earn_links_names_the_sites_own_pages_and_searches() -> None:
    (c,) = mt.build_earn_links(site(), [])
    value = checklist(c)
    text = " ".join(f"{i['text']} {i.get('detail', '')}" for i in value["items"])
    assert c.type == "earn_links" and c.target_url is None and c.field == "links"
    assert "The espresso guide" in text and "site:shop.example" in text and '"Beanly"' in text
    assert "espresso grinder resources" in text
    assert next(a["url"] for a in c.evidence["assets"]).endswith("/guides/espresso")
    assert c.evidence["inlink_poor_assets"] == [ORIGIN + "/guides/espresso"]


def test_earn_links_without_a_citable_page_says_to_make_one() -> None:
    ctx = context([page("/", word_count=30), page("/a", word_count=60)], [])
    (c,) = mt.build_earn_links(ctx, [])
    first = checklist(c)["items"][0]
    assert first["id"] == "pick-pages" and "Create or choose" in first["text"]


def test_earn_links_never_suggests_buying_or_swapping() -> None:
    (c,) = mt.build_earn_links(site(), [])
    never = " ".join(checklist(c)["never"]).lower()
    assert "buy links" in never and "swap links" in never
    body = " ".join(i["text"] for i in checklist(c)["items"]).lower()
    assert "buy a link" not in body and "paid link" not in body


# ---- author_eeat -------------------------------------------------------------------------------------------
def test_author_task_needs_a_finding_and_lists_the_pages() -> None:
    ctx = site()
    assert mt.build_author_eeat(ctx, []) == []
    urls = [ORIGIN + "/blog/a", ORIGIN + "/blog/b"]
    findings = [audit("AUTHOR_DATE_MISSING", u) for u in urls]
    (c,) = mt.build_author_eeat(ctx, findings)
    assert c.evidence["urls"] == urls and c.evidence["count"] == 2
    ids = [i["id"] for i in checklist(c)["items"]]
    assert ids == [
        "byline-and-date",
        "author-pages",
        "credentials",
        "about-page",
        "contact",
        "sources",
        "markup",
    ]
    assert "Add an About page" in " ".join(
        i["text"] for i in checklist(c)["items"]
    )  # none exists on this site
    assert "Invent" in checklist(c)["never"][0]


def test_author_task_notices_an_existing_about_page() -> None:
    ctx = site()
    ctx.pages[ORIGIN + "/about"] = page("/about", title="About us", text=BIG, word_count=400)
    (c,) = mt.build_author_eeat(ctx, [audit("AUTHOR_DATE_MISSING", ORIGIN + "/blog/a")])
    items = {i["id"]: i for i in checklist(c)["items"]}
    assert items["about-page"]["detail"] == f"Found: {ORIGIN}/about"
    assert items["about-page"]["text"].startswith("Make sure an About page")
    assert c.evidence["about_pages"] == [ORIGIN + "/about"]
    assert items["contact"]["detail"] == "Found: " + ORIGIN + "/contact"


# ---- collect_reviews ---------------------------------------------------------------------------------------
def test_reviews_follow_the_markup_found_on_the_site() -> None:
    ctx = site()
    ctx.pages[ORIGIN + "/p/1"] = page(
        "/p/1", jsonld=({"@type": "Product", "name": "Grinder"}, {"@type": ["Store", "LocalBusiness"]})
    )
    (c,) = mt.build_collect_reviews(ctx, [])
    ids = [i["id"] for i in checklist(c)["items"]]
    assert "business-profile" in ids and "product-reviews" in ids and "pick-platform" not in ids
    assert c.evidence["local_business_markup"] and c.evidence["product_markup"]
    (plain,) = mt.build_collect_reviews(site(), [])
    assert next(i["id"] for i in checklist(plain)["items"]) == "pick-platform"


def test_reviews_are_never_written_or_bought() -> None:
    (c,) = mt.build_collect_reviews(site(), [])
    never = " ".join(checklist(c)["never"]).lower()
    assert "write, buy or edit" in never and "reward" in never
    ask = next(i for i in checklist(c)["items"] if i["id"] == "ask-everyone")
    assert "not only" in ask["detail"]


# ---- digital_pr --------------------------------------------------------------------------------------------
def test_pr_uses_the_queries_people_already_find_the_site_by() -> None:
    (c,) = mt.build_digital_pr(site(), [])
    topic = checklist(c)["items"][0]
    assert '"espresso grinder"' in topic["detail"] and "espresso grinder" in topic["text"]
    assert next(q["query"] for q in c.evidence["queries"]) == "espresso grinder"


def test_pr_without_search_data_starts_from_the_best_page() -> None:
    ctx = site(gsc={}, has_gsc=False)
    (c,) = mt.build_digital_pr(ctx, [])
    assert "Choosing a grinder" in checklist(c)["items"][0]["detail"]


# ---- every task --------------------------------------------------------------------------------------------
def all_candidates() -> list[Any]:
    ctx = site()
    ctx.findings = [audit("AUTHOR_DATE_MISSING", ORIGIN + "/blog/a")]
    return [c for c in build_candidates(ctx) if ACTION_TYPES[c.type].value_kind == "checklist"]


def test_the_engine_generates_all_four_task_types() -> None:
    assert {c.type for c in all_candidates()} == {
        "earn_links",
        "author_eeat",
        "collect_reviews",
        "digital_pr",
    }


def test_manual_tasks_are_manual_unautomatable_and_have_clear_checklists() -> None:
    for c in all_candidates():
        info = ACTION_TYPES[c.type]
        assert info.risk_class is RiskClass.manual and not may_auto_deploy(c.type)
        value = checklist(c)
        assert value["title"] and len(value["intro"]) > 40 and value["never"]
        ids = [i["id"] for i in value["items"]]
        assert 5 <= len(ids) <= 9 and len(ids) == len(set(ids))
        assert all(re.fullmatch(r"[a-z]+(-[a-z]+)*", i) for i in ids)
        for entry in value["items"]:
            assert 25 <= len(entry["text"]) <= 330, entry
            assert entry["text"][0].isupper() and entry["text"].rstrip().endswith((".", ")")), entry
        assert c.evidence["why"] and c.target_url is None
        # a person ticks it off; it is never approved or deployed
        assert transition_error(c.type, ActionStatus.proposed, ActionStatus.approved, has_value=True)
        assert (
            transition_error(c.type, ActionStatus.proposed, ActionStatus.manual_done, has_value=True) is None
        )


def test_no_task_text_promises_results_or_suggests_black_hat_methods() -> None:
    banned = re.compile(r"guarantee|buy (a )?link|paid link|link farm|fake review|spin|cloak", re.I)
    for c in all_candidates():
        value = checklist(c)
        text = " ".join([value["intro"], *(f"{i['text']} {i.get('detail', '')}" for i in value["items"])])
        assert not banned.search(text), (c.type, banned.search(text))


def test_tasks_are_priced_as_priors_and_not_measured() -> None:
    ctx = site()
    for c in all_candidates():
        priced = price(ctx, c)
        assert priced.evidence["gain_basis"] == "prior" and priced.expected_gain > 0
        assert priced.effort == ACTION_TYPES[c.type].effort


def test_without_pages_there_is_nothing_to_write_about() -> None:
    ctx = context([], [])
    assert build_candidates(ctx) == []


def test_every_action_type_has_a_builder() -> None:
    assert set(BUILDERS) == set(ACTION_TYPES)


def test_never_automated_list_holds_for_every_action_type() -> None:
    for action_type in NEVER_AUTOMATED & set(ACTION_TYPES):
        assert ACTION_TYPES[action_type].risk_class is RiskClass.manual
        assert not may_auto_deploy(action_type)
    assert {"earn_links", "author_eeat", "collect_reviews", "digital_pr"} <= {
        t for t, i in ACTION_TYPES.items() if i.risk_class is RiskClass.manual
    }


# ---- with a database --------------------------------------------------------------------------------------
async def test_planning_the_fixture_site_adds_the_manual_tasks_and_keeps_ticks(
    session: AsyncSession, fixture_site: FixtureServer
) -> None:
    site_id = await crawled_site(session, fixture_site)
    await plan_site(get_sessionmaker(), site_id, trigger="test", provider=None)
    tasks = [
        a
        for a in await actions_of(session, site_id)
        if a.type in ("earn_links", "collect_reviews", "digital_pr", "author_eeat")
    ]
    assert {"earn_links", "collect_reviews", "digital_pr"} <= {a.type for a in tasks}
    for a in tasks:
        assert a.risk_class is RiskClass.manual and a.status is ActionStatus.proposed
        assert a.proposed_value["kind"] == "checklist" and a.checklist_done == []
    earn = next(a for a in tasks if a.type == "earn_links")
    earn.checklist_done = ["pick-pages", "log-and-follow-up"]
    earn_id = earn.id
    await session.commit()

    again = await plan_site(get_sessionmaker(), site_id, trigger="test", provider=None)
    assert again["created"] == 0 and again["obsolete"] == 0
    session.expire_all()
    kept = await session.get(Action, earn_id)
    assert kept is not None and kept.checklist_done == ["pick-pages", "log-and-follow-up"]
    count = await session.scalar(
        sa.select(sa.func.count())
        .select_from(Action)
        .where(Action.site_id == site_id, Action.type == "earn_links")
    )
    assert count == 1
