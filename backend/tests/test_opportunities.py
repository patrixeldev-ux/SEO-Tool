"""Opportunity finders on synthetic data with known answers (numbers worked out by hand), boundaries included.

Reference curve: the default prior, CTR 0.110 at position 3, 0.080 at 4, 0.062 at 5, 0.031 at 8, 0.004 at 20.
"""

from __future__ import annotations

import math

import pytest

from searchos.stats.ctr_curve import Confidence, CtrCurve, default_curve
from searchos.stats.opportunities import (
    CTR_GAP_MIN_IMPRESSIONS,
    PageClicks,
    PageQueryStat,
    cannibalisation,
    ctr_gap,
    decay,
    decay_p_value,
    find_opportunities,
    impression_percentile,
    striking_distance,
)

CURVE = default_curve()


def pq(
    impressions: int, position: float, clicks: int = 0, page: str = "/p", query: str = "q"
) -> PageQueryStat:
    return PageQueryStat(page, query, impressions, clicks, position)


# ---- striking distance ------------------------------------------------------------------------------
def test_percentile_is_numpys_linear_interpolation() -> None:
    values = [pq(i, 5) for i in (10, 20, 30, 40, 50, 60, 70, 80, 1000)]
    assert impression_percentile(values) == 70.0  # index (9-1)*0.75 = 6 -> the 7th smallest
    assert impression_percentile([pq(10, 5), pq(20, 5)]) == 17.5
    assert impression_percentile([]) == 0.0


def test_striking_distance_known_answer() -> None:
    stats = [
        pq(10, 5, query="a"),
        pq(20, 5, query="b"),
        pq(30, 5, query="c"),
        pq(40, 5, query="d"),
        pq(50, 5, query="e"),
        pq(60, 6, query="f"),  # below the P75 (70)
        pq(70, 20.0, query="g"),  # exactly P75 and exactly position 20: included
        pq(80, 4.0, query="h"),  # exactly position 4: included
        pq(1000, 5.0, clicks=60, query="i"),
    ]
    found = striking_distance(stats, CURVE)
    assert [o.query for o in found] == ["i", "g", "h"]  # by score
    # score = impressions x (CTR@3 - CTR@position)
    assert [o.score for o in found] == pytest.approx(
        [1000 * (0.110 - 0.062), 70 * (0.110 - 0.004), 80 * (0.110 - 0.080)]
    )
    ev = found[0].evidence
    assert ev["impressions"] == 1000 and ev["position"] == 5.0 and ev["impressions_p75"] == 70.0
    assert ev["expected_ctr_at_position"] == pytest.approx(0.062) and ev[
        "expected_ctr_at_3"
    ] == pytest.approx(0.110)
    assert ev["ctr"] == pytest.approx(0.06)


def test_striking_distance_position_boundaries() -> None:
    stats = [
        pq(100, 3.99, query="top3"),
        pq(100, 4.0, query="in1"),
        pq(100, 20.0, query="in2"),
        pq(100, 20.01, query="out"),
    ]
    # P75 of [100,100,100,100] = 100 and the rule is ">= P75", so equal impressions qualify.
    assert sorted(o.query or "" for o in striking_distance(stats, CURVE)) == ["in1", "in2"]


def test_striking_distance_empty_and_no_gain() -> None:
    assert striking_distance([], CURVE) == []
    flat = fit_flat_curve()
    assert striking_distance([pq(500, 6)], flat) == []  # CTR@3 == CTR@6: nothing to gain, nothing reported


def fit_flat_curve() -> CtrCurve:
    sources: list[Confidence] = ["site"] * 20
    return CtrCurve((0.05,) * 20, tuple(sources), (1000,) * 20)


# ---- CTR gap ----------------------------------------------------------------------------------------
def test_ctr_gap_needs_the_upper_bound_below_the_curve_not_just_the_point_estimate() -> None:
    # Position 5 -> expected CTR 0.062.
    clear = pq(1000, 5, clicks=30, query="clear")  # 3.0%, Wilson upper ~4.25%  -> flagged
    narrow = pq(1000, 5, clicks=45, query="narrow")  # 4.5%, upper ~5.97% < 6.2% -> flagged
    noisy = pq(
        1000, 5, clicks=50, query="noisy"
    )  # 5.0% < 6.2% as a point estimate, upper ~6.5% -> NOT flagged
    fine = pq(1000, 5, clicks=70, query="fine")
    found = ctr_gap([clear, narrow, noisy, fine], CURVE)
    assert [o.query for o in found] == ["clear", "narrow"]
    assert found[0].score == pytest.approx(1000 * (0.062 - 0.03))
    ev = found[0].evidence
    assert ev["ctr"] == pytest.approx(0.03) and ev["expected_ctr"] == pytest.approx(0.062)
    assert ev["ctr_upper_95"] == pytest.approx(0.0425, abs=2e-4) and ev["curve_confidence"] == "default"


def test_ctr_gap_impression_threshold_boundary() -> None:
    assert CTR_GAP_MIN_IMPRESSIONS == 200
    just_under = pq(199, 5, clicks=0, query="under")
    exactly = pq(200, 5, clicks=0, query="exact")  # 0 clicks: Wilson upper = z^2/(n+z^2) = 0.0188 < 0.062
    found = ctr_gap([just_under, exactly], CURVE)
    assert [o.query for o in found] == ["exact"]
    assert found[0].evidence["ctr_upper_95"] == pytest.approx(3.8416 / 203.8416, abs=1e-4)


