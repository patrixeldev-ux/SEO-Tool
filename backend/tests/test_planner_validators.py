"""3.3: the hard validators. Every rule has an accepting and a rejecting case, and the boundaries are exact."""

from __future__ import annotations

import json
from typing import Any

import pytest

from searchos.planner import validators as v

GOOD_TITLE = "Pour Over Coffee Guide for Beginners"  # 37 chars


# ---- helpers -------------------------------------------------------------------------------------
def test_squash_and_norm_key() -> None:
    assert v.squash("  a \n b\t c  ") == "a b c"
    assert v.norm_key("  Hello   WORLD ") == "hello world"


# ---- titles --------------------------------------------------------------------------------------
@pytest.mark.parametrize(("n", "ok"), [(29, False), (30, True), (45, True), (60, True), (61, False)])
def test_title_length_boundaries(n: int, ok: bool) -> None:
    title = ("Alpha Beta Gamma Delta Epsilon Zeta Eta Theta Iota Kappa Lambda Mu Nu Xi Omicron" * 2)[
        :n
    ].rstrip()
    title = title.ljust(n, "x")  # exactly n characters
    assert len(title) == n
    problems = v.validate_title(title)
    assert (not [p for p in problems if "characters" in p]) is ok


def test_title_accepts_a_good_one_and_counts_after_collapsing_spaces() -> None:
    assert v.validate_title(GOOD_TITLE) == []
    assert v.validate_title("  Pour   Over   Coffee   Guide   for   Beginners  ") == []


@pytest.mark.parametrize("bad", [None, "", "   ", 12, ["x"]])
def test_title_rejects_empty_or_non_text(bad: Any) -> None:
    assert v.validate_title(bad) == ["The title is empty."]


def test_title_all_caps() -> None:
    assert any("capital" in p for p in v.validate_title("BEST POUR OVER COFFEE GUIDE FOR BEGINNERS"))
    assert any(
        "capital" in p for p in v.validate_title("Learn BREWING COFFEE METHODS and tips today")
    )  # 3 shouted words
    assert v.validate_title("Pour Over Coffee Guide for SEO and NASA Fans") == []  # short acronyms are fine
    assert (
        v.validate_title("Learn to brew BEST COFFEE at home with our guide") == []
    )  # two shouted words only


def test_title_keyword_stuffing() -> None:
    stuffed = "Coffee Beans: Buy Coffee Beans, Best Coffee Beans Online"  # "coffee" x3
    assert any('"coffee"' in p for p in v.validate_title(stuffed))
    twice = "Coffee Beans and Coffee Grinders for Every Kitchen"  # twice is allowed
    assert v.validate_title(twice) == []
    short_words = "Go to it: go to it, go to it, do it now please yes"  # words under 3 letters are ignored
    assert not any("stuffing" in p for p in v.validate_title(short_words))


def test_title_must_be_unique_and_different_from_current() -> None:
    taken = {v.norm_key(GOOD_TITLE)}
    assert any(
        "unique" in p for p in v.validate_title("  pour over coffee GUIDE for beginners ", taken=taken)
    )
    assert v.validate_title("A Different Title That Is Long Enough", taken=taken) == []
    assert any("same as the current" in p for p in v.validate_title(GOOD_TITLE, current=GOOD_TITLE.lower()))


def test_title_must_be_one_line_of_plain_text() -> None:
    assert any("HTML" in p for p in v.validate_title("<b>Pour Over Coffee Guide</b> for Beginners"))
    assert any("single line" in p for p in v.validate_title("Pour Over Coffee Guide\nfor Beginners and more"))
    assert any("single line" in p for p in v.validate_title("Pour Over Coffee\x00 Guide for Beginners"))


# ---- meta descriptions ---------------------------------------------------------------------------
def _meta(n: int) -> str:
    base = "Learn how to brew a better cup of coffee at home with simple steps and clear advice. "
    return (base * 4)[:n].rstrip().ljust(n, "x")


@pytest.mark.parametrize(("n", "ok"), [(119, False), (120, True), (140, True), (155, True), (156, False)])
def test_meta_length_boundaries(n: int, ok: bool) -> None:
    text = _meta(n)
    assert len(text) == n
    assert (not [p for p in v.validate_meta(text) if "characters" in p]) is ok


def test_meta_other_rules() -> None:
    good = _meta(140)
    assert v.validate_meta(good) == []
    assert v.validate_meta("") == ["The description is empty."]
    assert any("unique" in p for p in v.validate_meta(good, taken={v.norm_key(good)}))
    assert any("same as the current" in p for p in v.validate_meta(good, current=good))
    assert any("capital" in p for p in v.validate_meta(good.upper()))
    assert any("HTML" in p for p in v.validate_meta(good.replace("coffee", "<i>coffee</i>")))


