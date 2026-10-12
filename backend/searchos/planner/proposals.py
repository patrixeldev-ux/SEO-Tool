"""Validating a proposed value for an action, whoever wrote it (the AI, the planner or a person editing in the UI).

`validate_proposal` is the only gate: it returns the cleaned value plus a list of problems; no problems means the
value may be stored. It uses the validators in `validators.py` and the page data in the `PlanContext`.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Collection
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from searchos.audit.schema_rules import rule_family
from searchos.planner import validators as v
from searchos.planner.model import PlanContext, PlanPage

MAX_BRIEF_STEPS = 30
MAX_BRIEF_STEP_CHARS = 1500
_MONTHS = {
    m: i
    for i, m in enumerate(
        [
            "january",
            "february",
            "march",
            "april",
            "may",
            "june",
            "july",
            "august",
            "september",
            "october",
            "november",
            "december",
        ],
        1,
    )
}
_ISO_DATE = re.compile(r"\b(19|20)\d\d-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])\b")
_LONG_DATE = re.compile(
    r"\b(?:(?P<d1>\d{1,2})\s+(?P<m1>[A-Za-z]{3,9})\.?,?\s+(?P<y1>(?:19|20)\d\d)|(?P<m2>[A-Za-z]{3,9})\.?\s+(?P<d2>\d{1,2})(?:st|nd|rd|th)?,?\s+(?P<y2>(?:19|20)\d\d))\b"
)
_BYLINE = re.compile(r"\bBy\s+([A-Z][\w'’.-]+(?:\s+[A-Z][\w'’.-]+){1,2})")  # noqa: RUF001


# ---- uniqueness of titles and descriptions ---------------------------------------------------------------
@dataclass(slots=True)
class Uniqueness:
    """How often each title / description is in use across the site (current values and accepted proposals)."""

    titles: Counter[str] = field(default_factory=Counter)
    descriptions: Counter[str] = field(default_factory=Counter)

    @classmethod
    def from_pages(cls, pages: Collection[PlanPage]) -> Uniqueness:
        u = cls()
        for p in pages:
            if p.is_indexable:
                if p.title and p.title.strip():
                    u.titles[v.norm_key(p.title)] += 1
                if p.meta_description and p.meta_description.strip():
                    u.descriptions[v.norm_key(p.meta_description)] += 1
        return u

    @staticmethod
    def _taken(counter: Counter[str], own: str | None) -> set[str]:
        own_key = v.norm_key(own) if own else None
        return {k for k, n in counter.items() if n - (1 if k == own_key else 0) > 0}

    def taken_titles(self, own: str | None) -> set[str]:
        return self._taken(self.titles, own)

    def taken_descriptions(self, own: str | None) -> set[str]:
        return self._taken(self.descriptions, own)

    def swap_title(self, old: str | None, new: str) -> None:
        if old and self.titles[v.norm_key(old)] > 0:
            self.titles[v.norm_key(old)] -= 1
        self.titles[v.norm_key(new)] += 1

    def swap_description(self, old: str | None, new: str) -> None:
        if old and self.descriptions[v.norm_key(old)] > 0:
            self.descriptions[v.norm_key(old)] -= 1
        self.descriptions[v.norm_key(new)] += 1


# ---- facts the page itself states ---------------------------------------------------------------------------
def dates_in(text: str) -> list[str]:
    """ISO dates for every date written in `text` ("2026-03-14", "14 March 2026", "March 14, 2026")."""
    found: list[tuple[int, str]] = []
    for m in _ISO_DATE.finditer(text):
        found.append((m.start(), m.group(0)))
    for m in _LONG_DATE.finditer(text):
        month = _MONTHS.get((m.group("m1") or m.group("m2")).lower())
        if month is None:
            continue
        day = int(m.group("d1") or m.group("d2"))
        year = int(m.group("y1") or m.group("y2"))
        if 1 <= day <= 31:
            found.append((m.start(), f"{year:04d}-{month:02d}-{day:02d}"))
    return [d for _pos, d in sorted(found)]


def author_in(text: str) -> str | None:
    m = _BYLINE.search(text)
    return m.group(1).strip(" .,") if m else None


def title_segments(title: str | None) -> list[str]:
    return [s.strip() for s in re.split(r"\s+[|–—-]\s+|\s*[|–—]\s*", title or "") if s.strip()]  # noqa: RUF001


def brand_name(ctx: PlanContext) -> str:
    """The name to put in titles: the site's display name, or the domain's main label when that is all we have."""
    name = (ctx.site_name or "").strip()
    domain_like = name.lower() in (ctx.domain.lower(), f"www.{ctx.domain.lower()}") or (
        "." in name and " " not in name
    )
    if name and not domain_like:
        return name
    label = ctx.domain.split(".")[0]
    return label.replace("-", " ").title()


def page_facts(ctx: PlanContext, page: PlanPage, schema_type: str) -> dict[str, Any]:
    """Values the page states about itself, which a JSON-LD block may use. Nothing here is guessed."""
    facts: dict[str, Any] = {}
    candidates = [brand_name(ctx), *title_segments(page.title), *page.h1]
    if schema_type in ("Organization", "LocalBusiness"):
        facts["name_candidates"] = list(dict.fromkeys(c for c in candidates if c))
        facts["name"] = facts["name_candidates"][0] if facts["name_candidates"] else None
        facts["url"] = ctx.origin + "/"
    else:
        facts["url"] = page.url
        headline = (page.h1[0] if page.h1 else page.title) or ""
        facts["headline"] = headline.strip()
        facts["name"] = headline.strip()
        author = author_in(page.text)
        if author:
            facts["author"] = author
        dates = dates_in(page.text)
        if dates:
            facts["date_published"] = dates[0]
            facts["dates_found"] = dates[:5]
    return {k: val for k, val in facts.items() if val}


def ground_texts(ctx: PlanContext, page: PlanPage, facts: dict[str, Any]) -> list[str]:
    ground = [page.text, page.title or "", page.meta_description or "", *page.h1, *page.h2]
    for key in ("dates_found",):
        ground.extend(str(d) for d in facts.get(key, []))
    if facts.get("date_published"):
        ground.append(str(facts["date_published"]))
    ground.append(brand_name(ctx))
    ground.extend(str(c) for c in facts.get("name_candidates", []))
    return ground


def site_hosts(ctx: PlanContext) -> set[str]:
    host = urlsplit(ctx.origin).netloc.lower()
    return {host, host.removeprefix("www."), f"www.{host.removeprefix('www.')}", ctx.domain.lower()}


# ---- the gate ----------------------------------------------------------------------------------------------
def _same_site_url(ctx: PlanContext, url: object) -> list[str]:
    if not isinstance(url, str) or not url.strip():
        return ["The URL is empty."]
    parts = urlsplit(url.strip())
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return ["The URL must be absolute (start with https://)."]
    if parts.netloc.lower() not in site_hosts(ctx):
        return ["The URL must be on this site."]
    if parts.fragment:
        return ["The URL must not contain a #fragment."]
    return []


def _healthy(page: PlanPage | None) -> bool:
    return page is not None and page.is_indexable and page.status_code == 200 and not page.redirect_chain


def validate_brief(value: object) -> list[str]:
    if not isinstance(value, dict) or value.get("kind") != "brief":
        return ['A brief must be an object with kind "brief".']
    steps = value.get("steps")
    if not isinstance(steps, list) or not steps or not all(isinstance(s, str) and s.strip() for s in steps):
        return ["The brief needs a list of steps (text)."]
    if len(steps) > MAX_BRIEF_STEPS or any(len(s) > MAX_BRIEF_STEP_CHARS for s in steps):
        return [f"A brief may have at most {MAX_BRIEF_STEPS} steps of {MAX_BRIEF_STEP_CHARS} characters."]
    return []


def validate_proposal(
    ctx: PlanContext,
    *,
    action_type: str,
    target_url: str | None,
    field_name: str,
    value: Any,
    current_value: Any = None,
    evidence: dict[str, Any] | None = None,
    uniqueness: Uniqueness | None = None,
) -> tuple[Any, list[str]]:
    """(cleaned value, problems). The cleaned value is what gets stored; it is None when there are problems."""
    evidence = evidence or {}
    uniq = uniqueness or Uniqueness.from_pages(ctx.pages.values())
    page = ctx.page(target_url)
    own_title = page.title if page else None

    if action_type == "rewrite_title":
        problems = v.validate_title(
            value,
            taken=uniq.taken_titles(own_title),
            current=current_value if isinstance(current_value, str) else None,
        )
        return (v.squash(value), []) if not problems else (None, problems)

    if action_type == "rewrite_meta":
        own = page.meta_description if page else None
        problems = v.validate_meta(
            value,
            taken=uniq.taken_descriptions(own),
            current=current_value if isinstance(current_value, str) else None,
        )
        return (v.squash(value), []) if not problems else (None, problems)

    if action_type == "add_alt":
        problems = v.validate_alt(value, filename=str(evidence.get("filename") or ""))
        return (v.squash(value), []) if not problems else (None, problems)

    if action_type in ("add_jsonld", "fix_jsonld"):
        if page is None:
            return None, ["The page is not in the latest crawl."]
        expected = str(evidence.get("schema_type") or field_name.split(":", 1)[-1])
        facts = page_facts(ctx, page, rule_family(expected) or expected)
        existing = current_value if isinstance(current_value, dict) and "@type" in current_value else None
        entity, problems = v.validate_jsonld(
            value,
            expected_type=expected,
            ground=ground_texts(ctx, page, facts),
            hosts=site_hosts(ctx),
            existing=existing,
        )
        if entity is not None and existing is not None:
            for key, old in existing.items():
                if entity.get(key) != old:
                    problems.append(f'The existing property "{key}" must be kept unchanged.')
        return (entity, []) if not problems else (None, list(dict.fromkeys(problems)))

    if action_type == "add_internal_links":
        if page is None:
            return None, ["The page is not in the latest crawl."]
        links = value.get("links") if isinstance(value, dict) else None
        if not isinstance(links, list) or not links or len(links) > 3:
            return None, ["Propose between 1 and 3 links."]
        problems = []
        cleaned: list[dict[str, str]] = []
        for item in links:
            if not isinstance(item, dict):
                problems.append("Each link needs a target and an anchor.")
                continue
            to, anchor = item.get("to"), item.get("anchor")
            target = ctx.page(to if isinstance(to, str) else None)
            already = (
                frozenset({str(to)}) if page.url in ctx.linked_from.get(str(to), frozenset()) else frozenset()
            )
            link_target = (
                v.LinkTarget(target.url, target.status_code, target.is_indexable, bool(target.redirect_chain))
                if target is not None
                else None
            )
            problems += v.validate_internal_link(
                source_url=page.url,
                source_text=page.text,
                target=link_target,
                already_linked=already,
                anchor=anchor,
            )
            if isinstance(to, str) and isinstance(anchor, str):
                cleaned.append({"to": to, "anchor": v.squash(anchor)})
        return (None, list(dict.fromkeys(problems))) if problems else ({"links": cleaned}, [])

    if action_type == "fix_canonical":
        problems = _same_site_url(ctx, value)
        if not problems:
            target = ctx.page(v.squash(str(value)))
            if target is not None and not _healthy(target):
                problems.append("The canonical must point at an indexable page that answers 200.")
        return (v.squash(str(value)), []) if not problems else (None, problems)

    if action_type == "collapse_redirect":
        if not isinstance(value, dict):
            return None, ["Propose {from, to, status}."]
        problems = _same_site_url(ctx, value.get("to"))
        if value.get("status") not in (301, 308):
            problems.append("Use a permanent redirect (301 or 308).")
        if value.get("from") != target_url:
            problems.append("The redirect must start at this page's URL.")
        if not problems:
            end = ctx.page(str(value["to"]))
            if end is not None and not _healthy(end):
                problems.append(
                    "The destination must be an indexable page that answers 200 without redirecting."
                )
        return (
            (None, problems)
            if problems
            else ({"from": value["from"], "to": str(value["to"]), "status": value["status"]}, [])
        )

    if action_type == "fix_link":
        if not isinstance(value, dict):
            return None, ["Propose {from, to, mode}."]
        mode = value.get("mode")
        if mode == "remove":
            return {"from": value.get("from"), "to": None, "mode": "remove"}, []
        problems = (
            _same_site_url(ctx, value.get("to"))
            if mode == "replace"
            else ['The mode must be "replace" or "remove".']
        )
        if not problems and not _healthy(ctx.page(str(value["to"]))):
            problems.append("The new target must be an indexable page that answers 200.")
        return (
            (None, problems)
            if problems
            else ({"from": value.get("from"), "to": str(value["to"]), "mode": "replace"}, [])
        )

    # briefs and manual tasks
    problems = validate_brief(value)
    return (value, []) if not problems else (None, problems)
