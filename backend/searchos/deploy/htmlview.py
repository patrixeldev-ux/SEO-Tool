"""Reading the raw HTML of a page: what is there now (before a deploy), and whether a change is there (after).

Raw HTML only - what a crawler that does not run JavaScript sees. Used for the "before" snapshot, the
staleness check ("did someone edit the page since the audit?") and the post-deploy verification.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin

from selectolax.lexbor import LexborHTMLParser

from searchos.audit.schema_rules import rule_family, types_of

_WS = re.compile(r"\s+")


def norm(value: Any) -> str:
    return _WS.sub(" ", value).strip() if isinstance(value, str) else ""


def absolute(base: str, href: str | None) -> str:
    return urljoin(base, href.strip()) if href and href.strip() else ""


def same_url(a: str, b: str) -> bool:
    return a.rstrip("/") == b.rstrip("/")


def parse(html: str) -> LexborHTMLParser:
    return LexborHTMLParser(html)


def read_title(tree: LexborHTMLParser) -> str | None:
    node = tree.css_first("head title") or tree.css_first("title")
    return norm(node.text()) if node is not None else None


def read_meta_description(tree: LexborHTMLParser) -> str | None:
    for node in tree.css("meta"):
        if (node.attributes.get("name") or "").strip().lower() == "description":
            return norm(node.attributes.get("content") or "")
    return None


def read_canonical(tree: LexborHTMLParser, page_url: str) -> str | None:
    for node in tree.css("link"):
        rels = (node.attributes.get("rel") or "").lower().split()
        if "canonical" in rels:
            return absolute(page_url, node.attributes.get("href"))
    return None


def read_alt(tree: LexborHTMLParser, page_url: str, src: str) -> str | None:
    want = absolute(page_url, src)
    for node in tree.css("img"):
        if same_url(absolute(page_url, node.attributes.get("src")), want):
            alt = node.attributes.get("alt")
            return norm(alt) if alt is not None else None
    return None


@dataclass(slots=True)
class JsonLd:
    entities: list[dict[str, Any]]
    parse_errors: int


def read_jsonld(tree: LexborHTMLParser) -> JsonLd:
    entities: list[dict[str, Any]] = []
    errors = 0
    for node in tree.css('script[type="application/ld+json"]'):
        try:
            data = json.loads(node.text(deep=True) or "")
        except ValueError:
            errors += 1
            continue
        items = data if isinstance(data, list) else [data]
        for item in items:
            if isinstance(item, dict) and isinstance(item.get("@graph"), list):
                entities.extend(g for g in item["@graph"] if isinstance(g, dict))
            elif isinstance(item, dict):
                entities.append(item)
    return JsonLd(entities, errors)


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def entity_present(entities: list[dict[str, Any]], wanted: dict[str, Any]) -> bool:
    target = canonical_json(wanted)
    return any(canonical_json(e) == target for e in entities)


def has_link(tree: LexborHTMLParser, page_url: str, anchor: str, to: str) -> bool:
    want = absolute(page_url, to)
    for node in tree.css("a[href]"):
        if same_url(absolute(page_url, node.attributes.get("href")), want) and norm(anchor) in norm(
            node.text(deep=True)
        ):
            return True
    return False


def body_text(tree: LexborHTMLParser) -> str:
    body = tree.body
    return norm(body.text(deep=True, separator=" ")) if body is not None else ""


def links_to(tree: LexborHTMLParser, page_url: str, to: str) -> bool:
    want = absolute(page_url, to)
    return any(same_url(absolute(page_url, n.attributes.get("href")), want) for n in tree.css("a[href]"))


# ---- per-action questions --------------------------------------------------------------------------------
def observed_value(action_type: str, field: str, html: str, page_url: str) -> Any:
    """The live value of what the action edits, in a JSON-able shape (stored as the before / after snapshot)."""
    tree = parse(html)
    if action_type == "rewrite_title":
        return read_title(tree)
    if action_type == "rewrite_meta":
        return read_meta_description(tree)
    if action_type == "fix_canonical":
        return read_canonical(tree, page_url)
    if action_type == "add_alt":
        return read_alt(tree, page_url, field.removeprefix("alt:"))
    if action_type in ("add_jsonld", "fix_jsonld"):
        ld = read_jsonld(tree)
        return {"entities": ld.entities, "parse_errors": ld.parse_errors}
    if action_type == "add_internal_links":
        return {"links_to": [n.attributes.get("href") for n in tree.css("a[href]")][:300]}
    return None


def same_value(a: Any, b: Any) -> bool:
    if a in (None, "") and b in (None, ""):
        return True
    if isinstance(a, str) and isinstance(b, str):
        return norm(a) == norm(b)
    return canonical_json(a) == canonical_json(b)


def is_stale(
    action_type: str, field: str, current_value: Any, proposed: Any, html: str, page_url: str
) -> str | None:
    """None when the page still looks like it did when the action was written; else why the action is stale."""
    tree = parse(html)
    if action_type in ("rewrite_title", "rewrite_meta", "fix_canonical", "add_alt"):
        live = observed_value(action_type, field, html, page_url)
        if (
            action_type == "fix_canonical"
            and isinstance(current_value, str)
            and isinstance(live, str)
            and same_url(absolute(page_url, current_value), live)
        ):
            return None
        if not same_value(live, current_value):
            return "The page has changed since the audit: its current value is not the one this action was written for."
        return None
    if action_type in ("add_jsonld", "fix_jsonld"):
        ld = read_jsonld(tree)
        if action_type == "add_jsonld":
            family = field.split(":", 1)[1] if ":" in field else ""
            if any(rule_family(t) == family for e in ld.entities for t in types_of(e)):
                return "The page now has this structured data already."
            return None
        if field == "jsonld:parse_error":
            return (
                None if ld.parse_errors else "The broken structured data on this page has been fixed already."
            )
        if isinstance(current_value, dict) and not entity_present(ld.entities, current_value):
            return "The page's structured data has changed since the audit."
        return None
    if action_type == "add_internal_links":
        text = body_text(tree)
        links = proposed.get("links", []) if isinstance(proposed, dict) else []
        usable = [
            ln
            for ln in links
            if norm(ln.get("anchor")) in text and not links_to(tree, page_url, str(ln.get("to")))
        ]
        if not usable:
            return "The text this link would use is gone from the page, or the page links there already."
        return None
    return None


def proposed_present(
    action_type: str, field: str, proposed: Any, html: str, page_url: str
) -> tuple[bool, Any]:
    """(is the new value in the raw HTML?, what was observed)."""
    tree = parse(html)
    if action_type in ("rewrite_title", "rewrite_meta", "add_alt"):
        live = observed_value(action_type, field, html, page_url)
        return same_value(live, proposed), live
    if action_type == "fix_canonical":
        live = read_canonical(tree, page_url)
        return (live is not None and same_url(live, absolute(page_url, str(proposed)))), live
    if action_type in ("add_jsonld", "fix_jsonld"):
        ld = read_jsonld(tree)
        entity = proposed if isinstance(proposed, dict) else json.loads(str(proposed))
        found = entity_present(ld.entities, entity)
        observed: Any = {"entity_found": found, "entities": len(ld.entities), "parse_errors": ld.parse_errors}
        if action_type == "fix_jsonld" and field == "jsonld:parse_error":
            return found, observed
        return found, observed
    if action_type == "add_internal_links":
        links = proposed.get("links", []) if isinstance(proposed, dict) else []
        results = [has_link(tree, page_url, str(ln.get("anchor")), str(ln.get("to"))) for ln in links]
        # at least one of the (up to three) links must be present; a link whose text is gone is skipped by the deployers
        return any(results), {"links_found": results}
    return False, None
