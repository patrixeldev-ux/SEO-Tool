"""Builder for `add_internal_links`: which pages should link to which, and with what words.

Targets are pages that need inbound links: orphans (no page links to them), pages deeper than 4 clicks, and
"striking distance" pages that rank just below the first results. Sources are other healthy pages whose text is
topically close (TF-IDF cosine >= `SIMILARITY_THRESHOLD`), that do not link to the target yet, and that contain a
phrase naming the target. Everything is deterministic and needs no AI.

One action per source page, at most 3 links in it; at most 3 source pages per target.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from searchos.planner import gain
from searchos.planner.model import Candidate, Finding, PlanContext, PlanPage
from searchos.planner.similarity import (
    SIMILARITY_THRESHOLD,
    TextIndex,
    pick_anchor,
    ranked_sources,
    term_weights,
)
from searchos.planner.validators import LinkTarget, validate_internal_link

MAX_SOURCES_PER_TARGET = 3
MAX_LINKS_PER_SOURCE = 3
SHALLOW_DEPTH = 2  # a deep page is linked from pages at most this many clicks from the homepage

REASON_LABEL = {
    "ORPHAN_PAGE": "orphan",
    "STRIKING_DISTANCE": "striking_distance",
    "DEEP_PAGE": "deep_page",
}
SOURCE_OF_REASON = {label: source for source, label in REASON_LABEL.items()}


@dataclass(slots=True)
class Need:
    target: PlanPage
    reasons: list[str] = field(default_factory=list)
    queries: list[str] = field(default_factory=list)
    gain_clicks: float = 0.0
    measured: bool = False
    impressions: int = 0


@dataclass(slots=True)
class Proposal:
    source: PlanPage
    target: PlanPage
    anchor: str
    similarity: float
    need: Need


def _link_target(page: PlanPage) -> LinkTarget:
    return LinkTarget(page.url, page.status_code, page.is_indexable, redirects=bool(page.redirect_chain))


def collect_needs(ctx: PlanContext, findings: list[Finding]) -> dict[str, Need]:
    needs: dict[str, Need] = {}
    for f in findings:
        page = ctx.page(f.url)
        if page is None or not page.is_indexable or not page.has_text:
            continue
        need = needs.setdefault(page.url, Need(page))
        label = REASON_LABEL.get(f.source, f.source.lower())
        if label not in need.reasons:
            need.reasons.append(label)
        if f.source == "STRIKING_DISTANCE" and ctx.curve is not None:
            d = f.details
            need.gain_clicks += gain.striking_gain(
                int(d.get("impressions") or 0),
                float(d.get("position") or 0),
                float(d.get("ctr") or 0),
                ctx.curve,
            )
            need.measured = True
            need.impressions += int(d.get("impressions") or 0)
            if f.query:
                need.queries.append(f.query)
    return needs


def build_add_internal_links(ctx: PlanContext, findings: list[Finding]) -> list[Candidate]:
    needs = collect_needs(ctx, findings)
    if not needs:
        return []
    pages = {u: p for u, p in ctx.pages.items() if p.is_indexable and p.has_text}
    index = TextIndex.build(
        {u: f"{p.title or ''} {p.title or ''} {' '.join(p.h1)} {p.text}" for u, p in pages.items()}
    )
    home = ctx.homepage_url
    proposals: list[Proposal] = []
    for url in sorted(needs):
        need = needs[url]
        target = need.target
        scores = index.similarity_to(url)
        eligible = [
            u
            for u, p in pages.items()
            if u != url
            and p.status_code == 200
            and (p.inlinks > 0 or u == home)
            and ("deep_page" not in need.reasons or (p.depth is not None and p.depth <= SHALLOW_DEPTH))
        ]
        weights = term_weights(
            [target.title or "", *target.h1, *need.queries[:3]],
            index.top_terms(url, 8),
        )
        linked_by = ctx.linked_from.get(url, frozenset())
        chosen = 0
        for source_url, similarity in ranked_sources(scores, eligible, SIMILARITY_THRESHOLD):
            source = pages[source_url]
            anchor = pick_anchor(source.text, weights)
            if anchor is None:
                continue
            problems = validate_internal_link(
                source_url=source_url,
                source_text=source.text,
                target=_link_target(target),
                already_linked=frozenset({url}) if source_url in linked_by else frozenset(),
                anchor=anchor,
            )
            if problems:
                continue
            proposals.append(Proposal(source, target, anchor, similarity, need))
            chosen += 1
            if chosen >= MAX_SOURCES_PER_TARGET:
                break
        if chosen == 0:
            ctx.skipped.append(
                {
                    "url": url,
                    "reason": "no related page with a fitting phrase was found to link from (add content or link by hand)",
                }
            )

    per_target: dict[str, int] = defaultdict(int)
    for p in proposals:
        per_target[p.target.url] += 1
    by_source: dict[str, list[Proposal]] = defaultdict(list)
    for p in proposals:
        by_source[p.source.url].append(p)

    out: list[Candidate] = []
    for source_url in sorted(by_source):
        group = sorted(
            by_source[source_url],
            key=lambda p: (-(p.need.gain_clicks / per_target[p.target.url]), -p.similarity, p.target.url),
        )[:MAX_LINKS_PER_SOURCE]
        links = [{"to": p.target.url, "anchor": p.anchor} for p in group]
        measured = [p for p in group if p.need.measured]
        total_gain = sum(p.need.gain_clicks / per_target[p.target.url] for p in measured)
        why = sorted({r for p in group for r in p.need.reasons})
        out.append(
            Candidate(
                type="add_internal_links",
                target_url=source_url,
                field="internal_links",
                source_kind="gsc"
                if measured and not any("orphan" in p.need.reasons for p in group)
                else "audit",
                sources=sorted({SOURCE_OF_REASON[r] for p in group for r in p.need.reasons}),
                current_value=None,
                proposed_value={"links": links},
                evidence={
                    "why": ["These pages are about the same topic and this page does not link to them yet."],
                    "links": [
                        {
                            "to": p.target.url,
                            "anchor": p.anchor,
                            "similarity": round(p.similarity, 3),
                            "reasons": p.need.reasons,
                            "queries": p.need.queries[:3],
                            "target_depth": p.target.depth,
                            "target_inlinks": p.target.inlinks,
                        }
                        for p in group
                    ],
                    "reasons": why,
                    "note": "Each anchor is a phrase that already appears in this page's text; nothing is rewritten.",
                },
                gain_clicks=round(total_gain, 2) if measured and total_gain > 0 else None,
                impressions=sum(p.need.impressions for p in measured),
            )
        )
    return out
