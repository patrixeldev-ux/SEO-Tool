"""Builders: turn findings into candidate actions. One builder per action type (see `mapping.py` for which
sources feed which type).

Builders are pure functions of the `PlanContext`. They never call the AI: for value-type actions they leave
`proposed_value` empty and put everything the AI (or a person) needs into `evidence`; `planner/generate.py`
fills the value in.
"""

from __future__ import annotations

import difflib
from collections import defaultdict
from collections.abc import Callable, Iterable
from typing import Any
from urllib.parse import urlsplit

from searchos.audit.catalog import load_catalog
from searchos.audit.schema_rules import rule_family, types_of
from searchos.crawl.urls import InvalidUrlError, match_key, normalise_url
from searchos.planner.gain import CTR_GAP_SHARE
from searchos.planner.manual_tasks import MANUAL_BUILDERS
from searchos.planner.mapping import MAPPINGS
from searchos.planner.model import Candidate, Finding, PlanContext, PlanPage

MAX_URLS_LISTED = 100
MAX_ALT_PER_PAGE = 50
REPLACEMENT_MIN_SIMILARITY = 0.75

Builder = Callable[[PlanContext, list[Finding]], list[Candidate]]


# ---- helpers -------------------------------------------------------------------------------------
def why_of(findings: Iterable[Finding]) -> list[str]:
    seen: dict[str, None] = {}
    for f in findings:
        mapping = MAPPINGS.get(f.source)
        if mapping and mapping.why:
            seen.setdefault(mapping.why)
    return list(seen)


def source_kind(findings: Iterable[Finding]) -> str:
    kinds = {f.kind for f in findings}
    return "audit" if "audit" in kinds else "gsc"


def sources_of(findings: Iterable[Finding]) -> list[str]:
    return sorted({f.source for f in findings})


def group_by_page(findings: list[Finding]) -> dict[str, list[Finding]]:
    grouped: dict[str, list[Finding]] = defaultdict(list)
    for f in findings:
        if f.url:
            grouped[f.url].append(f)
    return dict(grouped)


def search_facts(ctx: PlanContext, url: str) -> dict[str, Any]:
    """The Search Console numbers shown next to an action (empty when the page has none)."""
    entry = ctx.gsc.get(match_key(url))
    if entry is None:
        return {}
    return {
        "impressions_28d": entry.impressions,
        "clicks_28d": entry.clicks,
        "top_queries": [
            {
                "query": q.query,
                "impressions": q.impressions,
                "clicks": q.clicks,
                "position": round(q.position, 1),
            }
            for q in entry.queries[:5]
        ],
    }


def target_query(ctx: PlanContext, url: str, findings: list[Finding]) -> str | None:
    """The query a rewritten title should speak to: the CTR gap with most missing clicks, else the page's top query."""
    gaps = [f for f in findings if f.source == "CTR_GAP" and f.query]
    if gaps:
        return max(gaps, key=lambda f: float(f.details.get("missing_clicks") or 0)).query
    entry = ctx.gsc.get(match_key(url))
    return entry.top_query.query if entry and entry.top_query else None


def ctr_gap_clicks(findings: list[Finding], action_type: str) -> float | None:
    gaps = [f for f in findings if f.source == "CTR_GAP"]
    if not gaps:
        return None
    total = sum(float(f.details.get("missing_clicks") or 0) for f in gaps)
    return round(total * CTR_GAP_SHARE.get(action_type, 1.0), 2)


def gap_rows(findings: list[Finding]) -> list[dict[str, Any]]:
    return [
        {
            "query": f.query,
            "impressions": f.details.get("impressions"),
            "position": f.details.get("position"),
            "ctr": f.details.get("ctr"),
            "expected_ctr": f.details.get("expected_ctr"),
            "missing_clicks": f.details.get("missing_clicks"),
        }
        for f in findings
        if f.source == "CTR_GAP"
    ]


def _eligible(page: PlanPage | None) -> PlanPage | None:
    return page if page is not None and page.is_indexable else None