# ---- alt text ------------------------------------------------------------------------------------
@pytest.mark.parametrize(("n", "ok"), [(4, False), (5, True), (60, True), (125, True), (126, False)])
def test_alt_length_boundaries(n: int, ok: bool) -> None:
    text = ("Roasted coffee beans in a tray " * 6)[:n].rstrip().ljust(n, "x")
    assert (not [p for p in v.validate_alt(text) if "characters" in p]) is ok


@pytest.mark.parametrize(
    "alt", ["Image of coffee beans", "picture of a cup", "  IMAGE OF a grinder", "Image  of  beans"]
)
def test_alt_must_not_start_with_image_of(alt: str) -> None:
    assert any('"image of"' in p for p in v.validate_alt(alt))


def test_alt_accepts_words_that_only_contain_image() -> None:
    assert v.validate_alt("Imagery of roasting: beans cooling in a copper tray") == []
    assert v.validate_alt("Coffee beans in an image gallery frame") == []


def test_alt_must_not_be_the_filename() -> None:
    for alt in ("beans.jpg", "BEANS.JPG", "beans"):
        assert any("file name" in p for p in v.validate_alt(alt, filename="beans.jpg")), alt
    assert any("file name" in p for p in v.validate_alt("dark roast", filename="dark-roast.png"))
    assert any("file name" in p for p in v.validate_alt("dark roast", filename="/img/dark_roast.png"))
    assert v.validate_alt("Dark roast beans in a bowl", filename="dark-roast.png") == []
    assert v.validate_alt("Roasted beans", filename="") == []


def test_alt_empty_and_markup() -> None:
    assert v.validate_alt("") == ["The alt text is empty."]
    assert v.validate_alt(None) == ["The alt text is empty."]
    assert any("HTML" in p for p in v.validate_alt("<img> beans in a tray"))


# ---- JSON-LD -------------------------------------------------------------------------------------
PAGE = "Harbor & Pine Coffee Roasters. By Mara Ellison, published 2026-03-14. A pour over brewing guide for 2 people."
HOSTS = {"shop.example", "www.shop.example"}


def org(**kw: Any) -> dict[str, Any]:
    entity: dict[str, Any] = {
        "@context": "https://schema.org",
        "@type": "Organization",
        "name": "Harbor & Pine Coffee Roasters",
        "url": "https://shop.example/",
    }
    entity.update(kw)
    return entity


def check(raw: Any, expected: str = "Organization", **kw: Any) -> tuple[dict[str, Any] | None, list[str]]:
    return v.validate_jsonld(raw, expected_type=expected, ground=[PAGE], hosts=HOSTS, **kw)


def test_jsonld_accepts_a_grounded_organization_as_object_or_text() -> None:
    entity, problems = check(org())
    assert problems == [] and entity is not None
    assert check(json.dumps(org()))[1] == []
    assert check([org()])[1] == []  # a one-element list is unwrapped


def test_jsonld_must_parse_and_be_an_object() -> None:
    assert "not valid JSON" in check("{not json")[1][0]
    assert check(json.dumps([org(), org()]))[1] == ["The JSON-LD must be a single JSON object."]
    assert check("42")[1] == ["The JSON-LD must be a single JSON object."]


@pytest.mark.parametrize(
    ("context", "ok"),
    [
        ("https://schema.org", True),
        ("http://schema.org/", True),
        (["https://schema.org", {"x": "y"}], True),
        ("https://example.com", False),
        (None, False),
        (7, False),
    ],
)
def test_jsonld_context(context: Any, ok: bool) -> None:
    entity = org()
    entity["@context"] = context
    problems = check(entity)[1]
    assert (not any("@context" in p for p in problems)) is ok


def test_jsonld_type_must_be_supported_and_expected() -> None:
    assert any("not one we generate" in p for p in check(org(**{"@type": "Recipe"}))[1])
    assert any(
        "@type" in p and "missing" in p for p in check({"@context": "https://schema.org", "name": "x"})[1]
    )
    assert any("must be Article" in p for p in check(org(), expected="Article")[1])
    # a sub-type of the expected family is fine
    corp = org(**{"@type": "Corporation"})
    assert check(corp, expected="Organization")[1] == []


def test_jsonld_required_properties_come_from_our_table() -> None:
    assert any(
        "name" in p and "required" in p.lower()
        for p in check({k: v_ for k, v_ in org().items() if k != "name"})[1]
    )
    assert any("url" in p for p in check({k: v_ for k, v_ in org().items() if k != "url"})[1])
    art = {"@context": "https://schema.org", "@type": "Article", "headline": "A pour over brewing guide"}
    missing = [p for p in check(art, expected="Article")[1] if "Required" in p]
    assert missing and "author" in missing[0] and "datePublished" in missing[0]
    full = {**art, "author": {"@type": "Person", "name": "Mara Ellison"}, "datePublished": "2026-03-14"}
    assert check(full, expected="Article")[1] == []
    page = {"@context": "https://schema.org", "@type": "WebPage", "name": "A pour over brewing guide"}
    assert any("url" in p for p in check(page, expected="WebPage")[1])


