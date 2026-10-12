"""PageSpeed Insights: Core Web Vitals per page.

GET https://www.googleapis.com/pagespeedonline/v5/runPagespeed?url=...&strategy=mobile[&key=...]
Docs: https://developers.google.com/speed/docs/insights/v5/get-started

Field data (`loadingExperience.metrics`, real Chrome users over 28 days) is preferred:
  LARGEST_CONTENTFUL_PAINT_MS      milliseconds (75th percentile)
  INTERACTION_TO_NEXT_PAINT        milliseconds (75th percentile)
  CUMULATIVE_LAYOUT_SHIFT_SCORE    reported x100 (e.g. 5 means 0.05), so it is divided by 100
Lab data (`lighthouseResult.audits`, one simulated load) is only a fallback for LCP and CLS.
INP is NEVER estimated from lab data: without field data it stays empty.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any

import httpx

from searchos.integrations.errors import ProviderApiError

PSI_URL = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"
TOP_PAGES = 20
MAX_ATTEMPTS = 3
TIMEOUT = 120.0

Sleep = Callable[[float], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class PsiMetrics:
    lcp_ms: float | None = None
    lcp_source: str | None = None
    inp_ms: float | None = None
    inp_source: str | None = None
    cls: float | None = None
    cls_source: str | None = None
    performance_score: float | None = None

    @property
    def has_data(self) -> bool:
        return any(v is not None for v in (self.lcp_ms, self.inp_ms, self.cls))


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def parse_psi(body: dict[str, Any]) -> PsiMetrics:
    field = ((body.get("loadingExperience") or {}).get("metrics")) or {}

    def field_value(name: str) -> float | None:
        return _number((field.get(name) or {}).get("percentile"))

    audits = ((body.get("lighthouseResult") or {}).get("audits")) or {}

    def lab_value(name: str) -> float | None:
        return _number((audits.get(name) or {}).get("numericValue"))

    lcp, lcp_source = field_value("LARGEST_CONTENTFUL_PAINT_MS"), "field"
    if lcp is None:
        lcp, lcp_source = lab_value("largest-contentful-paint"), "lab"
    inp = field_value("INTERACTION_TO_NEXT_PAINT")  # field only
    cls_raw = field_value("CUMULATIVE_LAYOUT_SHIFT_SCORE")
    cls, cls_source = (cls_raw / 100.0 if cls_raw is not None else None), "field"
    if cls is None:
        cls, cls_source = lab_value("cumulative-layout-shift"), "lab"

    score = _number(
        ((((body.get("lighthouseResult") or {}).get("categories")) or {}).get("performance") or {}).get(
            "score"
        )
    )
    return PsiMetrics(
        lcp_ms=lcp,
        lcp_source=lcp_source if lcp is not None else None,
        inp_ms=inp,
        inp_source="field" if inp is not None else None,
        cls=cls,
        cls_source=cls_source if cls is not None else None,
        performance_score=score * 100 if score is not None else None,
    )


def _redact(text: str, api_key: str | None) -> str:
    return text.replace(api_key, "***") if api_key else text


async def fetch_psi(
    client: httpx.AsyncClient,
    url: str,
    *,
    api_key: str | None = None,
    strategy: str = "mobile",
    sleep: Sleep = asyncio.sleep,
) -> PsiMetrics:
    """One PSI run. Retries 429 and 5xx with 2 s, 4 s waits; raises `ProviderApiError` otherwise."""
    params = {"url": url, "strategy": strategy, "category": "performance"}
    if api_key:
        params["key"] = api_key
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = await client.get(PSI_URL, params=params)
        except httpx.TransportError as exc:
            if attempt == MAX_ATTEMPTS:
                raise ProviderApiError(
                    0, f"Could not reach PageSpeed Insights: {type(exc).__name__}"
                ) from exc
            await sleep(2.0 * attempt)
            continue
        if response.is_success:
            try:
                return parse_psi(response.json())
            except ValueError as exc:
                raise ProviderApiError(
                    response.status_code, "PageSpeed Insights sent an unreadable answer"
                ) from exc
        if response.status_code in (429, 500, 502, 503, 504) and attempt < MAX_ATTEMPTS:
            await sleep(2.0 * attempt)
            continue
        message = _api_message(response)
        raise ProviderApiError(response.status_code, _redact(message, api_key))
    raise ProviderApiError(0, "PageSpeed Insights did not answer")  # pragma: no cover


def _api_message(response: httpx.Response) -> str:
    try:
        body = response.json()
        detail = body["error"]["message"]
    except (ValueError, KeyError, TypeError):
        detail = response.text[:200] or f"HTTP {response.status_code}"
    return f"PageSpeed Insights answered {response.status_code}: {detail}"


@dataclass(frozen=True, slots=True)
class Candidate:
    """A crawled page that may be tested. `url` is the page's normalised URL."""

    url: str
    inlinks: int
    indexable: bool
    is_homepage: bool = False


def select_psi_urls(
    pages: Sequence[Candidate], gsc_impressions: dict[str, int] | None, limit: int = TOP_PAGES
) -> list[str]:
    """The pages to test, at most `limit`.

    With Search Console data: crawled indexable pages with the most impressions first. If fewer than `limit` of
    them are crawled pages, the rest is filled the way it is without Search Console data: the homepage first,
    then the most linked-to pages (ties broken by URL so the choice is stable).
    """
    usable = [p for p in pages if p.indexable]
    chosen: list[str] = []
    if gsc_impressions:
        ranked = sorted(
            (p for p in usable if gsc_impressions.get(p.url, 0) > 0),
            key=lambda p: (-gsc_impressions[p.url], p.url),
        )
        chosen = [p.url for p in ranked[:limit]]
    rest = sorted(
        (p for p in usable if p.url not in set(chosen)), key=lambda p: (not p.is_homepage, -p.inlinks, p.url)
    )
    for page in rest:
        if len(chosen) >= limit:
            break
        chosen.append(page.url)
    return chosen
