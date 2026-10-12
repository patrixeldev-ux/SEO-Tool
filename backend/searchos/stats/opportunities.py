"""Opportunity finders over a 28-day window of Search Console data. Pure functions, no database.

Four types, each returning `Opportunity` records that carry the numbers they were decided on (`evidence`):

STRIKING_DISTANCE  page x query ranking on average at position 4-20 with many impressions (>= the site's P75 of
                   page x query impressions). Score = impressions x (CTR at position 3 - CTR at its position):
                   the extra clicks per window if it moved up to the bottom of the top 3.
CTR_GAP            page x query with >= 200 impressions whose actual CTR is *statistically* below what the site's
                   curve expects: the Wilson 95% upper bound of the actual CTR is below the expected CTR.
                   Score = impressions x (expected CTR - actual CTR).
DECAY              page whose clicks fell versus the previous 28 days: previous >= 30 clicks and an exact one-sided
                   binomial test (recent ~ Binomial(recent + previous, 1/2)) gives p < 0.01. Score = clicks lost.
CANNIBALISATION    query on which two or more pages each take >= 20% of the query's impressions (and the query has
                   at least 50 impressions). Score = impressions that go to pages other than the leading one.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

import numpy as np
from scipy import stats as scipy_stats  # type: ignore[import-untyped, unused-ignore]

from searchos.stats.ctr_curve import CtrCurve, bucket_of, wilson_interval

OpportunityType = Literal["STRIKING_DISTANCE", "CTR_GAP", "DECAY", "CANNIBALISATION"]

STRIKING_MIN_POSITION = 4.0
STRIKING_MAX_POSITION = 20.0
STRIKING_REFERENCE_POSITION = 3
STRIKING_IMPRESSION_PERCENTILE = 75.0
CTR_GAP_MIN_IMPRESSIONS = 200
DECAY_MIN_PRIOR_CLICKS = 30
DECAY_ALPHA = 0.01
CANNIBALISATION_MIN_SHARE = 0.20
CANNIBALISATION_MIN_QUERY_IMPRESSIONS = 50


@dataclass(frozen=True, slots=True)
class PageQueryStat:
    """One page x query over the window. `position` is the impression-weighted average position."""

    page: str
    query: str
    impressions: int
    clicks: int
    position: float


@dataclass(frozen=True, slots=True)
class PageClicks:
    page: str
    recent_clicks: int
    prior_clicks: int


@dataclass(frozen=True, slots=True)
class Opportunity:
    type: OpportunityType
    page: str | None
    query: str | None
    score: float
    evidence: dict[str, Any] = field(default_factory=dict)


def _r(value: float, digits: int = 6) -> float:
    return round(float(value), digits)


def impression_percentile(
    stats: Sequence[PageQueryStat], percentile: float = STRIKING_IMPRESSION_PERCENTILE
) -> float:
    """Linear-interpolation percentile of page x query impressions (numpy's default method)."""
    if not stats:
        return 0.0
    return float(np.percentile(np.array([s.impressions for s in stats], dtype=np.float64), percentile))


def striking_distance(stats: Sequence[PageQueryStat], curve: CtrCurve) -> list[Opportunity]:
    p75 = impression_percentile(stats)
    at_three = curve.at(STRIKING_REFERENCE_POSITION)
    found: list[Opportunity] = []
    for s in stats:
        if not STRIKING_MIN_POSITION <= s.position <= STRIKING_MAX_POSITION or s.impressions < p75:
            continue
        expected = curve.at(s.position)
        score = s.impressions * (at_three - expected)
        if score <= 0:
            continue
        found.append(
            Opportunity(
                "STRIKING_DISTANCE",
                s.page,
                s.query,
                _r(score, 3),
                {
                    "impressions": s.impressions,
                    "clicks": s.clicks,
                    "position": _r(s.position, 2),
                    "ctr": _r(s.clicks / s.impressions if s.impressions else 0.0),
                    "impressions_p75": _r(p75, 2),
                    "expected_ctr_at_position": _r(expected),
                    "expected_ctr_at_3": _r(at_three),
                    "extra_clicks_if_position_3": _r(score, 2),
                },
            )
        )
    return sorted(found, key=lambda o: -o.score)


def ctr_gap(stats: Sequence[PageQueryStat], curve: CtrCurve) -> list[Opportunity]:
    found: list[Opportunity] = []
    for s in stats:
        if s.impressions < CTR_GAP_MIN_IMPRESSIONS or bucket_of(s.position) is None:
            continue
        expected = curve.at(s.position)
        actual = s.clicks / s.impressions
        _low, upper = wilson_interval(s.clicks, s.impressions)
        if upper >= expected:
            continue
        found.append(
            Opportunity(
                "CTR_GAP",
                s.page,
                s.query,
                _r(s.impressions * (expected - actual), 3),
                {
                    "impressions": s.impressions,
                    "clicks": s.clicks,
                    "position": _r(s.position, 2),
                    "ctr": _r(actual),
                    "ctr_upper_95": _r(upper),
                    "expected_ctr": _r(expected),
                    "curve_confidence": curve.confidence_at(s.position),
                    "missing_clicks": _r(s.impressions * (expected - actual), 2),
                },
            )
        )
    return sorted(found, key=lambda o: -o.score)


def decay_p_value(recent: int, prior: int) -> float:
    """P(X <= recent) for X ~ Binomial(recent + prior, 0.5): the chance of a drop at least this large if the
    page's click rate had not changed (exact conditional test of equal Poisson rates, one-sided)."""
    n = recent + prior
    if n == 0:
        return 1.0
    return float(scipy_stats.binom.cdf(recent, n, 0.5))


def decay(pages: Sequence[PageClicks]) -> list[Opportunity]:
    found: list[Opportunity] = []
    for p in pages:
        if p.prior_clicks < DECAY_MIN_PRIOR_CLICKS or p.recent_clicks >= p.prior_clicks:
            continue
        p_value = decay_p_value(p.recent_clicks, p.prior_clicks)
        if p_value >= DECAY_ALPHA:
            continue
        lost = p.prior_clicks - p.recent_clicks
        found.append(
            Opportunity(
                "DECAY",
                p.page,
                None,
                float(lost),
                {
                    "clicks_last_28d": p.recent_clicks,
                    "clicks_previous_28d": p.prior_clicks,
                    "clicks_lost": lost,
                    "change_pct": _r(-100.0 * lost / p.prior_clicks, 1),
                    "p_value": p_value,
                    "test": "exact one-sided binomial (recent ~ Binomial(recent+previous, 0.5))",
                },
            )
        )
    return sorted(found, key=lambda o: -o.score)


def cannibalisation(stats: Sequence[PageQueryStat]) -> list[Opportunity]:
    by_query: dict[str, list[PageQueryStat]] = {}
    for s in stats:
        by_query.setdefault(s.query, []).append(s)
    found: list[Opportunity] = []
    for query, rows in by_query.items():
        total = sum(r.impressions for r in rows)
        if total < CANNIBALISATION_MIN_QUERY_IMPRESSIONS:
            continue
        sharing = sorted(
            (r for r in rows if r.impressions / total >= CANNIBALISATION_MIN_SHARE),
            key=lambda r: -r.impressions,
        )
        if len(sharing) < 2:
            continue
        split_away = total - sharing[0].impressions
        found.append(
            Opportunity(
                "CANNIBALISATION",
                sharing[0].page,
                query,
                float(split_away),
                {
                    "query_impressions": total,
                    "impressions_on_other_pages": split_away,
                    "pages": [
                        {
                            "page": r.page,
                            "impressions": r.impressions,
                            "clicks": r.clicks,
                            "position": _r(r.position, 2),
                            "share": _r(r.impressions / total, 4),
                        }
                        for r in sharing
                    ],
                },
            )
        )
    return sorted(found, key=lambda o: -o.score)


def find_opportunities(
    page_query: Sequence[PageQueryStat],
    page_clicks: Sequence[PageClicks] | None,
    curve: CtrCurve,
) -> dict[OpportunityType, list[Opportunity]]:
    """All four finders. `page_clicks=None` skips DECAY (for example when history is incomplete)."""
    result: dict[OpportunityType, list[Opportunity]] = {
        "STRIKING_DISTANCE": striking_distance(page_query, curve),
        "CTR_GAP": ctr_gap(page_query, curve),
        "DECAY": decay(page_clicks) if page_clicks is not None else [],
        "CANNIBALISATION": cannibalisation(page_query),
    }
    return result


__all__ = [
    "Opportunity",
    "OpportunityType",
    "PageClicks",
    "PageQueryStat",
    "cannibalisation",
    "ctr_gap",
    "decay",
    "decay_p_value",
    "find_opportunities",
    "impression_percentile",
    "striking_distance",
]
