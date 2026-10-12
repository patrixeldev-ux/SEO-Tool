"""Every check has a positive (flagged) and a negative (not flagged) case, plus edge cases."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from searchos.audit.catalog import load_catalog
from searchos.audit.model import AuditInput, PageData, SiteData
from searchos.audit.registry import CheckOutcome, registered_checks

BASE = "https://s.test"
TITLE = "A perfectly reasonable page title here"  # 38 chars
DESC = "A description."


def page(path: str = "/p", **kw: Any) -> PageData:
    url = BASE + path
    defaults: dict[str, Any] = {
        "id": f"id:{path}",
        "url": url,
        "normalised_url": url,
        "status_code": 200,
        "final_url": url,
        "content_type": "text/html",
        "ttfb_ms": 120,
        "title": f"{TITLE} {path}"[:60],
        "meta_description": f"{DESC} {path}",
        "h1": ["Heading"],
        "canonical": url,
        "lang": "en",
        "has_viewport": True,
        "word_count": 400,
        "depth": 1,
        "inlinks_count": 3,
    }
    defaults.update(kw)
    return PageData(**defaults)


def site(**kw: Any) -> SiteData:
    defaults: dict[str, Any] = {
        "start_url": BASE + "/",
        "homepage_url": BASE + "/",
        "robots_status": "ok",
        "robots_text": "User-agent: *\nDisallow: /private/\n",
        "sitemap_found": True,
        "llms_txt_present": True,
    }
    defaults.update(kw)
    return SiteData(**defaults)


def run(check_id: str, pages: list[PageData], site_data: SiteData | None = None) -> CheckOutcome:
    return registered_checks()[check_id].run(AuditInput(pages, site_data or site()))


def failing(check_id: str, pages: list[PageData], site_data: SiteData | None = None) -> set[str]:
    outcome = run(check_id, pages, site_data)
    return {f.page_id or "<site>" for f in outcome.failures}


Case = Callable[[], tuple[list[PageData], SiteData | None]]


def _home(**kw: Any) -> PageData:
    return page("/", **kw)


# check id -> (pages that must FAIL: {id: page}, pages that must PASS)
# Each entry builds a list of pages; the expected failing ids are given explicitly.
POSITIVE: dict[str, tuple[Case, set[str]]] = {
    "HTTP_4XX": (
        lambda: ([page("/a", status_code=404), page("/b", status_code=410)], None),
        {"id:/a", "id:/b"},
    ),
    "HTTP_5XX": (lambda: ([page("/a", status_code=503), page("/b")], None), {"id:/a"}),
    "REDIRECT_CHAIN": (
        lambda: (
            [
                page(
                    "/a",
                    status_code=301,
                    content_type=None,
                    redirect_chain=[
                        {"url": BASE + "/a", "status": 301, "location": BASE + "/b"},
                        {"url": BASE + "/b", "status": 302, "location": BASE + "/c"},
                    ],
                    final_url=BASE + "/c",
                ),
                page("/c"),
            ],
            None,
        ),
        {"id:/a"},
    ),
    "REDIRECT_LOOP": (
        lambda: (
            [
                page(
                    "/a",
                    status_code=301,
                    fetch_error="redirect_loop",
                    redirect_chain=[
                        {"url": BASE + "/a", "status": 301, "location": BASE + "/b"},
                        {"url": BASE + "/b", "status": 301, "location": BASE + "/a"},
                    ],
                )
            ],
            None,
        ),
        {"id:/a"},
    ),
    "BROKEN_INTERNAL_LINK": (
        lambda: (
            [
                page("/a", internal_link_targets=(BASE + "/gone",)),
                page("/gone", status_code=404, content_type=None),
            ],
            None,
        ),
        {"id:/a"},
    ),
    "CANONICAL_MISSING": (
        lambda: ([page("/a", canonical=None), page("/b", canonical="")], None),
        {"id:/a", "id:/b"},
    ),
    "CANONICAL_TO_NON200": (
        lambda: (
            [
                page("/a", canonical=BASE + "/old"),
                page("/old", status_code=301, content_type=None, final_url=BASE + "/new"),
            ],
            None,
        ),
        {"id:/a"},
    ),
    "CANONICAL_MISMATCH": (lambda: ([page("/a", canonical=BASE + "/b"), page("/b")], None), {"id:/a"}),
    "NOINDEX_IN_SITEMAP": (
        lambda: (
            [
                page("/a", in_sitemap=True, robots_meta="noindex, follow"),
                page("/b", in_sitemap=True, x_robots_tag="noindex"),
            ],
            None,
        ),
        {"id:/a", "id:/b"},
    ),
    "TITLE_MISSING": (lambda: ([page("/a", title=None), page("/b", title="  ")], None), {"id:/a", "id:/b"}),
    "TITLE_DUPLICATE": (
        lambda: (
            [
                page("/a", title="Same Title Used Twice On Site!!"),
                page("/b", title="same  title used twice on site!!"),
                page("/c"),
            ],
            None,
        ),
        {"id:/a", "id:/b"},
    ),
    "TITLE_LENGTH": (
        lambda: (
            [
                page("/a", title="Too short"),
                page("/b", title="x" * 61),
                page("/c", title="x" * 30),
                page("/d", title="x" * 60),
            ],
            None,
        ),
        {"id:/a", "id:/b"},
    ),
    "META_DESC_MISSING": (
        lambda: ([page("/a", meta_description=None), page("/b", meta_description="")], None),
        {"id:/a", "id:/b"},
    ),
    "META_DESC_DUPLICATE": (
        lambda: (
            [page("/a", meta_description="Shared."), page("/b", meta_description="shared."), page("/c")],
            None,
        ),
        {"id:/a", "id:/b"},
    ),
    "H1_MISSING": (lambda: ([page("/a", h1=[]), page("/b", h1=["  "])], None), {"id:/a", "id:/b"}),
    "H1_MULTIPLE": (lambda: ([page("/a", h1=["One", "Two"])], None), {"id:/a"}),
    "THIN_CONTENT": (lambda: ([page("/a", word_count=249), page("/b", word_count=250)], None), {"id:/a"}),
    "IMG_ALT_MISSING": (
        lambda: (
            [page("/a", extra={"images": [{"src": "/x.png", "alt": None}, {"src": "/y.png", "alt": "ok"}]})],
            None,
        ),
        {"id:/a"},
    ),
    "VIEWPORT_MISSING": (lambda: ([page("/a", has_viewport=False)], None), {"id:/a"}),
    "LANG_MISSING": (lambda: ([page("/a", lang=None), page("/b", lang="")], None), {"id:/a", "id:/b"}),
    "HREFLANG_INVALID": (
        lambda: (
            [
                page("/a", hreflang=[{"lang": "english", "href": BASE + "/a"}]),
                page("/b", hreflang=[{"lang": "en", "href": "/relative"}]),
                page(
                    "/c", hreflang=[{"lang": "en", "href": BASE + "/c"}, {"lang": "EN", "href": BASE + "/c2"}]
                ),
            ],
            None,
        ),
        {"id:/a", "id:/b", "id:/c"},
    ),
    "MIXED_CONTENT": (
        lambda: ([page("/a", extra={"http_resources": ["http://x.test/a.png"]})], None),
        {"id:/a"},
    ),
    "TTFB_SLOW": (lambda: ([page("/a", ttfb_ms=801), page("/b", ttfb_ms=800)], None), {"id:/a"}),
    "CWV_LCP_POOR": (
        lambda: (
            [
                page("/a", extra={"psi": {"lcp_ms": 4001, "lcp_source": "field"}}),
                page(
                    "/b", extra={"psi": {"lcp_ms": 4000, "lcp_source": "field"}}
                ),  # 4000 is still "needs improvement"
                page("/c", extra={"psi": {"lcp_ms": 9000, "lcp_source": "lab"}}),
            ],
            None,
        ),
        {"id:/a", "id:/c"},
    ),
    "CWV_INP_POOR": (
        lambda: (
            [
                page("/a", extra={"psi": {"inp_ms": 501, "inp_source": "field"}}),
                page("/b", extra={"psi": {"inp_ms": 500, "inp_source": "field"}}),
            ],
            None,
        ),
        {"id:/a"},
    ),
    "CWV_CLS_POOR": (
        lambda: (
            [
                page("/a", extra={"psi": {"cls": 0.26, "cls_source": "field"}}),
                page("/b", extra={"psi": {"cls": 0.25, "cls_source": "field"}}),
            ],
            None,
        ),
        {"id:/a"},
    ),
    "SCHEMA_PARSE_ERROR": (
        lambda: (
            [page("/a", structured_data={"jsonld_errors": ["Expecting value (line 1, column 2)"]})],
            None,
        ),
        {"id:/a"},
    ),
    "SCHEMA_REQUIRED_PROP_MISSING": (
        lambda: (
            [
                page("/a", structured_data={"jsonld": [{"@type": "Product", "name": "Mug"}]}),
                page("/b", structured_data={"jsonld": [{"@type": "Organization", "name": "Acme"}]}),
                page(
                    "/c",
                    structured_data={"jsonld": [{"@type": "BlogPosting", "headline": "H", "author": "A"}]},
                ),
                page("/d", structured_data={"microdata": [{"@type": ["LocalBusiness"], "name": "Shop"}]}),
            ],
            None,
        ),
        {"id:/a", "id:/b", "id:/c", "id:/d"},
    ),
    "ANSWER_BLOCK_MISSING": (
        lambda: (
            [
                page(
                    "/a",
                    extra={"question_headings": [{"text": "Why?", "answered": False, "answer_words": 120}]},
                )
            ],
            None,
        ),
        {"id:/a"},
    ),
    "AUTHOR_DATE_MISSING": (
        lambda: (
            [
                page("/a", extra={"is_article": True, "has_author": False, "has_date": True}),
                page("/b", extra={"is_article": True, "has_author": True, "has_date": False}),
            ],
            None,
        ),
        {"id:/a", "id:/b"},
    ),
    "HTTPS_MISSING": (lambda: ([_home()], site(homepage_url="http://s.test/")), {"<site>"}),
    "SITEMAP_MISSING": (lambda: ([_home()], site(sitemap_found=False)), {"<site>"}),
    "ROBOTS_MISSING": (lambda: ([_home()], site(robots_status="missing", robots_text=None)), {"<site>"}),
    "BLOCKED_IN_SITEMAP": (
        lambda: (
            [
                page(
                    "/private/x",
                    in_sitemap=True,
                    status_code=None,
                    fetch_error="blocked_by_robots",
                    content_type=None,
                ),
                page("/ok", in_sitemap=True),
            ],
            None,
        ),
        {"id:/private/x"},
    ),
    "ORPHAN_PAGE": (
        lambda: ([page("/a", in_sitemap=True, inlinks_count=0), page("/b", in_sitemap=True)], None),
        {"id:/a"},
    ),
    "DEEP_PAGE": (
        lambda: ([page("/a", depth=5), page("/b", depth=4), page("/c", depth=None)], None),
        {"id:/a"},
    ),
    "ORG_SCHEMA_MISSING": (
        lambda: ([_home(structured_data={"jsonld": [{"@type": "WebSite", "name": "S"}]})], None),
        {"id:/"},
    ),
    "AI_SEARCH_BOT_BLOCKED": (
        lambda: ([_home()], site(robots_text="User-agent: PerplexityBot\nDisallow: /\n")),
        {"<site>"},
    ),
    "AI_TRAINING_BOT_BLOCKED": (
        lambda: ([_home()], site(robots_text="User-agent: GPTBot\nDisallow: /\n")),
        {"<site>"},
    ),
    "LLMS_TXT": (lambda: ([_home()], site(llms_txt_present=False)), {"<site>"}),
    "JS_DEPENDENT_CONTENT": (
        lambda: (
            [
                page("/a", word_count=20, extra={"rendered_word_count": 400}),
                page("/b", word_count=199, extra={"rendered_word_count": 400}),
                page("/c", word_count=200, extra={"rendered_word_count": 400}),
            ],
            None,
        ),
        {"id:/a", "id:/b"},
    ),
}

# Pages/sites on which the same check must report nothing. `None` = every page built by `page()` defaults.
NEGATIVE: dict[str, Callable[[], tuple[list[PageData], SiteData | None]]] = {
    "HTTP_4XX": lambda: ([page("/a"), page("/b", status_code=500)], None),
    "HTTP_5XX": lambda: ([page("/a"), page("/b", status_code=404)], None),
    "REDIRECT_CHAIN": lambda: (
        [
            page(
                "/a",
                status_code=301,
                content_type=None,
                redirect_chain=[{"url": BASE + "/a", "status": 301, "location": BASE + "/b"}],
                final_url=BASE + "/b",
            ),
            page("/b"),
        ],
        None,
    ),
    "REDIRECT_LOOP": lambda: ([page("/a")], None),
    "BROKEN_INTERNAL_LINK": lambda: (
        [
            page("/a", internal_link_targets=(BASE + "/b", BASE + "/old", BASE + "/never-crawled")),
            page("/b"),
            page(
                "/old",
                status_code=301,
                content_type=None,
                final_url=BASE + "/b",
                redirect_chain=[{"url": BASE + "/old", "status": 301, "location": BASE + "/b"}],
            ),
        ],
        None,
    ),
    "CANONICAL_MISSING": lambda: ([page("/a")], None),
    "CANONICAL_TO_NON200": lambda: (
        [
            page("/a", canonical=BASE + "/b"),
            page("/b"),
            page("/c", canonical="https://other.example/x"),
            page("/d", canonical=BASE + "/uncrawled"),
        ],
        None,
    ),
    "CANONICAL_MISMATCH": lambda: ([page("/a"), page("/b", canonical="https://other.example/b")], None),
    "NOINDEX_IN_SITEMAP": lambda: (
        [
            page("/a", in_sitemap=True),
            page("/b", robots_meta="noindex"),
            page("/c", in_sitemap=True, robots_meta="index, follow"),
        ],
        None,
    ),
    "TITLE_MISSING": lambda: ([page("/a")], None),
    "TITLE_DUPLICATE": lambda: (
        [
            page("/a"),
            page("/b"),
            page("/c", title="Shared Title For Canonicalised Copy!!"),
            page("/d", title="Shared Title For Canonicalised Copy!!", canonical=BASE + "/c"),
            page("/e", title="Shared Title On A Noindex Page Here!", robots_meta="noindex"),
            page("/f", title="Shared Title On A Noindex Page Here!"),
        ],
        None,
    ),
    "TITLE_LENGTH": lambda: (
        [page("/a", title="x" * 30), page("/b", title="x" * 60), page("/c", title=None)],
        None,
    ),
    "META_DESC_MISSING": lambda: ([page("/a")], None),
    "META_DESC_DUPLICATE": lambda: (
        [page("/a"), page("/b"), page("/c", meta_description=None), page("/d", meta_description=None)],
        None,
    ),
    "H1_MISSING": lambda: ([page("/a", h1=["x"])], None),
    "H1_MULTIPLE": lambda: ([page("/a", h1=["One"])], None),
    "THIN_CONTENT": lambda: (
        [page("/a", word_count=250), page("/b", word_count=10, robots_meta="noindex")],
        None,
    ),
    "IMG_ALT_MISSING": lambda: (
        [
            page("/a", extra={"images": [{"src": "/x.png", "alt": ""}, {"src": "/y.png", "alt": "A mug"}]}),
            page("/b"),
        ],
        None,
    ),
    "VIEWPORT_MISSING": lambda: ([page("/a")], None),
    "LANG_MISSING": lambda: ([page("/a")], None),
    "HREFLANG_INVALID": lambda: (
        [
            page(
                "/a",
                hreflang=[
                    {"lang": "en", "href": BASE + "/a"},
                    {"lang": "en-GB", "href": BASE + "/uk"},
                    {"lang": ["zh-Hant", "TW"][0], "href": BASE + "/zh"},
                    {"lang": "es-419", "href": BASE + "/es"},
                    {"lang": "x-default", "href": BASE + "/"},
                ],
            ),
            page("/b"),
        ],
        None,
    ),
    "MIXED_CONTENT": lambda: (
        [
            page("/a", extra={"http_resources": []}),
            page(
                "/http-page",
                extra={"http_resources": ["http://x.test/a.png"]},
                url="http://s.test/http-page",
                normalised_url="http://s.test/http-page",
            ),
        ],
        None,
    ),
    "TTFB_SLOW": lambda: ([page("/a", ttfb_ms=800), page("/b", ttfb_ms=None)], None),
    "CWV_LCP_POOR": lambda: (
        [page("/a", extra={"psi": {"lcp_ms": 2400, "lcp_source": "field"}}), page("/b")],  # /b: no PSI data
        None,
    ),
    "CWV_INP_POOR": lambda: (
        [page("/a", extra={"psi": {"inp_ms": 120, "inp_source": "field"}}), page("/b")],
        None,
    ),
    "CWV_CLS_POOR": lambda: (
        [page("/a", extra={"psi": {"cls": 0.02, "cls_source": "field"}}), page("/b")],
        None,
    ),
    "SCHEMA_PARSE_ERROR": lambda: (
        [page("/a", structured_data={"jsonld": [{"@type": "Thing"}], "jsonld_errors": []})],
        None,
    ),
    "SCHEMA_REQUIRED_PROP_MISSING": lambda: (
        [
            page(
                "/a",
                structured_data={
                    "jsonld": [
                        {"@type": "Product", "name": "Mug", "aggregateRating": {"ratingValue": 5}},
                        {"@type": "Organization", "name": "Acme", "url": "https://acme.test"},
                        {
                            "@type": ["Article", "NewsArticle"],
                            "headline": "H",
                            "author": {"name": "A"},
                            "datePublished": "2026-01-01",
                        },
                        {"@type": "CafeOrCoffeeShop", "name": "Cafe", "address": {"streetAddress": "1 Quay"}},
                        {"@type": "FAQPage", "mainEntity": [{"@type": "Question"}]},
                        {"@type": "BreadcrumbList", "itemListElement": [{"@type": "ListItem"}]},
                        {"@type": "SomeUnknownType"},
                    ]
                },
            )
        ],
        None,
    ),
    "ANSWER_BLOCK_MISSING": lambda: (
        [
            page("/a", extra={"question_headings": [{"text": "Why?", "answered": True, "answer_words": 30}]}),
            page("/b"),
        ],
        None,
    ),
    "AUTHOR_DATE_MISSING": lambda: (
        [
            page("/a", extra={"is_article": True, "has_author": True, "has_date": True}),
            page("/b", extra={"is_article": False, "has_author": False, "has_date": False}),
        ],
        None,
    ),
    "HTTPS_MISSING": lambda: ([_home()], site(homepage_url="https://s.test/")),
    "SITEMAP_MISSING": lambda: ([_home()], site(sitemap_found=True)),
    "ROBOTS_MISSING": lambda: ([_home()], site(robots_status="ok")),
    "BLOCKED_IN_SITEMAP": lambda: (
        [page("/ok", in_sitemap=True), page("/private/x", in_sitemap=False)],
        site(robots_text="User-agent: *\nDisallow: /private/\nAllow: /private/public\n"),
    ),
    "ORPHAN_PAGE": lambda: (
        [
            page("/", in_sitemap=True, inlinks_count=0),
            page("/a", in_sitemap=True),
            page("/b", inlinks_count=0),
        ],
        None,
    ),
    "DEEP_PAGE": lambda: ([page("/a", depth=0), page("/b", depth=4)], None),
    "ORG_SCHEMA_MISSING": lambda: (
        [_home(structured_data={"jsonld": [{"@type": "LocalBusiness", "name": "S"}]})],
        None,
    ),
    "AI_SEARCH_BOT_BLOCKED": lambda: (
        [_home()],
        site(robots_text="User-agent: GPTBot\nDisallow: /\n\nUser-agent: *\nDisallow: /private/\n"),
    ),
    "AI_TRAINING_BOT_BLOCKED": lambda: (
        [_home()],
        site(robots_text="User-agent: PerplexityBot\nDisallow: /\n"),
    ),
    "LLMS_TXT": lambda: ([_home()], site(llms_txt_present=True)),
    "JS_DEPENDENT_CONTENT": lambda: (
        [
            page("/a", word_count=380, extra={"rendered_word_count": 400}),
            page("/b", word_count=0, extra={"rendered_word_count": 49}),
            page("/c"),
        ],
        None,
    ),
}


def test_every_registered_check_has_a_positive_and_a_negative_case() -> None:
    ids = set(registered_checks())
    assert ids == set(POSITIVE), ids ^ set(POSITIVE)
    assert ids == set(NEGATIVE), ids ^ set(NEGATIVE)


@pytest.mark.parametrize("check_id", sorted(POSITIVE))
def test_check_flags_the_defect(check_id: str) -> None:
    build, expected = POSITIVE[check_id]
    pages, site_data = build()
    assert failing(check_id, pages, site_data) == expected


@pytest.mark.parametrize("check_id", sorted(NEGATIVE))
def test_check_leaves_good_pages_alone(check_id: str) -> None:
    pages, site_data = NEGATIVE[check_id]()
    assert failing(check_id, pages, site_data) == set()
    outcome = run(check_id, pages, site_data)
    assert outcome.applicable >= 0


# ---- catalog ---------------------------------------------------------------------------------------
def test_catalog_loads_and_every_registered_check_has_an_entry() -> None:
    catalog = load_catalog()
    registered = set(registered_checks())
    assert registered <= set(catalog), registered - set(catalog)
    assert set(catalog) <= registered, f"catalog entries without a check: {set(catalog) - registered}"
    assert len(catalog) == 41


def test_catalog_applies_to_matches_the_registry() -> None:
    catalog = load_catalog()
    for check_id, reg in registered_checks().items():
        assert catalog[check_id].applies_to == reg.applies_to, check_id


def test_catalog_text_is_filled_in() -> None:
    for info in load_catalog().values():
        assert len(info.title) > 8 and len(info.why_it_matters) > 40 and len(info.how_to_fix) > 20, info.id


def test_info_checks_have_zero_weight_and_llms_txt_is_info() -> None:
    catalog = load_catalog()
    assert catalog["LLMS_TXT"].severity == "info" and catalog["LLMS_TXT"].weight == 0
    assert catalog["AI_TRAINING_BOT_BLOCKED"].severity == "info"
    assert catalog["AI_SEARCH_BOT_BLOCKED"].severity == "high"


# ---- edge cases ------------------------------------------------------------------------------------
def test_not_applicable_when_nothing_to_evaluate() -> None:
    assert run("AUTHOR_DATE_MISSING", [page("/a")]).applicable == 0
    assert run("JS_DEPENDENT_CONTENT", [page("/a")]).applicable == 0
    assert run("LLMS_TXT", [_home()], site(llms_txt_present=None)).applicable == 0
    assert run("HREFLANG_INVALID", [page("/a")]).applicable == 0
    not_loaded = [_home(status_code=500, content_type=None)]
    assert run("ORG_SCHEMA_MISSING", not_loaded).applicable == 0


def test_blocked_robots_stub_is_not_judged_as_a_page() -> None:
    stub = page(
        "/private/x", in_sitemap=True, status_code=None, fetch_error="blocked_by_robots", content_type=None
    )
    for check_id in (
        "TITLE_MISSING",
        "H1_MISSING",
        "HTTP_4XX",
        "REDIRECT_CHAIN",
        "THIN_CONTENT",
        "ORPHAN_PAGE",
    ):
        assert run(check_id, [stub]).applicable == 0, check_id


def test_noindex_pages_are_excluded_from_on_page_checks() -> None:
    hidden = page("/hidden", title=None, h1=[], word_count=5, robots_meta="NOINDEX")
    for check_id in ("TITLE_MISSING", "H1_MISSING", "THIN_CONTENT", "META_DESC_MISSING"):
        assert run(check_id, [hidden]).applicable == 0
    assert failing("TITLE_MISSING", [page("/x", title=None, x_robots_tag="googlebot: noindex")]) == set()


def test_duplicate_details_name_the_other_pages() -> None:
    pages = [
        page("/a", title="Same Title Used Twice On Site!!"),
        page("/b", title="Same Title Used Twice On Site!!"),
    ]
    outcome = run("TITLE_DUPLICATE", pages)
    assert outcome.applicable == 2
    assert outcome.failures[0].details["also_on"] == [BASE + "/b"]


def test_broken_link_details_list_each_broken_target() -> None:
    pages = [
        page("/a", internal_link_targets=(BASE + "/x", BASE + "/y", BASE + "/x")),
        page("/x", status_code=404, content_type=None),
        page("/y", status_code=500, content_type=None),
    ]
    details = run("BROKEN_INTERNAL_LINK", pages).failures[0].details
    assert details["count"] == 2
    assert {link["status"] for link in details["links"]} == {404, 500}


def test_redirect_to_a_missing_page_counts_as_a_broken_link() -> None:
    pages = [
        page("/a", internal_link_targets=(BASE + "/old",)),
        page(
            "/old",
            status_code=301,
            content_type=None,
            final_url=BASE + "/gone",
            redirect_chain=[{"url": BASE + "/old", "status": 301, "location": BASE + "/gone"}],
        ),
        page("/gone", status_code=404, content_type=None),
    ]
    assert failing("BROKEN_INTERNAL_LINK", pages) == {"id:/a"}


def test_ai_bot_details_name_the_blocked_bots() -> None:
    robots = "User-agent: OAI-SearchBot\nDisallow: /\n\nUser-agent: ChatGPT-User\nDisallow: /\n\nUser-agent: ClaudeBot\nDisallow: /\n"
    outcome = run("AI_SEARCH_BOT_BLOCKED", [_home()], site(robots_text=robots))
    assert {b["name"] for b in outcome.failures[0].details["bots"]} == {"OAI-SearchBot", "ChatGPT-User"}
    training = run("AI_TRAINING_BOT_BLOCKED", [_home()], site(robots_text=robots))
    assert [b["name"] for b in training.failures[0].details["bots"]] == ["ClaudeBot"]


def test_wildcard_group_blocks_every_ai_bot_but_a_named_group_overrides_it() -> None:
    everything = "User-agent: *\nDisallow: /\n"
    assert (
        len(run("AI_SEARCH_BOT_BLOCKED", [_home()], site(robots_text=everything)).failures[0].details["bots"])
        == 7
    )
    named_allow = "User-agent: *\nDisallow: /\n\nUser-agent: OAI-SearchBot\nAllow: /\n"
    bots = run("AI_SEARCH_BOT_BLOCKED", [_home()], site(robots_text=named_allow)).failures[0].details["bots"]
    assert "OAI-SearchBot" not in {b["name"] for b in bots}


def test_partial_disallow_does_not_count_as_blocking_a_bot() -> None:
    robots = "User-agent: GPTBot\nDisallow: /private/\n"
    assert failing("AI_TRAINING_BOT_BLOCKED", [_home()], site(robots_text=robots)) == set()


def test_missing_robots_blocks_no_bot() -> None:
    assert (
        failing("AI_SEARCH_BOT_BLOCKED", [_home()], site(robots_status="missing", robots_text=None)) == set()
    )


def test_blocked_in_sitemap_understands_wildcards_and_anchors() -> None:
    robots = "User-agent: *\nDisallow: /*.pdf$\nDisallow: /scratch*\n"
    pages = [
        page("/a.pdf", in_sitemap=True),
        page("/a.pdf?x=1", in_sitemap=True),
        page("/scratchfile", in_sitemap=True),
        page("/ok", in_sitemap=True),
    ]
    assert failing("BLOCKED_IN_SITEMAP", pages, site(robots_text=robots)) == {"id:/a.pdf", "id:/scratchfile"}


def test_schema_details_say_what_is_missing() -> None:
    outcome = run(
        "SCHEMA_REQUIRED_PROP_MISSING",
        [page("/a", structured_data={"jsonld": [{"@type": "Product", "name": "Mug"}]})],
    )
    assert outcome.failures[0].details["problems"] == [
        {"type": "Product", "missing": ["offers or review or aggregateRating"]}
    ]


def test_microdata_is_checked_too() -> None:
    p = page(
        "/a", structured_data={"microdata": [{"@type": ["Product"], "name": "Mug", "offers": {"price": "5"}}]}
    )
    assert failing("SCHEMA_REQUIRED_PROP_MISSING", [p]) == set()


def test_js_check_needs_a_meaningful_amount_of_rendered_text() -> None:
    tiny = page("/a", word_count=0, extra={"rendered_word_count": 49})
    assert failing("JS_DEPENDENT_CONTENT", [tiny]) == set()
    boundary = page("/b", word_count=25, extra={"rendered_word_count": 50})
    assert failing("JS_DEPENDENT_CONTENT", [boundary]) == set()  # exactly 50% is not "under half"
    assert failing(
        "JS_DEPENDENT_CONTENT", [page("/c", word_count=24, extra={"rendered_word_count": 50})]
    ) == {"id:/c"}


def test_orphan_check_ignores_the_homepage() -> None:
    pages = [page("/", in_sitemap=True, inlinks_count=0), page("/a", in_sitemap=True, inlinks_count=0)]
    assert failing("ORPHAN_PAGE", pages) == {"id:/a"}


def test_cwv_checks_are_not_run_without_pagespeed_data_and_never_use_lab_inp() -> None:
    pages = [page("/a"), page("/b", extra={"psi": {"lcp_ms": 1000, "lcp_source": "lab", "inp_ms": None}})]
    for check_id in ("CWV_LCP_POOR", "CWV_INP_POOR", "CWV_CLS_POOR"):
        outcome = run(check_id, [page("/a")], None)
        assert outcome.applicable == 0 and not outcome.failures
    # A page with only lab LCP is evaluated for LCP but not for INP or CLS.
    assert run("CWV_LCP_POOR", pages, None).applicable == 1
    assert run("CWV_INP_POOR", pages, None).applicable == 0
    assert run("CWV_CLS_POOR", pages, None).applicable == 0


def test_cwv_checks_skip_non_indexable_pages() -> None:
    noindex = page("/a", robots_meta="noindex", extra={"psi": {"lcp_ms": 9000, "lcp_source": "field"}})
    assert run("CWV_LCP_POOR", [noindex], None).applicable == 0


def test_cwv_issue_details_carry_the_value_the_limit_and_the_source() -> None:
    outcome = run(
        "CWV_LCP_POOR", [page("/a", extra={"psi": {"lcp_ms": 5200.0, "lcp_source": "field"}})], None
    )
    assert outcome.failures[0].details == {"lcp_ms": 5200.0, "limit": 4000, "source": "field"}