# ---- AI value types: title, description -----------------------------------------------------------
def _snippet_candidates(
    ctx: PlanContext,
    findings: list[Finding],
    action_type: str,
    field: str,
    current: Callable[[PlanPage], str | None],
) -> list[Candidate]:
    out: list[Candidate] = []
    for url, group in group_by_page(findings).items():
        page = _eligible(ctx.page(url))
        if page is None:
            continue
        gaps = gap_rows(group)
        search = search_facts(ctx, url)
        out.append(
            Candidate(
                type=action_type,
                target_url=url,
                field=field,
                source_kind=source_kind(group),
                sources=sources_of(group),
                current_value=current(page) or None,
                evidence={
                    "why": why_of(group),
                    "issues": [{"check": f.source, **f.details} for f in group if f.kind == "audit"],
                    "target_query": target_query(ctx, url, group),
                    "ctr_gaps": gaps,
                    "search": search,
                },
                gain_clicks=ctr_gap_clicks(group, action_type),
                impressions=int(search.get("impressions_28d") or 0),
            )
        )
    return out


def build_rewrite_title(ctx: PlanContext, findings: list[Finding]) -> list[Candidate]:
    return _snippet_candidates(ctx, findings, "rewrite_title", "title", lambda p: (p.title or "").strip())


def build_rewrite_meta(ctx: PlanContext, findings: list[Finding]) -> list[Candidate]:
    return _snippet_candidates(
        ctx, findings, "rewrite_meta", "meta_description", lambda p: (p.meta_description or "").strip()
    )


# ---- image alt text -------------------------------------------------------------------------------
def filename_of(src: str) -> str:
    path = urlsplit(src).path
    return path.rsplit("/", 1)[-1] or path


def build_add_alt(ctx: PlanContext, findings: list[Finding]) -> list[Candidate]:
    out: list[Candidate] = []
    for url, group in group_by_page(findings).items():
        page = _eligible(ctx.page(url))
        if page is None:
            continue
        missing = [img for img in page.images if img.get("alt") is None and img.get("src")]
        for img in missing[:MAX_ALT_PER_PAGE]:
            src = str(img["src"])
            out.append(
                Candidate(
                    type="add_alt",
                    target_url=url,
                    field=f"alt:{src}",
                    source_kind="audit",
                    sources=sources_of(group),
                    current_value=None,
                    evidence={
                        "why": why_of(group),
                        "image_src": src,
                        "filename": filename_of(src),
                        "images_missing_alt_on_page": len(missing),
                        "note": "Written from the file name and the page's text, without looking at the picture: check it before approving.",
                    },
                )
            )
    return out


# ---- structured data ------------------------------------------------------------------------------
SUPPORTED_GENERATED_TYPES = (
    "Organization",
    "LocalBusiness",
    "Article",
    "Product",
    "FAQPage",
    "BreadcrumbList",
    "WebPage",
)


def primary_schema_type(ctx: PlanContext, page: PlanPage) -> str:
    """The one type we generate for a page whose structured data is broken."""
    if ctx.homepage_url and page.url == ctx.homepage_url:
        return "Organization"
    if page.extra.get("is_article"):
        return "Article"
    return "WebPage"


def build_add_jsonld(ctx: PlanContext, findings: list[Finding]) -> list[Candidate]:
    out: list[Candidate] = []
    for url, group in group_by_page(findings).items():
        page = _eligible(ctx.page(url))
        if page is None:
            continue
        out.append(
            Candidate(
                type="add_jsonld",
                target_url=url,
                field="jsonld:Organization",
                source_kind="audit",
                sources=sources_of(group),
                current_value=None,
                evidence={
                    "why": why_of(group),
                    "schema_type": "Organization",
                    "found_types": [str(t) for f in group for t in f.details.get("found_types", [])],
                },
            )
        )
    return out


def _entity_of_family(page: PlanPage, family: str) -> dict[str, Any] | None:
    for entity in page.jsonld:
        if any(rule_family(t) == family for t in types_of(entity)):
            return entity
    return None