def test_ctr_gap_ignores_positions_outside_the_curve() -> None:
    assert ctr_gap([pq(5000, 25, clicks=0)], CURVE) == []
    assert ctr_gap([pq(5000, 0.3, clicks=0)], CURVE) == []


# ---- decay ------------------------------------------------------------------------------------------
def test_decay_p_value_exact() -> None:
    assert decay_p_value(2, 8) == pytest.approx(56 / 1024)  # (C(10,0)+C(10,1)+C(10,2)) / 2^10
    assert decay_p_value(0, 30) == pytest.approx(2.0**-30)
    assert decay_p_value(5, 5) == pytest.approx(sum(math.comb(10, k) for k in range(6)) / 1024)
    assert decay_p_value(0, 0) == 1.0


def test_decay_flags_significant_drops_only() -> None:
    big = PageClicks("/big", recent_clicks=60, prior_clicks=100)  # p ~ 0.001
    mild = PageClicks("/mild", recent_clicks=70, prior_clicks=100)  # p ~ 0.013 -> not significant at 0.01
    up = PageClicks("/up", recent_clicks=150, prior_clicks=100)
    same = PageClicks("/same", recent_clicks=100, prior_clicks=100)
    assert decay_p_value(70, 100) > 0.001 and decay_p_value(70, 100) > 0.01 > decay_p_value(60, 100)
    found = decay([big, mild, up, same])
    assert [o.page for o in found] == ["/big"]
    ev = found[0].evidence
    assert ev["clicks_last_28d"] == 60 and ev["clicks_previous_28d"] == 100 and ev["clicks_lost"] == 40
    assert ev["p_value"] == pytest.approx(decay_p_value(60, 100)) and ev["change_pct"] == -40.0
    assert found[0].score == 40.0 and found[0].query is None


def test_decay_minimum_prior_clicks_boundary() -> None:
    gone_small = PageClicks("/small", recent_clicks=0, prior_clicks=29)  # clearly a drop, but too few clicks
    gone_enough = PageClicks("/enough", recent_clicks=0, prior_clicks=30)  # p = 2^-30
    assert [o.page for o in decay([gone_small, gone_enough])] == ["/enough"]


# ---- cannibalisation --------------------------------------------------------------------------------
def test_cannibalisation_known_answer_and_shares() -> None:
    stats = [
        PageQueryStat("/a", "blue widgets", 60, 6, 3.0),
        PageQueryStat("/b", "blue widgets", 30, 2, 5.0),
        PageQueryStat("/c", "blue widgets", 10, 0, 9.0),  # 10% share: not a competitor
        PageQueryStat("/a", "single page query", 500, 50, 2.0),
    ]
    found = cannibalisation(stats)
    assert len(found) == 1 and found[0].query == "blue widgets" and found[0].page == "/a"
    assert found[0].score == 40.0  # 100 total - 60 on the leading page
    pages = found[0].evidence["pages"]
    assert [p["page"] for p in pages] == ["/a", "/b"]
    assert [p["share"] for p in pages] == [0.6, 0.3] and found[0].evidence["query_impressions"] == 100


def test_cannibalisation_share_boundary_is_inclusive_at_20_percent() -> None:
    at_boundary = [PageQueryStat("/a", "q1", 80, 0, 3.0), PageQueryStat("/b", "q1", 20, 0, 4.0)]  # 20/100
    below = [PageQueryStat("/a", "q2", 80, 0, 3.0), PageQueryStat("/b", "q2", 19, 0, 4.0)]  # 19/99 = 19.2%
    assert [o.query for o in cannibalisation(at_boundary + below)] == ["q1"]


def test_cannibalisation_ignores_queries_with_too_few_impressions() -> None:
    tiny = [PageQueryStat("/a", "tiny", 25, 0, 3.0), PageQueryStat("/b", "tiny", 24, 0, 4.0)]  # 49 in total
    enough = [PageQueryStat("/a", "enough", 25, 0, 3.0), PageQueryStat("/b", "enough", 25, 0, 4.0)]  # 50
    assert [o.query for o in cannibalisation(tiny + enough)] == ["enough"]


def test_three_way_split() -> None:
    stats = [PageQueryStat(f"/{i}", "x", 100, 0, 3.0 + i) for i in range(3)]
    (found,) = cannibalisation(stats)
    assert found.score == 200.0 and len(found.evidence["pages"]) == 3


# ---- all together -----------------------------------------------------------------------------------
def test_find_opportunities_skips_decay_when_history_is_missing() -> None:
    stats = [pq(1000, 5.0, clicks=10)]
    with_decay = find_opportunities(stats, [PageClicks("/p", 0, 100)], CURVE)
    without = find_opportunities(stats, None, CURVE)
    assert [o.page for o in with_decay["DECAY"]] == ["/p"] and without["DECAY"] == []
    assert set(with_decay) == {"STRIKING_DISTANCE", "CTR_GAP", "DECAY", "CANNIBALISATION"}
