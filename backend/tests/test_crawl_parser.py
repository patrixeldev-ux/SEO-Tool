from __future__ import annotations

from pathlib import Path

import pytest

from searchos.crawl.parser import (
    ParsedPage,
    count_words,
    decode_html,
    extract_main_text,
    is_question,
    parse_page,
)
from searchos.devtools.fixture_server import default_fixture_dir

BASE = "http://site.test"
HOSTS = {"site.test"}


def internal(url: str) -> bool:
    return url.split("/")[2] in HOSTS


def parse(html: str, path: str = "/p.html") -> ParsedPage:
    return parse_page(html, BASE + path, internal)


def fixture_page(rel: str) -> ParsedPage:
    html = (default_fixture_dir() / rel).read_text(encoding="utf-8").replace("{{BASE_URL}}", BASE)
    path = "/" + rel.removesuffix("index.html")
    return parse(html, path)


CLEAN = [
    "index.html",
    "about.html",
    "products/index.html",
    "products/espresso-beans.html",
    "products/cold-brew.html",
    "blog/index.html",
    "blog/brewing-guide.html",
    "blog/roast-levels.html",
    "contact.html",
    "redir-target.html",
    "chain-final.html",
    "deep/level-3.html",
]


@pytest.mark.parametrize("rel", CLEAN)
def test_clean_fixture_pages_have_enough_real_prose(rel: str) -> None:
    page = fixture_page(rel)
    assert page.main_text_method == "trafilatura"
    assert page.word_count >= 300, page.word_count
    assert page.title and 30 <= len(page.title) <= 60
    assert page.meta_description
    assert len(page.h1) == 1
    assert page.has_viewport and page.lang == "en"


def test_head_fields_of_a_fixture_page() -> None:
    page = fixture_page("about.html")
    assert page.title == "About Harbor & Pine, Roasters Since 2014"
    assert page.canonical == "http://site.test/about.html"
    assert page.robots_meta is None
    assert page.hreflang == [
        {"lang": "en", "href": "http://site.test/about.html"},
        {"lang": "x-default", "href": "http://site.test/about.html"},
    ]
    assert [e["@type"] for e in page.jsonld] == ["Organization"]
    assert page.http_resources == []


def test_defect_pages_expose_their_defect() -> None:
    assert fixture_page("defects/title-missing.html").title is None
    assert fixture_page("defects/meta-missing.html").meta_description is None
    assert fixture_page("defects/h1-missing.html").h1 == []
    assert len(fixture_page("defects/h1-multiple.html").h1) == 2
    assert fixture_page("defects/viewport-missing.html").has_viewport is False
    assert fixture_page("defects/lang-missing.html").lang is None
    assert fixture_page("defects/canonical-missing.html").canonical is None
    assert fixture_page("defects/noindex-in-sitemap.html").robots_meta == "noindex, follow"
    assert fixture_page("defects/thin.html").word_count < 100
    bad = fixture_page("defects/hreflang-invalid.html")
    assert bad.hreflang[0]["lang"] == "english"
    imgs = fixture_page("defects/img-alt-missing.html").images
    assert [i.alt for i in imgs] == [None]
    assert fixture_page("about.html").images == [] or all(i.alt for i in fixture_page("about.html").images)


def test_json_ld_errors_are_reported_not_swallowed() -> None:
    page = fixture_page("defects/schema-parse-error.html")
    assert page.jsonld == []
    assert len(page.jsonld_errors) == 1
    assert "line 1" in page.jsonld_errors[0]


def test_graph_and_list_json_ld_are_flattened() -> None:
    html = """<script type="application/ld+json">{"@context":"https://schema.org","@graph":[
      {"@type":"Organization","name":"A"},{"@type":["Product","Thing"],"name":"B"}]}</script>
      <script type="application/ld+json">[{"@type":"BreadcrumbList","itemListElement":[]}]</script>
      <script type="application/ld+json"></script>"""
    page = parse(html)
    assert [e["@type"] for e in page.jsonld] == ["Organization", ["Product", "Thing"], "BreadcrumbList"]
    assert page.jsonld_errors == ["empty JSON-LD block"]


def test_microdata_is_extracted() -> None:
    html = """<div itemscope itemtype="https://schema.org/Product"><span itemprop="name">Mug</span>
      <div itemprop="offers" itemscope itemtype="https://schema.org/Offer"><span itemprop="price">5</span></div></div>"""
    page = parse(html)
    assert page.microdata[0]["@type"] == ["Product"]
    assert page.microdata[0]["name"] == "Mug"
    assert page.microdata[0]["offers"]["price"] == "5"


def test_links_are_resolved_classified_and_deduplicated() -> None:
    html = """<base href="/blog/"><a href="post.html">Post</a><a href="post.html">Post</a>
      <a href="/about#team" rel="nofollow  Sponsored">About</a><a href="https://other.example/x">Out</a>
      <a href="mailto:a@b.c">mail</a><a href="#top">top</a><a href="javascript:void(0)">js</a>
      <a href="/img"><img src="/i.png" alt="Logo"></a>"""
    links = parse(html).links
    assert [(link.url, link.anchor, link.rel, link.is_internal) for link in links] == [
        (BASE + "/blog/post.html", "Post", None, True),
        (BASE + "/about", "About", "nofollow sponsored", True),
        ("https://other.example/x", "Out", None, False),
        (BASE + "/img", "Logo", None, True),
    ]