def test_jsonld_never_invents_values() -> None:
    entity, problems = check(org(name="Totally Different Roasters Ltd"))
    assert entity is not None and any("does not appear on the page" in p for p in problems)
    assert any("telephone" in p for p in check(org(telephone="+44 20 7946 0000"))[1])
    # urls on the site are fine, other hosts must be on the page
    assert check(org(logo="https://shop.example/logo.png"))[1] == []
    assert any("sameAs" in p for p in check(org(sameAs=["https://facebook.com/x"]))[1])
    # numbers must be on the page too
    assert check(org(numberOfEmployees=2))[1] == []
    assert any("numberOfEmployees" in p for p in check(org(numberOfEmployees=250))[1])


def test_jsonld_reviews_and_ratings_are_never_added() -> None:
    for key in ("aggregateRating", "review", "ratingValue", "reviewCount"):
        problems = check(org(**{key: {"ratingValue": 2}} if key != "ratingValue" else {key: 2}))[1]
        assert any("never generated" in p for p in problems), key
    existing = org(aggregateRating={"@type": "AggregateRating", "ratingValue": 4.5, "reviewCount": 10})
    # already there (the site owner's own markup): kept, not questioned
    assert check(dict(existing), existing=existing)[1] == []


def test_jsonld_existing_markup_is_not_regrounded_and_type_cannot_change() -> None:
    existing = org(telephone="+44 20 7946 0000")
    proposed = org(telephone="+44 20 7946 0000", description="Harbor & Pine Coffee Roasters")
    assert check(proposed, existing=existing)[1] == []
    changed = org(**{"@type": "Corporation"})
    assert any("type of existing markup" in p for p in check(changed, existing=org())[1])


def test_jsonld_dates_must_be_on_the_page() -> None:
    art = {
        "@context": "https://schema.org",
        "@type": "Article",
        "headline": "A pour over brewing guide",
        "author": {"@type": "Person", "name": "Mara Ellison"},
        "datePublished": "2026-03-14",
    }
    assert check(art, expected="Article")[1] == []
    art["datePublished"] = "2024-01-01"
    assert any("datePublished" in p for p in check(art, expected="Article")[1])


# ---- internal links ------------------------------------------------------------------------------
TARGET = v.LinkTarget("https://shop.example/guide", 200, True)
SOURCE_TEXT = "Our pour over method needs a steady hand. Read the coffee grinder guide before you start."


def link(**kw: Any) -> list[str]:
    args: dict[str, Any] = {
        "source_url": "https://shop.example/blog",
        "source_text": SOURCE_TEXT,
        "target": TARGET,
        "already_linked": (),
        "anchor": "coffee grinder guide",
    }
    args.update(kw)
    return v.validate_internal_link(**args)


def test_link_accepts_a_good_one() -> None:
    assert link() == []
    assert link(anchor="Coffee Grinder  Guide") == []  # case and spaces do not matter


def test_link_target_must_be_a_healthy_indexable_page() -> None:
    assert link(target=None) == ["The target page was not found in the latest crawl."]
    for bad in (
        v.LinkTarget(TARGET.url, 404, True),
        v.LinkTarget(TARGET.url, 301, True),
        v.LinkTarget(TARGET.url, 200, False),
        v.LinkTarget(TARGET.url, 200, True, redirects=True),
        v.LinkTarget(TARGET.url, None, True),
    ):
        assert any("indexable page that answers 200" in p for p in link(target=bad))


def test_link_not_to_itself_and_not_twice() -> None:
    assert any("itself" in p for p in link(source_url=TARGET.url))
    assert any("already links" in p for p in link(already_linked={TARGET.url}))
    assert link(already_linked={"https://shop.example/other"}) == []


@pytest.mark.parametrize(
    ("anchor", "ok"),
    [
        ("coffee", False),
        ("coffee grinder", True),
        ("our pour over method needs a steady hand read", False),
        ("steady hand", True),
        ("", False),
        (None, False),
    ],
)
def test_link_anchor_word_count_and_presence(anchor: Any, ok: bool) -> None:
    assert (link(anchor=anchor) == []) is ok


def test_link_anchor_must_appear_word_for_word() -> None:
    assert any("word for word" in p for p in link(anchor="espresso machine reviews"))
    nine = "Our pour over method needs a steady hand. Read"
    assert any("9 word" in p or "word(s)" in p for p in link(anchor=nine, source_text=nine))
    eight = "Our pour over method needs a steady"
    assert link(anchor=eight, source_text=eight) == []