def build_fix_jsonld(ctx: PlanContext, findings: list[Finding]) -> list[Candidate]:
    out: list[Candidate] = []
    for url, group in group_by_page(findings).items():
        page = _eligible(ctx.page(url))
        if page is None:
            continue
        for f in group:
            if f.source == "SCHEMA_REQUIRED_PROP_MISSING":
                for problem in f.details.get("problems", []):
                    family = str(problem["type"])
                    entity = _entity_of_family(page, family)
                    if entity is None:
                        # The incomplete markup is microdata: JSON-LD cannot patch it. Left to the builder's
                        # caller to report; nothing is invented.
                        ctx.skipped.append(
                            {"url": url, "reason": "the incomplete structured data is microdata, not JSON-LD"}
                        )
                        continue
                    out.append(
                        Candidate(
                            type="fix_jsonld",
                            target_url=url,
                            field=f"jsonld:{family}",
                            source_kind="audit",
                            sources=["SCHEMA_REQUIRED_PROP_MISSING"],
                            current_value=entity,
                            evidence={
                                "why": why_of([f]),
                                "schema_type": family,
                                "missing": list(problem.get("missing", [])),
                            },
                        )
                    )
            elif f.source == "SCHEMA_PARSE_ERROR":
                schema_type = primary_schema_type(ctx, page)
                out.append(
                    Candidate(
                        type="fix_jsonld",
                        target_url=url,
                        field="jsonld:parse_error",
                        source_kind="audit",
                        sources=["SCHEMA_PARSE_ERROR"],
                        current_value={"errors": list(f.details.get("errors", []))},
                        evidence={
                            "why": why_of([f]),
                            "schema_type": schema_type,
                            "errors": list(f.details.get("errors", [])),
                            "note": "The broken block is replaced by a new one written from the page's visible text.",
                        },
                    )
                )
    return out


# ---- canonical, redirects, broken links ------------------------------------------------------------
def build_fix_canonical(ctx: PlanContext, findings: list[Finding]) -> list[Candidate]:
    out: list[Candidate] = []
    for url, group in group_by_page(findings).items():
        page = _eligible(ctx.page(url))
        if page is None:
            continue
        sources = sources_of(group)
        proposed = page.url
        note = "Points the canonical at the page itself."
        if "CANONICAL_TO_NON200" in sources and page.canonical:
            target = ctx.page(_safe_norm(page.canonical))
            if (
                target is not None
                and target.final_url
                and target.final_url != target.url
                and target.redirect_chain
            ):
                proposed = target.final_url
                note = "The current canonical redirects; this points straight at where it ends up."
        elif "CANONICAL_MISMATCH" in sources:
            note = (
                "This page names another page as its canonical, so it is left out of search results. "
                "Approve only if this page should be indexed on its own; otherwise reject."
            )
        out.append(
            Candidate(
                type="fix_canonical",
                target_url=url,
                field="canonical",
                source_kind="audit",
                sources=sources,
                current_value=page.canonical,
                proposed_value=proposed,
                evidence={
                    "why": why_of(group),
                    "note": note,
                    "issues": [{"check": f.source, **f.details} for f in group],
                    "search": search_facts(ctx, url),
                },
            )
        )
    return out


def _safe_norm(url: str) -> str | None:
    try:
        return normalise_url(url)
    except InvalidUrlError:
        return None


def build_collapse_redirect(ctx: PlanContext, findings: list[Finding]) -> list[Candidate]:
    out: list[Candidate] = []
    for url, group in group_by_page(findings).items():
        page = ctx.page(url)
        if page is None or not page.final_url:
            continue
        chain = [str(u) for u in group[0].details.get("chain", [])]
        out.append(
            Candidate(
                type="collapse_redirect",
                target_url=url,
                field="redirect",
                source_kind="audit",
                sources=sources_of(group),
                current_value={"chain": chain},
                proposed_value={"from": url, "to": page.final_url, "status": 301},
                evidence={
                    "why": why_of(group),
                    "hops": group[0].details.get("hops"),
                    "internal_links_to_this_url": page.inlinks,
                    "note": "Also update internal links that still point at the first URL.",
                },
            )
        )
    return out


