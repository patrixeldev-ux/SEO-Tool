"""Sitemap discovery and parsing (urlset, sitemap index, .gz)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal
from urllib.parse import urlsplit

from lxml import etree

from searchos.crawl.fetcher import Fetcher, gunzip_limited
from searchos.crawl.robots import RobotsRules
from searchos.crawl.urls import InvalidUrlError, normalise_url

MAX_SITEMAP_FILES = 50
MAX_SITEMAP_URLS = 50_000
MAX_INDEX_DEPTH = 2
MAX_SITEMAP_BYTES = 50 * 1024 * 1024  # uncompressed, as in the sitemap protocol

SitemapKind = Literal["urlset", "index", "invalid"]


@dataclass(slots=True)
class ParsedSitemap:
    kind: SitemapKind
    locs: list[str] = field(default_factory=list)
    error: str | None = None


@dataclass(slots=True)
class SitemapResult:
    found: bool = False
    declared: list[str] = field(default_factory=list)  # from robots.txt (or the /sitemap.xml guess)
    files: list[str] = field(default_factory=list)  # sitemap files that were fetched and parsed
    urls: list[str] = field(default_factory=list)  # normalised page URLs on allowed hosts
    errors: list[str] = field(default_factory=list)
    truncated: bool = False


def parse_sitemap(data: bytes) -> ParsedSitemap:
    """Parse sitemap XML (transparently gunzipping). Never raises: problems are reported in `error`."""
    try:
        if data[:2] == b"\x1f\x8b":
            data = gunzip_limited(data, MAX_SITEMAP_BYTES)
        parser = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False)
        root = etree.fromstring(data, parser)
    except (ValueError, etree.XMLSyntaxError, etree.XMLSchemaError, OSError) as exc:
        return ParsedSitemap("invalid", error=f"{type(exc).__name__}: {exc}"[:200])
    root_name = etree.QName(root).localname
    if root_name not in ("urlset", "sitemapindex"):
        return ParsedSitemap("invalid", error=f"unexpected root element <{root_name}>")
    locs: list[str] = []
    for loc in root.iter():
        if not isinstance(loc.tag, str) or etree.QName(loc).localname != "loc":
            continue
        text = (loc.text or "").strip()
        if text:
            locs.append(text)
    return ParsedSitemap("urlset" if root_name == "urlset" else "index", locs)


async def discover_sitemaps(fetcher: Fetcher, start_url: str, robots: RobotsRules) -> SitemapResult:
    """Collect page URLs from the sitemaps declared in robots.txt, else from /sitemap.xml."""
    result = SitemapResult()
    origin = f"{urlsplit(start_url).scheme}://{urlsplit(start_url).netloc}"
    result.declared = list(dict.fromkeys(robots.sitemaps)) or [f"{origin}/sitemap.xml"]
    queue: list[tuple[str, int]] = [(u, 0) for u in result.declared]
    visited: set[str] = set()
    page_urls: dict[str, None] = {}

    while queue:
        loc, depth = queue.pop(0)
        try:
            norm = normalise_url(loc)
        except InvalidUrlError:
            result.errors.append(f"invalid sitemap URL: {loc[:120]}")
            continue
        if norm in visited:
            continue
        if len(visited) >= MAX_SITEMAP_FILES:
            result.truncated = True
            result.errors.append(f"more than {MAX_SITEMAP_FILES} sitemap files; the rest were skipped")
            break
        visited.add(norm)
        if not fetcher.is_allowed(norm):
            result.errors.append(f"sitemap on a host we may not fetch: {norm}")
            continue
        fetched = await fetcher.fetch(
            norm, read_body="always", max_bytes=MAX_SITEMAP_BYTES, respect_robots=False
        )
        if fetched.error or fetched.status_code is None:
            result.errors.append(f"{norm}: {fetched.error}")
            continue
        if not 200 <= fetched.status_code < 300:
            # The guessed /sitemap.xml being absent is the normal "no sitemap" case, reported by the check.
            if norm not in robots.sitemaps and len(result.declared) == 1 and fetched.status_code == 404:
                continue
            result.errors.append(f"{norm}: HTTP {fetched.status_code}")
            continue
        parsed = parse_sitemap(fetched.body or b"")
        if parsed.kind == "invalid":
            result.errors.append(f"{norm}: {parsed.error}")
            continue
        result.found = True
        result.files.append(norm)
        if parsed.kind == "index":
            if depth >= MAX_INDEX_DEPTH:
                result.errors.append(f"{norm}: sitemap index nested deeper than {MAX_INDEX_DEPTH}")
                continue
            queue.extend((child, depth + 1) for child in parsed.locs)
            continue
        for page in parsed.locs:
            try:
                page_norm = normalise_url(page)
            except InvalidUrlError:
                continue
            if not fetcher.is_allowed(page_norm):
                continue
            page_urls[page_norm] = None
            if len(page_urls) >= MAX_SITEMAP_URLS:
                result.truncated = True
                break
    result.urls = list(page_urls)
    return result
