"""Plain data produced by the crawl engine (no database types)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(slots=True)
class LinkRecord:
    to_url: str  # absolute, normalised
    anchor: str
    rel: str | None
    is_internal: bool


@dataclass(slots=True)
class PageRecord:
    url: str  # normalised URL as requested; identical to `normalised_url`
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    status_code: int | None = None  # this URL's own response (3xx for a redirecting URL)
    final_url: str | None = None  # where the redirect chain ended (== url when there is none)
    redirect_chain: list[dict[str, Any]] = field(default_factory=list)  # [{url, status, location}]
    fetch_error: str | None = None
    discovery_depth: int = 0
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
    extra: dict[str, Any] = field(default_factory=dict)
    links: list[LinkRecord] = field(default_factory=list)

    @property
    def normalised_url(self) -> str:
        return self.url


class CrawlSink(Protocol):
    async def on_page(self, record: PageRecord) -> None: ...

    async def flush(self) -> None: ...


class ListSink:
    """Keeps everything in memory (tests, the dev helper)."""

    def __init__(self) -> None:
        self.pages: list[PageRecord] = []

    async def on_page(self, record: PageRecord) -> None:
        self.pages.append(record)

    async def flush(self) -> None:
        return None

    def by_url(self) -> dict[str, PageRecord]:
        return {p.url: p for p in self.pages}
