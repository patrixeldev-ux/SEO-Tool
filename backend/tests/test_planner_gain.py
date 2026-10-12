"""3.4: expected gain, confidence and priority. Hand-computed values; no database."""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from searchos.audit.catalog import load_catalog
from searchos.crawl.urls import match_key
from searchos.planner import gain
from searchos.planner.catalog import ACTION_TYPES, effort_for
from searchos.planner.engine import price
from searchos.planner.model import Candidate
from searchos.stats.ctr_curve import CtrCurve, default_curve
from tests.planner_helpers import context, gsc_page, page

CURVE = default_curve()  # position 1 .285, 2 .157, 3 .110, 4 .080, 6 .048, 8 .031


# ---- gain ----------------------------------------------------------------------------------------------
def test_ctr_gain_is_impressions_times_the_missing_ctr() -> None:
    assert gain.ctr_gain(1000, 0.02, 0.11) == pytest.approx(90.0)
    assert gain.ctr_gain(250, 0.0, 0.08) == pytest.approx(20.0)


def test_ctr_gain_is_never_negative_and_zero_without_impressions() -> None:
    assert gain.ctr_gain(1000, 0.2, 0.11) == 0.0
    assert gain.ctr_gain(0, 0.0, 0.5) == 0.0
    assert gain.ctr_gain(-5, 0.0, 0.5) == 0.0


def test_striking_gain_is_two_positions_up_on_the_sites_curve() -> None:
    # position 8 -> 6: 2000 * (0.048 - 0.031)
    assert gain.striking_gain(2000, 8.0, 0.01, CURVE) == pytest.approx(34.0)
    # position 2 -> 1 (cannot go above 1): 2000 * (0.285 - 0.157)
    assert gain.striking_gain(2000, 2.0, 0.0, CURVE) == pytest.approx(256.0)


def test_striking_gain_does_not_credit_a_page_already_beating_the_curve() -> None:
    # actual CTR 0.05 is above the curve at 6 (0.048): nothing to gain
    assert gain.striking_gain(2000, 8.0, 0.05, CURVE) == 0.0
    # actual CTR 0.04 beats the curve at 8 (0.031) and is used as the baseline: 2000 * (0.048 - 0.04)
    assert gain.striking_gain(2000, 8.0, 0.04, CURVE) == pytest.approx(16.0)


def test_confidence_grows_with_data_and_shrinks_with_a_weaker_curve() -> None:
    assert gain.confidence(500, "site") == 0.5
    assert gain.confidence(500, "pooled") == 0.35
    assert gain.confidence(500, "default") == 0.25
    assert gain.confidence(500, None) == 0.25
    assert gain.confidence(0, "site") == 0.0
    assert gain.confidence(1500, "site") == 0.75
    assert gain.confidence(10**9, "site") == 0.95  # never certain
    assert gain.confidence(100, "site") < gain.confidence(1000, "site") < gain.confidence(10_000, "site")


def test_priority_is_gain_times_win_rate_over_effort() -> None:
    assert gain.PRIOR_WIN_RATE == 0.5
    assert gain.priority(10.0, 2) == 2.5
    assert gain.priority(10.0, 5) == 1.0
    assert gain.priority(10.0, 1, win_rate=0.8) == 8.0
    assert gain.priority(0.0, 1) == 0.0
    assert gain.priority(math.inf, 1) == 0.0
    with pytest.raises(ValueError, match="effort"):
        gain.priority(1.0, 0)


def test_lower_effort_beats_higher_effort_for_the_same_gain() -> None:
    assert gain.priority(20.0, effort_for("rewrite_title")) > gain.priority(
        20.0, effort_for("refresh_content")
    )


# ---- priors --------------------------------------------------------------------------------------------
def test_prior_is_the_larger_of_the_flat_value_and_a_share_of_the_pages_clicks() -> None:
    assert gain.prior_gain("rewrite_title", 0) == 3.0
    assert gain.prior_gain("rewrite_title", 20) == 3.0  # 5% of 20 = 1.0 < 3.0
    assert gain.prior_gain("rewrite_title", 200) == 10.0  # 5% of 200
    assert gain.prior_gain("refresh_content", 100) == 10.0
    assert gain.prior_gain("add_alt", 0) == 0.5
    assert gain.prior_gain("rewrite_title", -10) == 3.0


def test_dev_fix_prior_follows_the_checks_severity() -> None:
    assert [gain.flat_prior("dev_fix", s) for s in ("critical", "high", "medium", "low", "info")] == [
        4.0,
        2.5,
        1.0,
        0.5,
        0.0,
    ]
    assert gain.flat_prior("dev_fix") == 1.0
    assert gain.flat_prior("dev_fix", "unheard-of") == 1.0
    assert gain.flat_prior("something_new") == 1.0


def test_every_action_type_has_a_documented_prior() -> None:
    assert set(gain.FLAT_PRIOR) | {"dev_fix"} == set(ACTION_TYPES)
    assert all(v > 0 for v in gain.FLAT_PRIOR.values())
    assert set(gain.RELATIVE_UPLIFT) <= set(ACTION_TYPES)
    assert all(0 < v <= 0.2 for v in gain.RELATIVE_UPLIFT.values())  # small guesses, not promises


