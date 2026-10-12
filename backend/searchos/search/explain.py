"""Plain-language explanations of opportunities, built from the evidence the finders stored."""

from __future__ import annotations

from typing import Any

TITLES = {
    "STRIKING_DISTANCE": "Striking distance",
    "CTR_GAP": "Click-through gap",
    "DECAY": "Losing clicks",
    "CANNIBALISATION": "Pages competing",
}


def _pct(value: float, digits: int = 1) -> str:
    return f"{value * 100:.{digits}f}%"


def explain(kind: str, page: str | None, query: str | None, e: dict[str, Any]) -> str:
    """One or two sentences a non-specialist can act on. Never invents a number that is not in `e`."""
    if kind == "STRIKING_DISTANCE":
        return (
            f'"{query}" shows this page on average at position {e["position"]:.1f}, '
            f"{e['impressions']:,} times in 28 days. Pages in the top 3 get about "
            f"{_pct(e['expected_ctr_at_3'])} of clicks, here it is about {_pct(e['expected_ctr_at_position'])}. "
            f"Improving this page for the query could bring roughly {e['extra_clicks_if_position_3']:,.0f} "
            "more clicks per 28 days."
        )
    if kind == "CTR_GAP":
        return (
            f'For "{query}" this page gets {_pct(e["ctr"])} of clicks ({e["clicks"]:,} of '
            f"{e['impressions']:,}) at position {e['position']:.1f}, where your own results usually get about "
            f"{_pct(e['expected_ctr'])}. Even allowing for chance (up to {_pct(e['ctr_upper_95'])}) that is "
            f"too low: the title or description may not be winning the click. About "
            f"{e['missing_clicks']:,.0f} clicks per 28 days are missing."
        )
    if kind == "DECAY":
        return (
            f"This page got {e['clicks_last_28d']:,} clicks in the last 28 days against "
            f"{e['clicks_previous_28d']:,} in the 28 days before ({e['change_pct']:+.1f}%). The drop is "
            f"larger than normal week-to-week noise (p = {e['p_value']:.4f}). Look at what changed on the "
            "page or in the results."
        )
    if kind == "CANNIBALISATION":
        pages = e["pages"]
        return (
            f'"{query}" is shared between {len(pages)} of your pages, each taking at least 20% of its '
            f"{e['query_impressions']:,} impressions. {e['impressions_on_other_pages']:,} impressions go to "
            "pages other than the leading one. Consider merging them or making their focus clearly different."
        )
    return ""