def replacement_for(ctx: PlanContext, broken: str, source_page: str) -> tuple[str | None, float]:
    """The most similar healthy page to a broken link target, judged by its path (difflib ratio)."""
    broken_path = urlsplit(broken).path.strip("/").lower()
    best: tuple[str | None, float] = (None, 0.0)
    for page in ctx.pages.values():
        if not page.is_indexable or page.url == source_page or page.redirect_chain:
            continue
        ratio = difflib.SequenceMatcher(None, broken_path, urlsplit(page.url).path.strip("/").lower()).ratio()
        if ratio > best[1]:
            best = (page.url, ratio)
    return best


def build_fix_link(ctx: PlanContext, findings: list[Finding]) -> list[Candidate]:
    out: list[Candidate] = []
    for url, group in group_by_page(findings).items():
        if ctx.page(url) is None:
            continue
        for f in group:
            for link in f.details.get("links", []):
                href = str(link["url"])
                best, ratio = replacement_for(ctx, href, url)
                replace = best is not None and ratio >= REPLACEMENT_MIN_SIMILARITY
                out.append(
                    Candidate(
                        type="fix_link",
                        target_url=url,
                        field=f"link:{href}",
                        source_kind="audit",
                        sources=["BROKEN_INTERNAL_LINK"],
                        current_value={"href": href, "status": link.get("status")},
                        proposed_value={
                            "from": href,
                            "to": best if replace else None,
                            "mode": "replace" if replace else "remove",
                        },
                        evidence={
                            "why": why_of([f]),
                            "status": link.get("status"),
                            "best_match": best,
                            "path_similarity": round(ratio, 2),
                            "note": (
                                "Re-points the link to the most similar working page."
                                if replace
                                else "No similar working page was found, so the proposal is to remove the link. Edit it to choose a page instead."
                            ),
                        },
                    )
                )
    return out


# ---- briefs: content refresh, consolidation ---------------------------------------------------------
def build_refresh_content(ctx: PlanContext, findings: list[Finding]) -> list[Candidate]:
    out: list[Candidate] = []
    for url, group in group_by_page(findings).items():
        page = _eligible(ctx.page(url))
        if page is None:
            continue
        search = search_facts(ctx, url)
        queries = [q["query"] for q in search.get("top_queries", [])][:5]
        reasons: list[str] = []
        steps: list[str] = []
        for f in group:
            if f.source == "THIN_CONTENT":
                words = f.details.get("words", page.word_count)
                reasons.append(
                    f"Only {words} words of main text (the audit wants at least {f.details.get('min', 250)})."
                )
                steps.append(
                    "Add the substance a visitor came for: answer the main question completely, with examples, numbers or steps."
                )
            elif f.source == "DECAY":
                ev = f.details
                reasons.append(
                    f"Clicks fell from {ev.get('prior_clicks')} to {ev.get('recent_clicks')} over the last 28 days "
                    f"(-{abs(float(ev.get('change_pct') or 0)):.0f} %, p = {ev.get('p_value')})."
                )
                steps.append(
                    "Compare the page with what ranks above it now; update facts, dates and examples that went stale."
                )
            elif f.source == "ANSWER_BLOCK_MISSING":
                questions = [q.get("question") for q in f.details.get("questions", [])][:8]
                reasons.append(f"{f.details.get('count')} question heading(s) have no short direct answer.")
                steps.append(
                    "Directly under each question heading, add a self-contained answer of at most 80 words, then expand below it: "
                    + "; ".join(f'"{q}"' for q in questions if q)
                )
        if queries:
            steps.append(
                "Make sure the page speaks to the queries it already gets impressions for: "
                + ", ".join(queries)
                + "."
            )
        steps.append("Keep what already ranks; change the page in small steps so the effect can be measured.")
        out.append(
            Candidate(
                type="refresh_content",
                target_url=url,
                field="content",
                source_kind=source_kind(group),
                sources=sources_of(group),
                current_value={"words": page.word_count},
                proposed_value={"kind": "brief", "reasons": reasons, "steps": steps, "queries": queries},
                evidence={
                    "why": why_of(group),
                    "search": search,
                    "note": "A brief only: nothing is written or published for you.",
                },
                impressions=int(search.get("impressions_28d") or 0),
            )
        )
    return out


