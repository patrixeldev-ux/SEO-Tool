"""Per-page checks. Each returns None when the page passes, or a dict of details when it fails."""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from searchos.audit.model import AuditInput, PageData, safe_normalise
from searchos.audit.registry import Failure, group_check, page_check
from searchos.audit.schema_rules import missing_properties, rule_family, types_of

TITLE_MIN, TITLE_MAX = 30, 60
THIN_WORDS = 250
TTFB_SLOW_MS = 800
MAX_LISTED = 20

_HREFLANG = re.compile(r"^(?:x-default|[a-z]{2,3}(?:-[a-z]{4})?(?:-(?:[a-z]{2}|\d{3}))?)$", re.I)
_WS = re.compile(r"\s+")


def _squash(text: str | None) -> str:
    return _WS.sub(" ", text or "").strip()


# ---- HTTP, redirects, links -----------------------------------------------------------------------
@page_check("HTTP_4XX", "responding_urls")
def http_4xx(page: PageData, _audit: AuditInput) -> dict[str, Any] | None:
    if page.status_code is not None and 400 <= page.status_code < 500:
        return {"status": page.status_code}
    return None


@page_check("HTTP_5XX", "responding_urls")
def http_5xx(page: PageData, _audit: AuditInput) -> dict[str, Any] | None:
    if page.status_code is not None and page.status_code >= 500:
        return {"status": page.status_code}
    return None


@page_check("REDIRECT_CHAIN", "fetched_urls")
def redirect_chain(page: PageData, _audit: AuditInput) -> dict[str, Any] | None:
    if page.fetch_error == "redirect_loop":
        return None  # reported by REDIRECT_LOOP
    if len(page.redirect_chain) > 1:
        return {
            "hops": len(page.redirect_chain),
            "chain": [hop["url"] for hop in page.redirect_chain] + [page.final_url],
        }
    return None


@page_check("REDIRECT_LOOP", "fetched_urls")
def redirect_loop(page: PageData, _audit: AuditInput) -> dict[str, Any] | None:
    if page.fetch_error == "redirect_loop":
        return {"chain": [hop["url"] for hop in page.redirect_chain]}
    return None


@page_check("BROKEN_INTERNAL_LINK", "html_pages")
def broken_internal_link(page: PageData, audit: AuditInput) -> dict[str, Any] | None:
    broken: list[dict[str, Any]] = []
    for target in dict.fromkeys(page.internal_link_targets):
        landed = audit.resolve_final(target)
        if landed is None or landed.status_code is None:
            continue  # not crawled, or could not be fetched: we do not know
        if landed.status_code >= 400:
            broken.append({"url": target, "status": landed.status_code})
    if broken:
        return {"count": len(broken), "links": broken[:MAX_LISTED]}
    return None


# ---- canonical ------------------------------------------------------------------------------------
@page_check("CANONICAL_MISSING", "indexable_pages")
def canonical_missing(page: PageData, _audit: AuditInput) -> dict[str, Any] | None:
    return {} if not page.canonical else None


@page_check("CANONICAL_TO_NON200", "internal_canonical_pages")
def canonical_to_non200(page: PageData, audit: AuditInput) -> dict[str, Any] | None:
    target_url = safe_normalise(page.canonical)
    target = audit.by_norm.get(target_url or "")
    if target is None:
        return None  # not crawled: unknown
    if target.status_code == 200 and not target.redirect_chain and target.fetch_error is None:
        return None
    return {"canonical": target_url, "status": target.status_code, "error": target.fetch_error}


@page_check("CANONICAL_MISMATCH", "internal_canonical_pages")
def canonical_mismatch(page: PageData, _audit: AuditInput) -> dict[str, Any] | None:
    target = safe_normalise(page.canonical)
    if target is not None and target != page.normalised_url:
        return {"canonical": target}
    return None


@page_check("NOINDEX_IN_SITEMAP", "sitemap_pages")
def noindex_in_sitemap(page: PageData, _audit: AuditInput) -> dict[str, Any] | None:
    via = page.noindex_via
    return {"via": via} if via else None


# ---- titles and descriptions ----------------------------------------------------------------------
def _self_canonical(page: PageData) -> bool:
    """No canonical, or a canonical that points at the page itself (others are duplicates by design)."""
    if not page.canonical:
        return True
    return safe_normalise(page.canonical) == page.normalised_url


