"""Run every registered check and score the result."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from searchos.audit.catalog import CheckInfo, load_catalog
from searchos.audit.model import AuditInput, Category, Severity
from searchos.audit.registry import CheckOutcome, registered_checks
from searchos.audit.scoring import CheckScoreInput, ScoreResult, compute_scores


@dataclass(frozen=True, slots=True)
class IssueRecord:
    check_id: str
    severity: Severity
    category: Category
    page_id: str | None
    details: dict[str, Any]


@dataclass(slots=True)
class AuditResult:
    issues: list[IssueRecord]
    checks: dict[str, CheckOutcome]  # check id -> outcome (including checks that passed)
    scores: ScoreResult
    issue_counts: dict[str, Any] = field(default_factory=dict)


def run_audit(audit: AuditInput, *, skip: frozenset[str] = frozenset()) -> AuditResult:
    """`skip` lists check ids that must not run (for example JS_DEPENDENT_CONTENT without a browser)."""
    catalog = load_catalog()
    registry = registered_checks()
    unknown = set(registry) - set(catalog)
    if unknown:
        raise RuntimeError(f"checks without a catalog entry: {sorted(unknown)}")

    issues: list[IssueRecord] = []
    outcomes: dict[str, CheckOutcome] = {}
    score_inputs: list[CheckScoreInput] = []
    for check_id in sorted(registry):
        if check_id in skip:
            continue
        info: CheckInfo = catalog[check_id]
        outcome = registry[check_id].run(audit)
        outcomes[check_id] = outcome
        for failure in outcome.failures:
            issues.append(
                IssueRecord(check_id, info.severity, info.category, failure.page_id, failure.details)
            )
        score_inputs.append(
            CheckScoreInput(check_id, info.category, info.severity, outcome.applicable, len(outcome.failures))
        )
    return AuditResult(
        issues=issues,
        checks=outcomes,
        scores=compute_scores(score_inputs),
        issue_counts=summarise(issues),
    )


def summarise(issues: list[IssueRecord]) -> dict[str, Any]:
    by_severity: dict[str, int] = {}
    by_category: dict[str, int] = {}
    by_check: dict[str, int] = {}
    for issue in issues:
        by_severity[issue.severity] = by_severity.get(issue.severity, 0) + 1
        by_category[issue.category] = by_category.get(issue.category, 0) + 1
        by_check[issue.check_id] = by_check.get(issue.check_id, 0) + 1
    return {
        "total": len(issues),
        "by_severity": by_severity,
        "by_category": by_category,
        "by_check": by_check,
    }
