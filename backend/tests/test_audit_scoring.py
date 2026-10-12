"""Scoring tests. Expected values are written as the arithmetic they come from."""

from __future__ import annotations

import pytest

from searchos.audit.diff import diff_issues
from searchos.audit.model import AuditInput, Category, Severity
from searchos.audit.runner import run_audit
from searchos.audit.scoring import CheckScoreInput, compute_scores, score_of
from tests.test_audit_checks import BASE, _home, page, site


def item(
    severity: Severity, applicable: int, failing: int, category: Category = "on_page", cid: str = "X"
) -> CheckScoreInput:
    return CheckScoreInput(cid, category, severity, applicable, failing)


def test_perfect_site_scores_100() -> None:
    assert score_of([item("high", 10, 0), item("low", 4, 0)]) == 100.0


def test_everything_failing_scores_0() -> None:
    assert score_of([item("critical", 5, 5), item("medium", 1, 1)]) == 0.0


def test_weights_are_critical_10_high_5_medium_2_low_1() -> None:
    # one check, one severity: score depends only on the failing share
    assert score_of([item("critical", 10, 1)]) == pytest.approx(100 * (1 - 0.1))
    # critical fully failing next to a clean low check: 100 * (1 - (10*1 + 1*0) / (10 + 1))
    assert score_of([item("critical", 4, 4), item("low", 4, 0)]) == pytest.approx(100 * (1 - 10 / 11))
    # high 20% failing, medium 50% failing, low 100% failing:
    # weighted = 5*0.2 + 2*0.5 + 1*1.0 = 3.0 ; total weight = 5 + 2 + 1 = 8
    assert score_of([item("high", 10, 2), item("medium", 4, 2), item("low", 3, 3)]) == pytest.approx(
        100 * (1 - 3.0 / 8)
    )


def test_info_checks_have_zero_weight() -> None:
    assert score_of([item("high", 10, 5), item("info", 1, 1)]) == pytest.approx(100 * (1 - 5 * 0.5 / 5))
    assert score_of([item("info", 1, 1)]) is None


def test_checks_that_apply_to_nothing_are_left_out() -> None:
    # the 100%-failing check applies to 0 pages, so it must not drag the score
    assert score_of([item("high", 0, 0), item("medium", 10, 1)]) == pytest.approx(100 * (1 - 2 * 0.1 / 2))
    assert score_of([item("high", 0, 0)]) is None
    assert score_of([]) is None


def test_site_level_check_counts_as_share_zero_or_one() -> None:
    assert score_of([item("critical", 1, 1), item("medium", 10, 0)]) == pytest.approx(100 * (1 - 10 / 12))
    assert score_of([item("critical", 1, 0), item("medium", 10, 0)]) == 100.0


def test_failing_is_capped_at_applicable() -> None:
    assert score_of([item("high", 2, 5)]) == 0.0


def test_subscores_use_the_same_formula_per_category() -> None:
    items = [
        item("high", 10, 1, "indexability", "A"),  # r = 0.1
        item("medium", 5, 5, "indexability", "B"),  # r = 1
        item("low", 4, 2, "on_page", "C"),  # r = 0.5
        item("info", 1, 1, "ai_readiness", "D"),
    ]
    result = compute_scores(items)
    assert result.subscores["indexability"] == pytest.approx(100 * (1 - (5 * 0.1 + 2 * 1) / (5 + 2)))
    assert result.subscores["on_page"] == pytest.approx(100 * (1 - 0.5))
    assert result.subscores["ai_readiness"] is None  # only an info check: nothing to score
    assert result.subscores["performance"] is None
    assert result.subscores["structured_data"] is None
    assert result.score == pytest.approx(100 * (1 - (5 * 0.1 + 2 * 1 + 1 * 0.5) / (5 + 2 + 1)))


def test_diff_new_fixed_persisting_and_not_rechecked() -> None:
    previous = {
        ("TITLE_MISSING", "/a"),
        ("H1_MISSING", "/b"),
        ("HTTPS_MISSING", ""),
        ("THIN_CONTENT", "/gone"),
    }
    current = {("TITLE_MISSING", "/a"), ("H1_MISSING", "/c")}
    diff = diff_issues(previous, current, crawled_now={"/a", "/b", "/c"})
    assert diff.new == [("H1_MISSING", "/c")]
    assert diff.persisting == [("TITLE_MISSING", "/a")]
    assert diff.fixed == [("H1_MISSING", "/b"), ("HTTPS_MISSING", "")]
    assert diff.not_rechecked == [("THIN_CONTENT", "/gone")]


def test_run_audit_end_to_end_on_a_tiny_site() -> None:
    pages = [
        _home(
            in_sitemap=True, structured_data={"jsonld": [{"@type": "Organization", "name": "S", "url": BASE}]}
        ),
        page("/a", in_sitemap=True, title=None),
    ]
    result = run_audit(AuditInput(pages, site(llms_txt_present=False)))
    keys = {(i.check_id, i.page_id) for i in result.issues}
    assert ("TITLE_MISSING", "id:/a") in keys
    assert ("LLMS_TXT", None) in keys
    assert result.issue_counts["total"] == len(result.issues)
    assert result.issue_counts["by_severity"]["high"] >= 1
    assert result.scores.score is not None and 0 < result.scores.score < 100
    skipped = run_audit(AuditInput(pages, site()), skip=frozenset({"JS_DEPENDENT_CONTENT"}))
    assert "JS_DEPENDENT_CONTENT" not in skipped.checks
