"""Plain data the audit works on. No database or network types: checks are pure functions of these."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

from searchos.crawl.urls import InvalidUrlError, normalise_url

Severity = Literal["critical", "high", "medium", "low", "info"]
Category = Literal["indexability", "on_page", "structured_data", "performance", "ai_readiness"]

SEVERITIES: tuple[Severity, ...] = ("critical", "high", "medium", "low", "info")
CATEGORIES: tuple[Category, ...] = (
    "indexability",
    "on_page",
    "structured_data",
    "performance",
    "ai_readiness",
)
CATEGORY_LABELS: dict[Category, str] = {
    "indexability": "Indexability",
    "on_page": "On-page",
    "structured_data": "Structured data",
    "performance": "Performance",
    "ai_readiness": "AI-readiness",
}

_HTML_TYPES = frozenset({"text/html", "application/xhtml+xml"})
_NOINDEX = re.compile(r"(?:^|[\s,:])(noindex|none)(?:$|[\s,])", re.I)


def safe_normalise(url: str | None) -> str | None:
    if not url:
        return None
    try:
        return normalise_url(url)
    except InvalidUrlError:
        return None


@dataclass(frozen=True, slots=True)
class PageData:
    """One crawled URL, as stored in the `pages` table (plus its internal link targets)."""

    id: str
    url: str
    normalised_url: str
    status_code: int | None = None
    final_url: str | None = None
    redirect_chain: list[dict[str, Any]] = field(default_factory=list)
    fetch_error: str | None = None
    depth: int | None = None
    content_type: str | None = None
    ttfb_ms: int | None = None
    title: str | None = None
    meta_description: str | None = None
    h1: list[str] = field(default_factory=list)
    canonical: str | None = None
    robots_meta: str | None = None
    x_robots_tag: str | None = None
    lang: str | None = None
    has_viewport: bool = False
    hreflang: list[dict[str, str]] = field(default_factory=list)
    word_count: int = 0
    main_text_method: str | None = None
    structured_data: dict[str, Any] = field(default_factory=dict)
    in_sitemap: bool = False
    inlinks_count: int = 0
    extra: dict[str, Any] = field(default_factory=dict)
    internal_link_targets: tuple[str, ...] = ()

    @property
    def is_html(self) -> bool:
        return (self.content_type or "").split(";")[0].strip().lower() in _HTML_TYPES

    @property
    def is_success_html(self) -> bool:
        return (
            self.status_code is not None
            and 200 <= self.status_code < 300
            and self.fetch_error is None
            and self.is_html
        )

    @property
    def noindex_via(self) -> str | None:
        """'meta', 'header' or None."""
        if self.robots_meta and _NOINDEX.search(self.robots_meta):
            return "meta"
        if self.x_robots_tag and _NOINDEX.search(self.x_robots_tag):
            return "header"
        return None

    @property
    def is_indexable(self) -> bool:
        return self.is_success_html and self.noindex_via is None

    @property
    def jsonld(self) -> list[dict[str, Any]]:
        return list(self.structured_data.get("jsonld") or [])

    @property
    def jsonld_errors(self) -> list[str]:
        return list(self.structured_data.get("jsonld_errors") or [])

    @property
    def microdata(self) -> list[dict[str, Any]]:
        return list(self.structured_data.get("microdata") or [])

    @property
    def entities(self) -> list[dict[str, Any]]:
        return self.jsonld + self.microdata


@dataclass(frozen=True, slots=True)
class SiteData:
    """Site-wide facts gathered by the crawl."""

    start_url: str
    homepage_url: str
    robots_status: str = "ok"  # ok | missing | error
    robots_text: str | None = None
    sitemap_found: bool = True
    llms_txt_present: bool | None = None  # None = unknown
    js_check_available: bool = False


class AuditInput:
    """Everything the checks read, with lookups built once."""

    def __init__(self, pages: list[PageData], site: SiteData) -> None:
        self.pages = pages
        self.site = site
        self.by_norm: dict[str, PageData] = {p.normalised_url: p for p in pages}
        self.hosts: frozenset[str] = frozenset(p.normalised_url.split("/")[2] for p in pages)

    def resolve_final(self, url: str) -> PageData | None:
        """The page a URL ends up on, following recorded redirects (None if it was not crawled)."""
        current = self.by_norm.get(url)
        for _ in range(10):
            if current is None or not current.redirect_chain or not current.final_url:
                return current
            nxt = self.by_norm.get(current.final_url)
            if nxt is None or nxt is current:
                return None
            current = nxt
        return None

    @property
    def homepage(self) -> PageData | None:
        return self.resolve_final(self.site.homepage_url)

    def is_internal(self, url: str) -> bool:
        norm = safe_normalise(url)
        return norm is not None and norm.split("/")[2] in self.hosts
