"""Expected gain, confidence and priority of an action. Pure functions.

Units: expected gain is the estimated number of EXTRA CLICKS PER 28 DAYS.

Two kinds of estimate, and the difference is always shown to the person (`evidence["gain_basis"]`):

* ``gsc``   - measured inputs. For CTR-type actions: ``impressions_28d x (curve_ctr(expected_position) - actual_ctr)``
              where the curve is the site's own CTR curve (``stats/ctr_curve.py``).
* ``prior`` - there is nothing to measure yet (a missing title on a page with no search data, a canonical fix,
              ...). The numbers in ``FLAT_PRIOR`` / ``RELATIVE_UPLIFT`` are our own guesses, deliberately small,
              labelled "prior, not measured" in the UI and in docs/METHODOLOGY.md. Phase 5's experiments replace
              them with measured win rates.

``priority = expected_gain x prior_win_rate(type) / effort``; the win rate is 0.5 until Phase 5 learns it.
"""

from __future__ import annotations

import math

from searchos.stats.ctr_curve import CtrCurve

PRIOR_WIN_RATE = 0.5
# Share of a CTR-gap's missing clicks credited to the title and to the description (they act on the same snippet;
# the two shares add up to 1 so the plan's total does not count the gap twice).
CTR_GAP_SHARE: dict[str, float] = {"rewrite_title": 0.6, "rewrite_meta": 0.4}
# A striking-distance page is assumed to move up this many positions once better linked (a prior).
STRIKING_POSITIONS_GAINED = 2

# Extra clicks / 28 days when nothing can be measured. Prior, not measured.
FLAT_PRIOR: dict[str, float] = {
    "rewrite_title": 3.0,
    "rewrite_meta": 2.0,
    "add_alt": 0.5,
    "add_jsonld": 4.0,
    "fix_jsonld": 2.0,
    "add_internal_links": 2.0,
    "fix_canonical": 4.0,
    "collapse_redirect": 1.0,
    "fix_link": 1.0,
    "refresh_content": 6.0,
    "consolidate_pages": 8.0,
    "ssr_task": 10.0,
    "robots_review": 6.0,
    "noindex_review": 2.0,
    "author_eeat": 3.0,
    "earn_links": 5.0,
    "collect_reviews": 2.0,
    "digital_pr": 4.0,
}
# dev_fix covers many different checks; its prior depends on the check's severity.
DEV_FIX_PRIOR_BY_SEVERITY: dict[str, float] = {
    "critical": 4.0,
    "high": 2.5,
    "medium": 1.0,
    "low": 0.5,
    "info": 0.0,
}
# A page that already gets clicks gains more from a fix: prior uplift as a share of its clicks over 28 days.
RELATIVE_UPLIFT: dict[str, float] = {
    "rewrite_title": 0.05,
    "rewrite_meta": 0.03,
    "add_jsonld": 0.03,
    "fix_jsonld": 0.03,
    "fix_canonical": 0.05,
    "add_internal_links": 0.03,
    "refresh_content": 0.10,
    "add_alt": 0.002,
}
# Confidence of an estimate made from a prior alone.
PRIOR_CONFIDENCE = 0.2
_CURVE_FACTOR = {"site": 1.0, "pooled": 0.7, "default": 0.5}


def flat_prior(action_type: str, severity: str | None = None) -> float:
    if action_type == "dev_fix":
        return DEV_FIX_PRIOR_BY_SEVERITY.get(severity or "medium", 1.0)
    return FLAT_PRIOR.get(action_type, 1.0)


def prior_gain(action_type: str, page_clicks_28d: int = 0, severity: str | None = None) -> float:
    """Gain of a fix with nothing measurable: the larger of the flat prior and a small share of the page's clicks."""
    flat = flat_prior(action_type, severity)
    uplift = RELATIVE_UPLIFT.get(action_type, 0.0) * max(0, page_clicks_28d)
    return round(max(flat, uplift), 2)


def ctr_gain(impressions: int, actual_ctr: float, expected_ctr: float) -> float:
    """Extra clicks per 28 days if the page got the CTR the curve expects: impressions x (expected - actual)."""
    if impressions <= 0:
        return 0.0
    return max(0.0, impressions * (expected_ctr - actual_ctr))


def striking_gain(impressions: int, position: float, actual_ctr: float, curve: CtrCurve) -> float:
    """Extra clicks per 28 days if a page ranking at `position` moved up `STRIKING_POSITIONS_GAINED` places.

    Measured at the page's own CTR or the curve's CTR for its current position, whichever is higher, so that a page
    already beating the curve is not credited with a gain it has in effect already.
    """
    target = max(1.0, position - STRIKING_POSITIONS_GAINED)
    baseline = max(actual_ctr, curve.at(position))
    return max(0.0, impressions * (curve.at(target) - baseline))


def confidence(impressions: int, curve_confidence: str | None) -> float:
    """0..1 from the volume of data and where the CTR curve came from (own data > pooled > industry default)."""
    volume = impressions / (impressions + 500.0) if impressions > 0 else 0.0
    return round(min(0.95, volume * _CURVE_FACTOR.get(curve_confidence or "default", 0.5)), 3)


def priority(expected_gain: float, effort: int, win_rate: float = PRIOR_WIN_RATE) -> float:
    if effort < 1:
        raise ValueError("effort must be at least 1")
    value = expected_gain * win_rate / effort
    return round(value, 4) if math.isfinite(value) else 0.0
