"""Which finding becomes which action. This is DATA, not logic: change the plan's behaviour by editing the table.

A finding is either an audit check (`searchos/audit/catalog.yaml`) or a Search Console opportunity
(`searchos.stats.opportunities`). Each source maps to one or more action types, or to `NO_ACTION` with a reason.
A test asserts that every catalog check and every opportunity type appears here, so a new check cannot be added
without deciding what the planner does with it.

The functions that turn findings into concrete actions (`planner/builders.py`) are keyed by action type, not by
source, so one builder can serve several sources (a title can be rewritten because it is missing, too long,
duplicated, or because it underperforms in search).
"""

from __future__ import annotations

from dataclasses import dataclass

OPPORTUNITY_TYPES: tuple[str, ...] = ("STRIKING_DISTANCE", "CTR_GAP", "DECAY", "CANNIBALISATION")


@dataclass(frozen=True, slots=True)
class SourceMapping:
    source: str  # audit check id or opportunity type
    kind: str  # "audit" | "gsc"
    actions: tuple[str, ...]  # action types; empty = no action
    why: str  # one plain-language sentence shown with the action
    no_action_reason: str | None = None


def _audit(source: str, *actions: str, why: str = "", reason: str | None = None) -> SourceMapping:
    return SourceMapping(source, "audit", actions, why, reason)


def _gsc(source: str, *actions: str, why: str) -> SourceMapping:
    return SourceMapping(source, "gsc", actions, why)