def test_canonical_is_resolved_against_the_page() -> None:
    assert parse('<link rel="canonical" href="/a/b">', "/x.html").canonical == BASE + "/a/b"
    assert parse('<link rel="canonical" href="">').canonical == ""
    assert parse('<link rel="Canonical alternate" href="../c">', "/d/e.html").canonical == BASE + "/c"


def test_robots_meta_combines_robots_and_googlebot() -> None:
    page = parse('<meta name="robots" content="NoFollow"><meta name="googlebot" content="NOINDEX">')
    assert page.robots_meta == "nofollow, noindex"


def test_mixed_content_resources_are_found() -> None:
    html = """<img src="http://cdn.example/a.png"><img src="https://ok.example/b.png"><script src="//cdn/x.js"></script>
      <link rel="stylesheet" href="http://cdn.example/s.css"><link rel="icon" href="http://cdn.example/f.ico">
      <a href="http://plain.example/">link, not a resource</a>"""
    page = parse(html)
    assert page.http_resources == ["http://cdn.example/a.png", "http://cdn.example/s.css"]


def test_answer_block_detection() -> None:
    long_answer = " ".join(["word"] * 81)
    html = f"""<h2>What is cold brew?</h2><p>Coffee steeped in cold water for many hours.</p>
      <h2>Why is it smooth?</h2><p>{long_answer}</p>
      <h3>How to store it?</h3><ul><li>Fridge</li></ul>
      <h2>Our story</h2><p>Not a question.</p>
      <h2>Does it keep?</h2><p></p><p>Yes, for a week.</p>
      <h2>Is it strong?</h2><p>{" ".join(["w"] * 80)}</p>"""
    heads = parse(html).question_headings
    assert [(h.text, h.answered, h.answer_words) for h in heads] == [
        ("What is cold brew?", True, 8),
        ("Why is it smooth?", False, 81),
        ("How to store it?", False, None),
        ("Does it keep?", True, 4),
        ("Is it strong?", True, 80),
    ]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("What is a canonical tag?", True),
        ("How long does shipping take", True),
        ("Is it safe", True),
        ("Can I return it", True),
        ("Why choose us", False),
        ("How we source our beans", False),
        ("Our story", False),
        ("Who we are", False),
        ("Wasn't that fun?", True),
    ],
)
def test_question_heading_heuristic(text: str, expected: bool) -> None:
    assert is_question(text) is expected


def test_article_signals() -> None:
    full = parse(
        '<script type="application/ld+json">{"@type":"BlogPosting","author":{"name":"A"},"datePublished":"2026-01-01"}</script>'
    )
    assert (full.is_article, full.has_author, full.has_date) == (True, True, True)
    og = parse('<meta property="og:type" content="article"><p>text</p>')
    assert (og.is_article, og.has_author, og.has_date) == (True, False, False)
    html_signals = parse(
        '<meta property="og:type" content="article"><meta name="author" content="Z"><time datetime="2026-02-02">x</time>'
    )
    assert (html_signals.has_author, html_signals.has_date) == (True, True)
    plain = parse("<p>hello</p>")
    assert (plain.is_article, plain.has_author, plain.has_date) == (False, False, False)


def test_fixture_article_pages_carry_author_and_date() -> None:
    for rel in ("blog/brewing-guide.html", "blog/roast-levels.html"):
        page = fixture_page(rel)
        assert (page.is_article, page.has_author, page.has_date) == (True, True, True)
    page = fixture_page("defects/author-date-missing.html")
    assert (page.is_article, page.has_author, page.has_date) == (True, False, False)


def test_main_text_falls_back_when_trafilatura_finds_nothing() -> None:
    text, method = extract_main_text("<html><body><script>var x=1</script><style>p{}</style></body></html>")
    assert (text, method) == ("", "fallback")
    text, method = extract_main_text("<p>x</p>")
    assert (text, method) == ("x", "fallback")


def test_trafilatura_drops_navigation_on_real_pages() -> None:
    page = fixture_page("about.html")
    assert "Contact" not in page.main_text.split("\n")  # the <nav> links are not main text
    assert page.main_text.startswith("About our roastery")


def test_js_only_page_has_almost_no_raw_text() -> None:
    page = fixture_page("defects/js-rendered.html")
    assert page.word_count < 20


def test_decode_html_honours_declared_charsets() -> None:
    assert decode_html("café".encode("latin-1"), "text/html; charset=ISO-8859-1") == "café"
    assert decode_html(b'<meta charset="windows-1252"><p>\x93hi\x94</p>').endswith("“hi”</p>")
    assert decode_html(b"\xff\xfe broken", None) != ""
    assert decode_html("naïve".encode(), "text/html") == "naïve"


def test_count_words() -> None:
    assert count_words("One two-three don't  stop, 42 times!") == 6
    assert count_words("") == 0


def test_empty_and_garbage_html_do_not_crash() -> None:
    for html in ("", "<", "<<<>>>", "plain text", "<html", Path(__file__).read_text()[:2000]):
        page = parse(html)
        assert isinstance(page.word_count, int)
