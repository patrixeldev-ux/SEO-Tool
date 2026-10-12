"""Plain-text hand-off for a developer: what to change, where, from what to what, and why.

Pure functions of `Action` rows. The text is meant to be pasted into a ticket or an email, so it contains no
Search OS jargon, names every URL in full, and gives ready-to-paste HTML where there is some.
"""

from __future__ import annotations

import html
import json
from collections.abc import Iterable
from typing import Any

from searchos.models import Action
from searchos.planner.catalog import ACTION_TYPES

TICKET_TYPES = ("dev_fix", "ssr_task", "robots_review", "noindex_review")
MAX_URLS = 30


def _a(text: str) -> str:
    return html.escape(text, quote=True)


def _short(value: Any, limit: int = 300) -> str:
    if value is None:
        return "(none)"
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def snippet(action: Action) -> list[str]:
    """Lines telling a developer exactly what to put on the page."""
    value = action.proposed_value
    t = action.type
    if value is None:
        return []
    if t == "rewrite_title":
        return [f"Replace the <title> element with: <title>{_a(str(value))}</title>"]
    if t == "rewrite_meta":
        return [f'Set: <meta name="description" content="{_a(str(value))}">']
    if t == "add_alt":
        src = action.field.split(":", 1)[-1]
        return [f'On the image {src}: alt="{_a(str(value))}"']
    if t in ("add_jsonld", "fix_jsonld"):
        body = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2)
        what = "Replace the existing block" if t == "fix_jsonld" else "Add this block in <head>"
        return [f"{what}:", '<script type="application/ld+json">', body, "</script>"]
    if t == "fix_canonical":
        return [f'Set: <link rel="canonical" href="{_a(str(value))}">']
    if t == "collapse_redirect" and isinstance(value, dict):
        return [
            f"Make {value.get('from')} answer with a single {value.get('status')} redirect to {value.get('to')}."
        ]
    if t == "fix_link" and isinstance(value, dict):
        if value.get("mode") == "remove":
            return [f"Remove the link to {value.get('from')} from this page (it leads to an error page)."]
        return [f"Change the link {value.get('from')} to {value.get('to')}."]
    if t == "add_internal_links" and isinstance(value, dict):
        return [
            f'In the existing text, link the words "{link.get("anchor")}" to {link.get("to")}'
            for link in value.get("links", [])
        ]
    if isinstance(value, dict) and isinstance(value.get("steps"), list):
        return [f"- {s}" for s in value["steps"]]
    return [_short(value, 600)]


def render_item(number: int, action: Action) -> list[str]:
    info = ACTION_TYPES[action.type]
    head = f"{number}. {info.label}"
    if action.target_url:
        head += f" - {action.target_url}"
    lines = [head]
    ev = action.evidence or {}
    why = ev.get("why") or []
    if why:
        lines.append(f"   Why: {why[0]}")
    if action.type not in TICKET_TYPES and action.current_value is not None:
        lines.append(f"   Now: {_short(action.current_value)}")
    for line in snippet(action):
        lines.append(f"   {line}")
    urls = ev.get("urls") or []
    if urls and action.type in TICKET_TYPES:
        shown = urls[:MAX_URLS]
        lines.append(f"   Affected URLs ({len(urls)}):")
        lines.extend(f"     {u}" for u in shown)
        if len(urls) > len(shown):
            lines.append(f"     ... and {len(urls) - len(shown)} more")
    if action.edited_by_user:
        lines.append("   (The wording was edited by a person.)")
    return lines


def render(site_name: str, domain: str, actions: Iterable[Action], *, generated_on: str) -> str:
    rows = list(actions)
    out = [
        f"Developer hand-off for {site_name} ({domain})",
        f"Prepared {generated_on}. {len(rows)} change(s). Each one was reviewed and approved by a person.",
        "",
    ]
    if not rows:
        out.append("Nothing is approved yet. Approve actions in the plan, then come back.")
        return "\n".join(out) + "\n"
    sections: dict[str, list[Action]] = {"Page changes": [], "Technical tasks": []}
    for action in rows:
        key = "Technical tasks" if action.type in TICKET_TYPES else "Page changes"
        sections[key].append(action)
    n = 0
    for title, items in sections.items():
        if not items:
            continue
        out.append(title.upper())
        out.append("")
        for action in items:
            n += 1
            out.extend(render_item(n, action))
            out.append("")
    out.append("After deploying, re-run the audit in Search OS to confirm each change is live.")
    return "\n".join(out) + "\n"
