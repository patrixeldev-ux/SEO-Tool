"""HTML -> the facts the audit needs. Pure functions: no network, no database."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

import extruct
import trafilatura
from selectolax.lexbor import LexborHTMLParser, LexborNode

from searchos.crawl.urls import InvalidUrlError, normalise_url, resolve_url

MainTextMethod = Literal["trafilatura", "fallback"]

MAX_LINKS_PER_PAGE = 2000
MAX_JSONLD_BYTES = 500_000
MAX_HTTP_RESOURCES = 20
BOILERPLATE_TAGS = ["nav", "header", "footer", "aside", "script", "style", "noscript"]
ARTICLE_TYPES = frozenset(
    {"Article", "NewsArticle", "BlogPosting", "TechArticle", "ScholarlyArticle", "ReportageNewsArticle"}
)

_WORD = re.compile(r"[^\W_]+(?:['\u2019-][^\W_]+)*")
_WS = re.compile(r"\s+")
_CHARSET_HEADER = re.compile(r"charset\s*=\s*['\"]?([\w.:-]+)", re.I)
_CHARSET_META = re.compile(rb"<meta[^>]+charset\s*=\s*['\"]?([\w.:-]+)", re.I)
_QUESTION_START = re.compile(
    r"^(?:(?:what|who|whom|whose|when|where|why|how|which)\s+"
    r"(?:is|are|was|were|do|does|did|can|could|should|would|will|to|much|many|long|often)\b"
    r"|(?:is|are|can|could|do|does|did|should|would|will|may)\s+\w+)",
    re.I,
)
ANSWER_MAX_WORDS = 80


@dataclass(slots=True)
class ParsedLink:
    url: str  # absolute, normalised
    anchor: str
    rel: str | None
    is_internal: bool


@dataclass(slots=True)
class ParsedImage:
    src: str | None
    alt: str | None  # None = the alt attribute is absent (alt="" is a deliberate "decorative" marker)


@dataclass(slots=True)
class QuestionHeading:
    text: str
    tag: str
    answered: bool  # followed directly by a paragraph of at most ANSWER_MAX_WORDS words
    answer_words: int | None


@dataclass(slots=True)
class ParsedPage:
    title: str | None = None
    meta_description: str | None = None
    robots_meta: str | None = None
    canonical: str | None = None
    lang: str | None = None
    has_viewport: bool = False
    h1: list[str] = field(default_factory=list)
    h2: list[str] = field(default_factory=list)
    h3: list[str] = field(default_factory=list)
    hreflang: list[dict[str, str]] = field(default_factory=list)
    links: list[ParsedLink] = field(default_factory=list)
    images: list[ParsedImage] = field(default_factory=list)
    main_text: str = ""
    word_count: int = 0
    main_text_method: MainTextMethod = "fallback"
    jsonld: list[dict[str, Any]] = field(default_factory=list)
    jsonld_errors: list[str] = field(default_factory=list)
    microdata: list[dict[str, Any]] = field(default_factory=list)
    og_type: str | None = None
    is_article: bool = False
    has_author: bool = False
    has_date: bool = False
    http_resources: list[str] = field(default_factory=list)
    question_headings: list[QuestionHeading] = field(default_factory=list)


def decode_html(body: bytes, content_type: str | None = None) -> str:
    charset = None
    if content_type and (m := _CHARSET_HEADER.search(content_type)):
        charset = m.group(1)
    elif m2 := _CHARSET_META.search(body[:4096]):
        charset = m2.group(1).decode("ascii", "ignore")
    for candidate in (charset, "utf-8"):
        if not candidate:
            continue
        try:
            return body.decode(candidate)
        except (LookupError, UnicodeDecodeError):
            continue
    return body.decode("utf-8", errors="replace")


def count_words(text: str) -> int:
    return len(_WORD.findall(text))


def squash(text: str | None) -> str:
    return _WS.sub(" ", text or "").strip()


def is_question(text: str) -> bool:
    """Heuristic: a heading that ends with '?' or opens like a question ('How long does...', 'Is it...')."""
    text = text.strip()
    return text.endswith("?") or bool(_QUESTION_START.match(text))


def extract_main_text(html: str, url: str | None = None) -> tuple[str, MainTextMethod]:
    """Main content as plain text. trafilatura first; if it finds nothing, the body text minus
    nav/header/footer/aside/script/style/noscript."""
    try:
        text = trafilatura.extract(
            html, url=url, output_format="txt", include_comments=False, include_tables=True, favor_recall=True
        )
    except Exception:
        text = None
    if text and text.strip():
        return text.strip(), "trafilatura"
    tree = LexborHTMLParser(html)
    tree.strip_tags(BOILERPLATE_TAGS)
    body = tree.body
    return (squash(body.text(separator=" ", strip=True)) if body else ""), "fallback"


def _attr(node: LexborNode, name: str) -> str | None:
    value = node.attributes.get(name)
    return value if isinstance(value, str) else None


def _rel_tokens(node: LexborNode) -> set[str]:
    return set((_attr(node, "rel") or "").lower().split())


def _types(entity: dict[str, Any]) -> list[str]:
    raw = entity.get("@type")
    values = raw if isinstance(raw, list) else [raw]
    return [str(v).rsplit("/", 1)[-1] for v in values if isinstance(v, str)]


def _flatten_jsonld(data: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if isinstance(data, list):
        for item in data:
            out.extend(_flatten_jsonld(item))
    elif isinstance(data, dict):
        graph = data.get("@graph")
        if isinstance(graph, list):
            out.extend(_flatten_jsonld(graph))
            rest = {k: v for k, v in data.items() if k != "@graph"}
            if "@type" in rest:
                out.append(rest)
        else:
            out.append(data)
    return out


def _parse_jsonld(tree: LexborHTMLParser) -> tuple[list[dict[str, Any]], list[str]]:
    entities: list[dict[str, Any]] = []
    errors: list[str] = []
    for script in tree.css("script"):
        if (_attr(script, "type") or "").strip().lower() != "application/ld+json":
            continue
        raw = (script.text(strip=False) or "").strip()
        if not raw:
            errors.append("empty JSON-LD block")
            continue
        if len(raw) > MAX_JSONLD_BYTES:
            errors.append("JSON-LD block larger than 500 KB was not analysed")
            continue
        try:
            entities.extend(_flatten_jsonld(json.loads(raw)))
        except json.JSONDecodeError as exc:
            errors.append(f"{exc.msg} (line {exc.lineno}, column {exc.colno})")
    return entities, errors


def _parse_microdata(html: str, base_url: str) -> list[dict[str, Any]]:
    if "itemscope" not in html:
        return []
    try:
        data = extruct.extract(html, base_url=base_url, syntaxes=["microdata"], uniform=False)
    except Exception:
        return []
    items: list[dict[str, Any]] = []

    def convert(item: dict[str, Any]) -> dict[str, Any]:
        props = {
            k: (convert(v) if isinstance(v, dict) and "properties" in v else v)
            for k, v in (item.get("properties") or {}).items()
        }
        raw_type = item.get("type")
        types = raw_type if isinstance(raw_type, list) else [raw_type]
        return {"@type": [str(t).rsplit("/", 1)[-1] for t in types if t], **props}

    for item in data.get("microdata", []):
        if isinstance(item, dict):
            items.append(convert(item))
    return items


def _find_answer_blocks(tree: LexborHTMLParser) -> list[QuestionHeading]:
    if tree.body is None:
        return []
    blocks = tree.body.css("h1, h2, h3, h4, h5, h6, p, ul, ol, table, pre, blockquote, dl")
    result: list[QuestionHeading] = []
    for i, node in enumerate(blocks):
        if node.tag not in ("h2", "h3"):
            continue
        text = squash(node.text(separator=" ", strip=True))
        if not text or not is_question(text):
            continue
        answered, words = False, None
        for nxt in blocks[i + 1 :]:
            if nxt.tag == "p":
                n = count_words(nxt.text(separator=" ", strip=True))
                if n == 0:
                    continue  # skip empty paragraphs
                words = n
                answered = n <= ANSWER_MAX_WORDS
            break
        result.append(QuestionHeading(text, str(node.tag), answered, words))
    return result


def _has_author(tree: LexborHTMLParser, entities: list[dict[str, Any]]) -> bool:
    if any(e.get("author") for e in entities):
        return True
    for meta in tree.css("meta"):
        if (_attr(meta, "name") or "").lower() == "author" and (_attr(meta, "content") or "").strip():
            return True
    for node in tree.css("a, link"):
        if "author" in _rel_tokens(node):
            return True
    if tree.css_first('[itemprop="author"]') is not None:
        return True
    return tree.css_first(".byline, .author, .post-author, [class*='byline']") is not None


def _has_date(tree: LexborHTMLParser, entities: list[dict[str, Any]]) -> bool:
    if any(e.get("datePublished") or e.get("dateCreated") for e in entities):
        return True
    for time_node in tree.css("time"):
        if (_attr(time_node, "datetime") or "").strip():
            return True
    for meta in tree.css("meta"):
        key = (_attr(meta, "property") or _attr(meta, "name") or _attr(meta, "itemprop") or "").lower()
        if (
            key in ("article:published_time", "date", "datepublished", "og:published_time")
            and (_attr(meta, "content") or "").strip()
        ):
            return True
    return tree.css_first('[itemprop="datePublished"]') is not None


def _http_resources(tree: LexborHTMLParser, base: str) -> list[str]:
    found: list[str] = []
    selectors = [
        ("img[src]", "src"),
        ("script[src]", "src"),
        ("iframe[src]", "src"),
        ("audio[src]", "src"),
        ("video[src]", "src"),
        ("source[src]", "src"),
        ("embed[src]", "src"),
        ("object[data]", "data"),
        ("link[href]", "href"),
    ]
    for selector, attr in selectors:
        for node in tree.css(selector):
            if selector == "link[href]" and "stylesheet" not in _rel_tokens(node):
                continue
            value = (_attr(node, attr) or "").strip()
            if not value:
                continue
            if value.startswith("//"):
                continue  # protocol-relative inherits https
            try:
                absolute = resolve_url(base, value)
            except InvalidUrlError:
                continue
            if absolute and absolute.startswith("http://") and value.lower().startswith("http://"):
                found.append(absolute)
    return list(dict.fromkeys(found))[:MAX_HTTP_RESOURCES]


def parse_page(html: str, url: str, is_internal: Callable[[str], bool]) -> ParsedPage:
    """Parse one HTML document fetched from `url`. `is_internal(absolute_url)` classifies links."""
    tree = LexborHTMLParser(html)
    page = ParsedPage()

    base = url
    base_node = tree.css_first("base[href]")
    if base_node is not None:
        resolved = resolve_url(url, _attr(base_node, "href") or "")
        if resolved:
            base = resolved

    title_node = tree.css_first("title")
    if title_node is not None:
        page.title = squash(title_node.text(separator=" "))

    root = tree.css_first("html")
    if root is not None:
        page.lang = (_attr(root, "lang") or "").strip() or None

    robots_parts: list[str] = []
    for meta in tree.css("meta"):
        name = (_attr(meta, "name") or "").lower()
        content = _attr(meta, "content")
        prop = (_attr(meta, "property") or "").lower()
        if name == "description" and page.meta_description is None:
            page.meta_description = squash(content)
        elif name in ("robots", "googlebot") and content:
            robots_parts.append(squash(content).lower())
        elif name == "viewport" and (content or "").strip():
            page.has_viewport = True
        elif prop == "og:type" and content:
            page.og_type = content.strip().lower()
    page.robots_meta = ", ".join(robots_parts) if robots_parts else None

    for link in tree.css("link"):
        tokens = _rel_tokens(link)
        href = _attr(link, "href")
        if "canonical" in tokens and page.canonical is None and href is not None:
            page.canonical = (resolve_url(base, href) or href.strip()) if href.strip() else ""
        if "alternate" in tokens and _attr(link, "hreflang") is not None:
            page.hreflang.append(
                {"lang": (_attr(link, "hreflang") or "").strip(), "href": (href or "").strip()}
            )

    for tag, bucket in (("h1", page.h1), ("h2", page.h2), ("h3", page.h3)):
        for node in tree.css(tag):
            bucket.append(squash(node.text(separator=" ", strip=True)))

    seen_links: set[tuple[str, str, str | None]] = set()
    for a in tree.css("a[href]"):
        target = resolve_url(base, _attr(a, "href") or "")
        if target is None:
            continue
        anchor = squash(a.text(separator=" ", strip=True))
        if not anchor:
            img = a.css_first("img[alt]")
            anchor = squash(_attr(img, "alt")) if img is not None else ""
        rel = " ".join(sorted(_rel_tokens(a))) or None
        key = (target, anchor, rel)
        if key in seen_links:
            continue
        seen_links.add(key)
        page.links.append(ParsedLink(target, anchor[:300], rel, is_internal(target)))
        if len(page.links) >= MAX_LINKS_PER_PAGE:
            break

    for img in tree.css("img"):
        src = _attr(img, "src") or _attr(img, "data-src")
        page.images.append(ParsedImage(src, _attr(img, "alt") if "alt" in img.attributes else None))

    page.main_text, page.main_text_method = extract_main_text(html, url)
    page.word_count = count_words(page.main_text)

    page.jsonld, page.jsonld_errors = _parse_jsonld(tree)
    page.microdata = _parse_microdata(html, base)
    entities = page.jsonld + page.microdata
    type_names = {t for e in entities for t in _types(e)}
    page.is_article = bool(type_names & ARTICLE_TYPES) or page.og_type == "article"
    page.has_author = _has_author(tree, entities)
    page.has_date = _has_date(tree, entities)
    page.http_resources = _http_resources(tree, base)
    page.question_headings = _find_answer_blocks(tree)
    return page


def safe_normalise(url: str) -> str | None:
    try:
        return normalise_url(url)
    except InvalidUrlError:
        return None
