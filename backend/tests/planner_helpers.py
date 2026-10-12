"""Builders for planner tests: pages, findings and contexts without a database."""

from __future__ import annotations

from typing import Any

from searchos.planner.model import Finding, GscPage, GscQuery, PlanContext, PlanPage
from searchos.stats.ctr_curve import default_curve

ORIGIN = "https://shop.example"


def page(path: str = "/", **kw: Any) -> PlanPage:
    url = kw.pop("url", ORIGIN + path)
    values: dict[str, Any] = {
        "id": f"id-{path}",
        "url": url,
        "status_code": 200,
        "final_url": url,
        "redirect_chain": (),
        "title": "A fine page title that is long enough",
        "meta_description": "A description.",
        "h1": ("Heading",),
        "canonical": url,
        "word_count": 400,
        "depth": 1,
        "inlinks": 3,
        "in_sitemap": True,
        "is_indexable": True,
        "is_success_html": True,
        "text": "Some visible text about coffee beans and brewing at home with a grinder.",
        "images": (),
        "h2": (),
        "jsonld": (),
        "jsonld_errors": (),
        "extra": {},
    }
    values.update(kw)
    return PlanPage(**values)


def audit(source: str, url: str | None, **details: Any) -> Finding:
    return Finding(source, "audit", url, details)


def opp(source: str, url: str | None, query: str, score: float = 10.0, **details: Any) -> Finding:
    return Finding(source, "gsc", url, details, query=query, score=score, raw_url=url)


def gsc_page(impressions: int, clicks: int, *queries: tuple[str, int, int, float]) -> GscPage:
    return GscPage(impressions, clicks, [GscQuery(q, i, c, p) for q, i, c, p in queries])


def context(pages: list[PlanPage], findings: list[Finding], **kw: Any) -> PlanContext:
    values: dict[str, Any] = {
        "site_id": "s1",
        "org_id": "o1",
        "domain": "shop.example",
        "site_name": "Shop",
        "origin": ORIGIN,
        "homepage_url": ORIGIN + "/",
        "pages": {p.url: p for p in pages},
        "findings": findings,
        "gsc": {},
        "curve": default_curve(),
        "has_audit": True,
        "has_gsc": False,
    }
    values.update(kw)
    return PlanContext(**values)
