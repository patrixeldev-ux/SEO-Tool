"""Site-level checks (one verdict for the whole site) and the checks that need the whole crawl."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from searchos.audit.ai_bots import AiBot, load_ai_bots
from searchos.audit.model import AuditInput, PageData
from searchos.audit.registry import page_check, site_check
from searchos.audit.robots_policy import RobotsPolicy
from searchos.audit.schema_rules import has_organization

DEEP_PAGE_DEPTH = 4
# Sitemap URLs are judged against the rules a normal search crawler would see.
SEARCH_CRAWLER_TOKEN = "Googlebot"  # noqa: S105 - a robots.txt user-agent token, not a credential


def _homepage_origin(audit: AuditInput) -> str:
    parts = urlsplit(audit.site.homepage_url)
    return f"{parts.scheme}://{parts.netloc}"


@site_check("HTTPS_MISSING")
def https_missing(audit: AuditInput) -> dict[str, Any] | None:
    if audit.site.homepage_url.startswith("http://"):
        return {"homepage": audit.site.homepage_url}
    return None


@site_check("SITEMAP_MISSING")
def sitemap_missing(audit: AuditInput) -> dict[str, Any] | None:
    return {} if not audit.site.sitemap_found else None


@site_check("ROBOTS_MISSING")
def robots_missing(audit: AuditInput) -> dict[str, Any] | None:
    return {} if audit.site.robots_status == "missing" else None


@page_check("BLOCKED_IN_SITEMAP", "sitemap_urls")
def blocked_in_sitemap(page: PageData, audit: AuditInput) -> dict[str, Any] | None:
    policy = RobotsPolicy(audit.site.robots_status, audit.site.robots_text)
    if not policy.allows(page.normalised_url, SEARCH_CRAWLER_TOKEN):
        return {"crawler": SEARCH_CRAWLER_TOKEN}
    return None


@page_check("ORPHAN_PAGE", "sitemap_pages")
def orphan_page(page: PageData, audit: AuditInput) -> dict[str, Any] | None:
    home = audit.homepage
    if home is not None and home.id == page.id:
        return None
    return {} if page.inlinks_count == 0 else None


@page_check("DEEP_PAGE", "html_pages")
def deep_page(page: PageData, _audit: AuditInput) -> dict[str, Any] | None:
    if page.depth is not None and page.depth > DEEP_PAGE_DEPTH:
        return {"depth": page.depth, "limit": DEEP_PAGE_DEPTH}
    return None


def _homepage_loaded(audit: AuditInput) -> bool:
    home = audit.homepage
    return home is not None and home.is_success_html


@site_check("ORG_SCHEMA_MISSING", "homepage", applicable=_homepage_loaded)
def org_schema_missing(audit: AuditInput) -> dict[str, Any] | None:
    home = audit.homepage
    if home is not None and not has_organization(home.entities):
        return {"found_types": sorted({t for e in home.entities for t in _types(e)})}
    return None


def _types(entity: dict[str, Any]) -> list[str]:
    from searchos.audit.schema_rules import types_of

    return types_of(entity)


def blocked_bots(audit: AuditInput, purposes: frozenset[str]) -> list[AiBot]:
    policy = RobotsPolicy(audit.site.robots_status, audit.site.robots_text)
    home = _homepage_origin(audit) + "/"
    return [b for b in load_ai_bots() if b.purpose in purposes and not policy.allows(home, b.name)]


@site_check("AI_SEARCH_BOT_BLOCKED")
def ai_search_bot_blocked(audit: AuditInput) -> dict[str, Any] | None:
    blocked = blocked_bots(audit, frozenset({"search", "user_fetch"}))
    if blocked:
        return {"bots": [{"name": b.name, "vendor": b.vendor, "purpose": b.purpose} for b in blocked]}
    return None


@site_check("AI_TRAINING_BOT_BLOCKED")
def ai_training_bot_blocked(audit: AuditInput) -> dict[str, Any] | None:
    blocked = blocked_bots(audit, frozenset({"training"}))
    if blocked:
        return {"bots": [{"name": b.name, "vendor": b.vendor, "purpose": b.purpose} for b in blocked]}
    return None


@site_check("LLMS_TXT", applicable=lambda a: a.site.llms_txt_present is not None)
def llms_txt(audit: AuditInput) -> dict[str, Any] | None:
    return {} if audit.site.llms_txt_present is False else None