def build_consolidate_pages(ctx: PlanContext, findings: list[Finding]) -> list[Candidate]:
    out: list[Candidate] = []
    crawled = {match_key(u): page for u, page in ctx.pages.items()}
    for f in findings:
        pages = list(f.details.get("pages", []))
        if not pages or not f.query:
            continue
        lead = max(pages, key=lambda p: float(p.get("share") or 0))
        lead_page = crawled.get(match_key(lead["page"]))
        if lead_page is None:
            continue
        others = [p["page"] for p in pages if p["page"] != lead["page"]]
        out.append(
            Candidate(
                type="consolidate_pages",
                target_url=lead_page.url,
                field=f"query:{f.query}",
                source_kind="gsc",
                sources=["CANNIBALISATION"],
                current_value={"pages": pages},
                proposed_value={
                    "kind": "brief",
                    "reasons": [
                        f'{len(pages)} pages share the query "{f.query}"; {f.details.get("impressions_on_other_pages")} of its '
                        f"{f.details.get('query_impressions')} impressions go to pages other than the leading one."
                    ],
                    "steps": [
                        f"Keep {lead['page']} as the page for this query.",
                        "Decide for each other page: merge its useful parts into the leading page and 301-redirect it (an approval-class action), "
                        "or rewrite it to target a clearly different query.",
                        "Change internal links so they point at the leading page for this query.",
                        "Do not do all of it at once: the measurements in Phase 5 need one change at a time.",
                    ],
                    "leading_page": lead["page"],
                    "other_pages": others,
                },
                evidence={"why": why_of([f]), "pages": pages, "query": f.query},
                impressions=int(f.details.get("query_impressions") or 0),
            )
        )
    return out


# ---- manual / developer tasks (one aggregated task per kind) ---------------------------------------
def _urls(findings: list[Finding]) -> list[str]:
    return list(dict.fromkeys(f.url for f in findings if f.url))


def build_dev_fix(ctx: PlanContext, findings: list[Finding]) -> list[Candidate]:
    catalog = load_catalog()
    by_source: dict[str, list[Finding]] = defaultdict(list)
    for f in findings:
        by_source[f.source].append(f)
    out: list[Candidate] = []
    for source, group in sorted(by_source.items()):
        info = catalog.get(source)
        urls = _urls(group)
        out.append(
            Candidate(
                type="dev_fix",
                target_url=None,
                field=f"check:{source}",
                source_kind="audit",
                sources=[source],
                current_value=None,
                proposed_value={
                    "kind": "brief",
                    "steps": [info.how_to_fix.strip()] if info else [],
                    "title": info.title if info else source,
                },
                evidence={
                    "why": why_of(group),
                    "check": source,
                    "severity": info.severity if info else "medium",
                    "count": len(group),
                    "urls": urls[:MAX_URLS_LISTED],
                    "urls_truncated": len(urls) > MAX_URLS_LISTED,
                    "details": [f.details for f in group[:5]],
                },
            )
        )
    return out


SSR_STEPS = [
    "Make the page's main content part of the first HTML response (server-side rendering, static generation or pre-rendering) instead of building it in the browser.",
    "Frameworks: Next.js (App Router server components or generateStaticParams), Nuxt (SSR or nuxt generate), Astro / SvelteKit (SSR or prerender), or a pre-rendering service for an existing single-page app.",
    'Check it yourself: run `curl -s -A "GPTBot" <url>` and look for your main text in the output. If you can see it there, AI crawlers can.',
    "Do the most visited pages first, then re-run the audit; the check compares raw and rendered text.",
]