@page_check("TITLE_MISSING", "indexable_pages")
def title_missing(page: PageData, _audit: AuditInput) -> dict[str, Any] | None:
    return {} if not _squash(page.title) else None


def _duplicates(pages: list[PageData], value: Any, label: str) -> list[Failure]:
    groups: dict[str, list[PageData]] = defaultdict(list)
    for page in pages:
        text = _squash(value(page)).lower()
        if text and _self_canonical(page):
            groups[text].append(page)
    failures = []
    for members in groups.values():
        if len(members) < 2:
            continue
        for page in members:
            others = [m.url for m in members if m is not page]
            failures.append(
                Failure(
                    page.id,
                    {label: _squash(value(page)), "count": len(members), "also_on": others[:5]},
                )
            )
    return failures


@group_check("TITLE_DUPLICATE", "indexable_pages")
def title_duplicate(pages: list[PageData], _audit: AuditInput) -> list[Failure]:
    return _duplicates(pages, lambda p: p.title, "title")


@page_check("TITLE_LENGTH", "indexable_pages")
def title_length(page: PageData, _audit: AuditInput) -> dict[str, Any] | None:
    title = _squash(page.title)
    if title and not TITLE_MIN <= len(title) <= TITLE_MAX:
        return {"length": len(title), "title": title, "min": TITLE_MIN, "max": TITLE_MAX}
    return None


@page_check("META_DESC_MISSING", "indexable_pages")
def meta_desc_missing(page: PageData, _audit: AuditInput) -> dict[str, Any] | None:
    return {} if not _squash(page.meta_description) else None


@group_check("META_DESC_DUPLICATE", "indexable_pages")
def meta_desc_duplicate(pages: list[PageData], _audit: AuditInput) -> list[Failure]:
    return _duplicates(pages, lambda p: p.meta_description, "description")


# ---- headings, content, images, head tags -----------------------------------------------------------
@page_check("H1_MISSING", "indexable_pages")
def h1_missing(page: PageData, _audit: AuditInput) -> dict[str, Any] | None:
    return {} if not any(h.strip() for h in page.h1) else None


@page_check("H1_MULTIPLE", "indexable_pages")
def h1_multiple(page: PageData, _audit: AuditInput) -> dict[str, Any] | None:
    return {"count": len(page.h1), "h1": page.h1[:5]} if len(page.h1) > 1 else None


@page_check("THIN_CONTENT", "indexable_pages")
def thin_content(page: PageData, _audit: AuditInput) -> dict[str, Any] | None:
    if page.word_count < THIN_WORDS:
        return {"words": page.word_count, "min": THIN_WORDS, "method": page.main_text_method}
    return None


@page_check("IMG_ALT_MISSING", "indexable_pages")
def img_alt_missing(page: PageData, _audit: AuditInput) -> dict[str, Any] | None:
    missing = [img.get("src") for img in page.extra.get("images", []) if img.get("alt") is None]
    if missing:
        return {"count": len(missing), "examples": missing[:5]}
    return None


@page_check("VIEWPORT_MISSING", "indexable_pages")
def viewport_missing(page: PageData, _audit: AuditInput) -> dict[str, Any] | None:
    return {} if not page.has_viewport else None


@page_check("LANG_MISSING", "indexable_pages")
def lang_missing(page: PageData, _audit: AuditInput) -> dict[str, Any] | None:
    return {} if not (page.lang or "").strip() else None


@page_check("HREFLANG_INVALID", "hreflang_pages")
def hreflang_invalid(page: PageData, _audit: AuditInput) -> dict[str, Any] | None:
    problems: list[str] = []
    seen: set[str] = set()
    for alt in page.hreflang:
        code, href = alt.get("lang", ""), alt.get("href", "")
        if not _HREFLANG.match(code):
            problems.append(f'"{code}" is not a valid language code')
        elif code.lower() in seen:
            problems.append(f'"{code}" is listed more than once')
        seen.add(code.lower())
        if not href.lower().startswith(("http://", "https://")):
            problems.append(f'the address for "{code}" is not an absolute URL')
    return {"problems": problems} if problems else None


