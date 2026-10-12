"""Audit score: score = 100 * (1 - sum(w_i * r_i) / sum(w_i)) over the applicable checks.

* w_i is the weight of the check's severity: critical 10, high 5, medium 2, low 1, info 0.
* r_i is the share of applicable pages that fail check i (a site-level check has one unit, so r_i is 0 or 1).
* A check that applies to nothing (for example the article checks on a site without articles) is left out.
* The same formula over one category's checks gives its subscore.
* When no weighted check applies there is nothing to score: the result is None, never a made-up 100.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from searchos.audit.catalog import SEVERITY_WEIGHTS
from searchos.audit.model import CATEGORIES, Category, Severity


@dataclass(frozen=True, slots=True)
class CheckScoreInput:
    check_id: str
    category: Category
    severity: Severity
    applicable: int  # units the check was evaluated on
    failing: int  # units that failed (0 <= failing <= applicable)


@dataclass(frozen=True, slots=True)
class ScoreResult:
    score: float | None
    subscores: dict[Category, float | None]


def failing_share(item: CheckScoreInput) -> float:
    return min(item.failing, item.applicable) / item.applicable


def score_of(items: Iterable[CheckScoreInput]) -> float | None:
    weighted = 0.0
    total_weight = 0
    for item in items:
        weight = SEVERITY_WEIGHTS[item.severity]
        if item.applicable <= 0 or weight == 0:
            continue
        total_weight += weight
        weighted += weight * failing_share(item)
    if total_weight == 0:
        return None
    return 100.0 * (1.0 - weighted / total_weight)


def compute_scores(items: Iterable[CheckScoreInput]) -> ScoreResult:
    all_items = list(items)
    return ScoreResult(
        score=score_of(all_items),
        subscores={cat: score_of(i for i in all_items if i.category == cat) for cat in CATEGORIES},
    )