def build_ssr_task(ctx: PlanContext, findings: list[Finding]) -> list[Candidate]:
    if not findings:
        return []
    urls = _urls(findings)
    return [
        Candidate(
            type="ssr_task",
            target_url=None,
            field="ssr",
            source_kind="audit",
            sources=["JS_DEPENDENT_CONTENT"],
            proposed_value={"kind": "brief", "steps": SSR_STEPS, "title": "Render the content on the server"},
            evidence={
                "why": why_of(findings),
                "count": len(urls),
                "urls": urls[:MAX_URLS_LISTED],
                "urls_truncated": len(urls) > MAX_URLS_LISTED,
                "raw_shares": {f.url: f.details.get("raw_share") for f in findings[:20] if f.url},
            },
        )
    ]


def build_robots_review(ctx: PlanContext, findings: list[Finding]) -> list[Candidate]:
    """robots.txt is never changed automatically. The task only explains what to look at."""
    if not findings:
        return []
    steps: list[str] = []
    evidence: dict[str, Any] = {
        "why": why_of(findings),
        "note": "Search OS never edits robots.txt: a wrong line there can remove a whole site from search.",
    }
    for f in findings:
        if f.source == "AI_SEARCH_BOT_BLOCKED":
            bots = f.details.get("bots", [])
            evidence["blocked_bots"] = bots
            steps.append(
                "Open your robots.txt and look for Disallow rules that apply to: "
                + ", ".join(f"{b['name']} ({b['vendor']})" for b in bots)
                + ". Remove them unless blocking is deliberate."
            )
        elif f.source == "ROBOTS_MISSING":
            steps.append(
                "Publish a robots.txt at /robots.txt. A minimal one is `User-agent: *` with an empty `Disallow:` and a `Sitemap:` line."
            )
        elif f.source == "BLOCKED_IN_SITEMAP" and f.url:
            evidence.setdefault("blocked_urls", []).append(f.url)
    if evidence.get("blocked_urls"):
        steps.append(
            "These URLs are in your sitemap but blocked by robots.txt: either allow them or take them out of the sitemap."
        )
    steps.append("After changing it, re-run the audit to confirm.")
    return [
        Candidate(
            type="robots_review",
            target_url=None,
            field="robots",
            source_kind="audit",
            sources=sources_of(findings),
            proposed_value={"kind": "brief", "steps": steps, "title": "Review robots.txt"},
            evidence=evidence,
        )
    ]


def build_noindex_review(ctx: PlanContext, findings: list[Finding]) -> list[Candidate]:
    if not findings:
        return []
    urls = _urls(findings)
    return [
        Candidate(
            type="noindex_review",
            target_url=None,
            field="noindex_in_sitemap",
            source_kind="audit",
            sources=["NOINDEX_IN_SITEMAP"],
            proposed_value={
                "kind": "brief",
                "title": "Decide about noindex pages listed in the sitemap",
                "steps": [
                    "For each URL below decide: should it appear in search?",
                    "If yes, remove the noindex tag (or X-Robots-Tag header). If no, remove the URL from the sitemap.",
                    "Search OS never adds or removes noindex by itself.",
                ],
            },
            evidence={"why": why_of(findings), "count": len(urls), "urls": urls[:MAX_URLS_LISTED]},
        )
    ]


def _links_builder(ctx: PlanContext, findings: list[Finding]) -> list[Candidate]:
    from searchos.planner.links import build_add_internal_links

    return build_add_internal_links(ctx, findings)


BUILDERS: dict[str, Builder] = {
    "add_internal_links": _links_builder,
    "rewrite_title": build_rewrite_title,
    "rewrite_meta": build_rewrite_meta,
    "add_alt": build_add_alt,
    "add_jsonld": build_add_jsonld,
    "fix_jsonld": build_fix_jsonld,
    "fix_canonical": build_fix_canonical,
    "collapse_redirect": build_collapse_redirect,
    "fix_link": build_fix_link,
    "refresh_content": build_refresh_content,
    "consolidate_pages": build_consolidate_pages,
    "dev_fix": build_dev_fix,
    "ssr_task": build_ssr_task,
    "robots_review": build_robots_review,
    "noindex_review": build_noindex_review,
    **MANUAL_BUILDERS,
}