@page_check("MIXED_CONTENT", "https_pages")
def mixed_content(page: PageData, _audit: AuditInput) -> dict[str, Any] | None:
    resources = page.extra.get("http_resources") or []
    return {"count": len(resources), "resources": resources[:MAX_LISTED]} if resources else None


@page_check("TTFB_SLOW", "indexable_pages")
def ttfb_slow(page: PageData, _audit: AuditInput) -> dict[str, Any] | None:
    if page.ttfb_ms is not None and page.ttfb_ms > TTFB_SLOW_MS:
        return {"ttfb_ms": page.ttfb_ms, "limit_ms": TTFB_SLOW_MS}
    return None


# ---- Core Web Vitals (from PageSpeed Insights data attached to the crawl) -------------------------
CWV_LCP_POOR_MS = 4000
CWV_INP_POOR_MS = 500
CWV_CLS_POOR = 0.25


def _cwv(page: PageData, metric: str, source_key: str, value_key: str, limit: float) -> dict[str, Any] | None:
    psi = page.extra.get("psi") or {}
    value = psi.get(value_key)
    if value is None or value <= limit:
        return None
    return {metric: value, "limit": limit, "source": psi.get(source_key)}


@page_check("CWV_LCP_POOR", "psi_lcp_pages")
def cwv_lcp_poor(page: PageData, _audit: AuditInput) -> dict[str, Any] | None:
    return _cwv(page, "lcp_ms", "lcp_source", "lcp_ms", CWV_LCP_POOR_MS)


@page_check("CWV_INP_POOR", "psi_inp_pages")
def cwv_inp_poor(page: PageData, _audit: AuditInput) -> dict[str, Any] | None:
    return _cwv(page, "inp_ms", "inp_source", "inp_ms", CWV_INP_POOR_MS)


@page_check("CWV_CLS_POOR", "psi_cls_pages")
def cwv_cls_poor(page: PageData, _audit: AuditInput) -> dict[str, Any] | None:
    return _cwv(page, "cls", "cls_source", "cls", CWV_CLS_POOR)


# ---- structured data ------------------------------------------------------------------------------
@page_check("SCHEMA_PARSE_ERROR", "html_pages")
def schema_parse_error(page: PageData, _audit: AuditInput) -> dict[str, Any] | None:
    errors = page.jsonld_errors
    return {"errors": errors[:MAX_LISTED]} if errors else None


@page_check("SCHEMA_REQUIRED_PROP_MISSING", "html_pages")
def schema_required_prop_missing(page: PageData, _audit: AuditInput) -> dict[str, Any] | None:
    problems: list[dict[str, Any]] = []
    for entity in page.entities:
        families = {fam for t in types_of(entity) if (fam := rule_family(t)) is not None}
        for family in sorted(families):
            missing = missing_properties(entity, family)
            if missing:
                problems.append({"type": family, "missing": missing})
    return {"problems": problems} if problems else None


# ---- AI-readiness ---------------------------------------------------------------------------------
@page_check("ANSWER_BLOCK_MISSING", "question_pages")
def answer_block_missing(page: PageData, _audit: AuditInput) -> dict[str, Any] | None:
    unanswered = [
        {"question": q["text"], "next_paragraph_words": q.get("answer_words")}
        for q in page.extra.get("question_headings", [])
        if not q.get("answered")
    ]
    return {"count": len(unanswered), "questions": unanswered[:MAX_LISTED]} if unanswered else None


@page_check("AUTHOR_DATE_MISSING", "article_pages")
def author_date_missing(page: PageData, _audit: AuditInput) -> dict[str, Any] | None:
    missing = []
    if not page.extra.get("has_author"):
        missing.append("author")
    if not page.extra.get("has_date"):
        missing.append("date")
    return {"missing": missing} if missing else None


JS_MIN_RENDERED_WORDS = 50
JS_RAW_SHARE = 0.5


@page_check("JS_DEPENDENT_CONTENT", "rendered_pages")
def js_dependent_content(page: PageData, _audit: AuditInput) -> dict[str, Any] | None:
    rendered = int(page.extra["rendered_word_count"])
    if rendered >= JS_MIN_RENDERED_WORDS and page.word_count < JS_RAW_SHARE * rendered:
        return {
            "raw_words": page.word_count,
            "rendered_words": rendered,
            "raw_share": round(page.word_count / rendered, 3),
        }
    return None