_MAPPINGS: tuple[SourceMapping, ...] = (
    # --- indexability / HTTP -------------------------------------------------------------------------
    _audit(
        "HTTP_4XX", "dev_fix", why="The page returns an error, so visitors and search engines cannot use it."
    ),
    _audit(
        "HTTP_5XX",
        "dev_fix",
        why="The server fails on this page, which makes search engines crawl the site less.",
    ),
    _audit(
        "REDIRECT_CHAIN", "collapse_redirect", why="Every extra hop slows visitors and wastes crawl budget."
    ),
    _audit("REDIRECT_LOOP", "dev_fix", why="A redirect loop never reaches a page; nobody can open it."),
    _audit("BROKEN_INTERNAL_LINK", "fix_link", why="A link on this page leads to an error page."),
    _audit(
        "CANONICAL_MISSING",
        "fix_canonical",
        why="Without a canonical tag, search engines guess which URL is the main one.",
    ),
    _audit(
        "CANONICAL_TO_NON200",
        "fix_canonical",
        why="The canonical points at a URL that does not return a normal page.",
    ),
    _audit(
        "CANONICAL_MISMATCH",
        "fix_canonical",
        why="The canonical points at a different page, so this one is left out of search results.",
    ),
    _audit(
        "NOINDEX_IN_SITEMAP",
        "noindex_review",
        why="The sitemap lists a page that tells search engines not to index it.",
    ),
    _audit(
        "BLOCKED_IN_SITEMAP",
        "robots_review",
        why="robots.txt blocks a URL that the sitemap asks search engines to crawl.",
    ),
    _audit("ROBOTS_MISSING", "robots_review", why="The site has no robots.txt."),
    _audit("SITEMAP_MISSING", "dev_fix", why="Without a sitemap, search engines find new pages more slowly."),
    _audit("HTTPS_MISSING", "dev_fix", why="The site is served over plain http."),
    _audit(
        "ORPHAN_PAGE",
        "add_internal_links",
        why="No other page links to this page, so crawlers and visitors rarely reach it.",
    ),
    _audit(
        "DEEP_PAGE",
        "add_internal_links",
        why="The page is many clicks from the homepage; links from shallower pages make it easier to reach.",
    ),
    # --- titles, descriptions, headings, content -----------------------------------------------------
    _audit("TITLE_MISSING", "rewrite_title", why="The page has no title, so search engines write their own."),
    _audit(
        "TITLE_DUPLICATE", "rewrite_title", why="Several pages share this title, so none of them stands out."
    ),
    _audit(
        "TITLE_LENGTH",
        "rewrite_title",
        why="The title is too short or too long and may be cut off in results.",
    ),
    _audit(
        "META_DESC_MISSING",
        "rewrite_meta",
        why="The page has no meta description, so search engines pick a random snippet.",
    ),
    _audit("META_DESC_DUPLICATE", "rewrite_meta", why="Several pages share this description."),
    _audit("H1_MISSING", "dev_fix", why="The page has no main heading."),
    _audit("H1_MULTIPLE", "dev_fix", why="The page has more than one main heading."),
    _audit("THIN_CONTENT", "refresh_content", why="The page has very little text to rank for."),
    _audit(
        "IMG_ALT_MISSING",
        "add_alt",
        why="An image has no alt text, so people using screen readers and AI crawlers cannot tell what it shows.",
    ),
    _audit("VIEWPORT_MISSING", "dev_fix", why="The page has no viewport tag, so it looks broken on phones."),
    _audit("LANG_MISSING", "dev_fix", why="The page does not declare its language."),
    _audit(
        "HREFLANG_INVALID",
        "dev_fix",
        why="The hreflang tags are invalid, so search engines may show the wrong language version.",
    ),
    _audit("MIXED_CONTENT", "dev_fix", why="An https page loads something over plain http."),
    # --- performance ---------------------------------------------------------------------------------
    _audit("TTFB_SLOW", "dev_fix", why="The server answers slowly."),
    _audit("CWV_LCP_POOR", "dev_fix", why="The main content takes too long to appear."),
    _audit("CWV_INP_POOR", "dev_fix", why="The page reacts slowly to clicks and taps."),
    _audit("CWV_CLS_POOR", "dev_fix", why="The layout jumps while the page loads."),
    # --- structured data -----------------------------------------------------------------------------
    _audit(
        "SCHEMA_PARSE_ERROR",
        "fix_jsonld",
        why="The structured data on the page is broken, so search engines ignore it.",
    ),
    _audit(
        "SCHEMA_REQUIRED_PROP_MISSING",
        "fix_jsonld",
        why="The structured data lacks properties that search engines and AI assistants need.",
    ),
    _audit(
        "ORG_SCHEMA_MISSING",
        "add_jsonld",
        why="The homepage does not say, in structured data, who the organisation is.",
    ),
    # --- AI readiness --------------------------------------------------------------------------------
    _audit(
        "ANSWER_BLOCK_MISSING",
        "refresh_content",
        why="A question heading is not followed by a short direct answer that AI assistants can quote.",
    ),
    _audit("AUTHOR_DATE_MISSING", "author_eeat", why="Articles do not show who wrote them or when."),
    _audit(
        "AI_SEARCH_BOT_BLOCKED",
        "robots_review",
        why="robots.txt blocks AI search crawlers, so the site cannot be quoted in AI answers.",
    ),
    _audit(
        "JS_DEPENDENT_CONTENT",
        "ssr_task",
        why="The content only appears after JavaScript runs; most AI crawlers never see it.",
    ),
    _audit(
        "AI_TRAINING_BOT_BLOCKED",
        reason="Blocking AI training crawlers is a legitimate owner choice and does not affect search or AI answers.",
    ),
    _audit(
        "LLMS_TXT",
        reason="No major search or AI engine has confirmed using llms.txt, so the audit shows it for information only.",
    ),
    # --- Search Console opportunities ----------------------------------------------------------------
    _gsc(
        "CTR_GAP",
        "rewrite_title",
        "rewrite_meta",
        why="The page ranks well but gets fewer clicks than pages at that position usually do.",
    ),
    _gsc(
        "STRIKING_DISTANCE",
        "add_internal_links",
        why="The page ranks just below the first results for a query people search often.",
    ),
    _gsc(
        "DECAY",
        "refresh_content",
        why="The page is getting clearly fewer clicks than in the previous 28 days.",
    ),
    _gsc("CANNIBALISATION", "consolidate_pages", why="Several pages compete for the same query."),
)

MAPPINGS: dict[str, SourceMapping] = {m.source: m for m in _MAPPINGS}
if len(MAPPINGS) != len(_MAPPINGS):  # a duplicated source would silently shadow another
    raise RuntimeError("duplicate source in the planner mapping table")


def mapping_for(source: str) -> SourceMapping | None:
    return MAPPINGS.get(source)


def sources_for_action(action_type: str) -> list[str]:
    return [m.source for m in _MAPPINGS if action_type in m.actions]