def test_ctr_gap_shares_do_not_count_the_gap_twice() -> None:
    assert sum(gain.CTR_GAP_SHARE.values()) == pytest.approx(1.0)
    assert set(gain.CTR_GAP_SHARE) == {"rewrite_title", "rewrite_meta"}


def test_dev_fix_priors_cover_every_severity_in_the_catalogue() -> None:
    severities = {c.severity for c in load_catalog().values()}
    assert severities <= set(gain.DEV_FIX_PRIOR_BY_SEVERITY)


# ---- pricing a candidate -------------------------------------------------------------------------------
def candidate(action_type: str, url: str | None, **kw: object) -> Candidate:
    return Candidate(type=action_type, target_url=url, field="f", source_kind="audit", sources=["X"], **kw)  # type: ignore[arg-type]


def test_a_search_console_based_gain_is_measured_and_labelled() -> None:
    p = page("/a")
    ctx = context([p], [], curve=CURVE)
    priced = price(ctx, candidate("rewrite_title", p.url, gain_clicks=36.0, impressions=1500))
    assert priced.expected_gain == 36.0
    assert priced.confidence == 0.375  # 1500 / (1500 + 500) = 0.75, times 0.5 for a default curve
    assert priced.evidence["gain_basis"] == "gsc" and "CTR curve" in priced.evidence["gain_note"]
    assert priced.effort == effort_for("rewrite_title")
    assert priced.priority == gain.priority(36.0, priced.effort)


def test_confidence_uses_the_curves_own_summary() -> None:
    p = page("/a")
    site_curve = CtrCurve(CURVE.values, ("site",) * 20, (1000,) * 20)
    ctx = context([p], [], curve=site_curve)
    priced = price(ctx, candidate("rewrite_meta", p.url, gain_clicks=10.0, impressions=1500))
    assert priced.confidence == 0.75


def test_without_a_curve_a_measured_gain_has_default_confidence() -> None:
    p = page("/a")
    ctx = context([p], [], curve=None)
    priced = price(ctx, candidate("rewrite_title", p.url, gain_clicks=5.0, impressions=500))
    assert priced.confidence == gain.confidence(500, None)


def test_a_fix_without_data_uses_a_labelled_prior() -> None:
    p = page("/a")
    ctx = context([p], [])
    priced = price(ctx, candidate("fix_canonical", p.url))
    assert priced.expected_gain == 4.0 and priced.confidence == gain.PRIOR_CONFIDENCE
    assert priced.evidence["gain_basis"] == "prior"
    assert "prior, not measured" in priced.evidence["gain_note"].lower()
    assert priced.priority == gain.priority(4.0, effort_for("fix_canonical"))


def test_a_page_that_gets_clicks_has_a_larger_prior() -> None:
    p = page("/a")
    ctx = context([p], [], gsc={match_key(p.url): gsc_page(5000, 400)})
    priced = price(ctx, candidate("fix_canonical", p.url))
    assert priced.expected_gain == 20.0  # 5% of 400 clicks beats the flat 4.0


def test_dev_fix_is_priced_by_the_severity_of_its_check() -> None:
    ctx = context([], [])
    by_check = {c.id: c.severity for c in load_catalog().values()}
    check = next(i for i, s in by_check.items() if s == "critical")
    low = next(i for i, s in by_check.items() if s == "low")
    hi = price(ctx, Candidate("dev_fix", None, "check", "audit", [check]))
    lo = price(ctx, Candidate("dev_fix", None, "check", "audit", [low]))
    assert hi.expected_gain == 4.0 and lo.expected_gain == 0.5 and hi.priority > lo.priority


def test_a_cheap_measured_title_fix_outranks_an_expensive_prior_only_refresh() -> None:
    p = page("/a")
    ctx = context([p], [], curve=CURVE)
    title = price(ctx, candidate("rewrite_title", p.url, gain_clicks=30.0, impressions=3000))
    refresh = price(ctx, candidate("refresh_content", p.url))
    assert title.priority > refresh.priority


# ---- the documentation says what the code does ---------------------------------------------------------
def _doc_table(heading: str) -> dict[str, list[str]]:
    text = (Path(__file__).resolve().parents[2] / "docs" / "METHODOLOGY.md").read_text()
    rows: dict[str, list[str]] = {}
    for line in text.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if line.startswith("|") and cells[0] in ACTION_TYPES:
            rows.setdefault(cells[0], []).append(" ".join(cells[1:]))
    assert rows, heading
    return rows


def test_methodology_lists_every_prior_effort_and_risk_class_as_coded() -> None:
    rows = _doc_table("Action priority")
    assert set(rows) == set(ACTION_TYPES)
    for action_type, found in rows.items():
        info = ACTION_TYPES[action_type]
        assert f"{info.risk_class.value} {info.effort}" == found[-1], action_type
        if action_type != "dev_fix":
            assert len(found) == 2, action_type
            flat, _, share = found[0].partition(" ")
            assert float(flat) == gain.FLAT_PRIOR[action_type], action_type
            uplift = gain.RELATIVE_UPLIFT.get(action_type)
            expected = "-" if uplift is None else f"{uplift * 100:g}%"
            assert share == expected, action_type
