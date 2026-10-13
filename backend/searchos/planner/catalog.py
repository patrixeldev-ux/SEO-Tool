"""Everything fixed about action types: label, risk class, effort, and what produces their value.

This is the single source of truth for risk classes (plan Decisions: `auto` may deploy without approval when the
site's autopilot is ON, `approve` always needs a person, `manual` cannot be automated at all) and for the list of
things that are NEVER automated. Phase 4's deployers and the autopilot scheduler must read `risk_class` from here
(through the `actions.risk_class` column), never decide it themselves.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Literal


class RiskClass(enum.StrEnum):
    auto = "auto"
    approve = "approve"
    manual = "manual"


class ActionStatus(enum.StrEnum):
    proposed = "proposed"
    needs_ai = "needs_ai"  # a value is wanted but none exists yet (no AI key, budget used up, per-run limit)
    no_suggestion = "no_suggestion"  # the AI's answers failed validation 3 times; edit by hand or re-plan
    approved = "approved"
    deployed = "deployed"  # Phase 4
    rejected = "rejected"
    rolled_back = "rolled_back"  # Phase 4
    manual_done = "manual_done"
    stale = "stale"  # Phase 4: the live page changed since the audit, so the action was not deployed
    obsolete = "obsolete"  # the issue is gone from the latest audit / opportunities


# Statuses that occupy the (site, type, target, field) slot: re-planning updates these instead of adding rows.
OPEN_STATUSES: tuple[ActionStatus, ...] = (
    ActionStatus.proposed,
    ActionStatus.needs_ai,
    ActionStatus.no_suggestion,
    ActionStatus.approved,
    ActionStatus.deployed,
)
# Statuses the planner may close on its own when the underlying finding disappears.
AUTO_CLOSABLE: tuple[ActionStatus, ...] = (
    ActionStatus.proposed,
    ActionStatus.needs_ai,
    ActionStatus.no_suggestion,
    ActionStatus.approved,
)

ValueKind = Literal["ai", "deterministic", "brief", "checklist"]


@dataclass(frozen=True, slots=True)
class ActionTypeInfo:
    type: str
    label: str
    risk_class: RiskClass
    effort: int  # 1 (minutes) .. 5 (days)
    value_kind: ValueKind
    description: str


def _t(
    type_: str, label: str, risk: RiskClass, effort: int, kind: ValueKind, description: str
) -> tuple[str, ActionTypeInfo]:
    return type_, ActionTypeInfo(type_, label, risk, effort, kind, description)


A, P, M = RiskClass.auto, RiskClass.approve, RiskClass.manual

ACTION_TYPES: dict[str, ActionTypeInfo] = dict(
    [
        _t("rewrite_title", "Rewrite page title", A, 1, "ai", "Replace the <title> with a better one."),
        _t("rewrite_meta", "Rewrite meta description", A, 1, "ai", "Replace the meta description."),
        _t("add_alt", "Add image alt text", A, 1, "ai", "Describe one image for people and AI crawlers."),
        _t("add_jsonld", "Add structured data", A, 2, "ai", "Add a JSON-LD block that is missing."),
        _t(
            "fix_jsonld",
            "Fix structured data",
            A,
            2,
            "ai",
            "Repair a JSON-LD block that is broken or incomplete.",
        ),
        _t(
            "add_internal_links",
            "Add internal links",
            A,
            2,
            "deterministic",
            "Link related pages to each other.",
        ),
        _t("fix_canonical", "Fix canonical tag", P, 2, "deterministic", "Set the canonical URL."),
        _t(
            "collapse_redirect",
            "Collapse redirect chain",
            P,
            2,
            "deterministic",
            "Point the first URL straight at the final one.",
        ),
        _t(
            "fix_link",
            "Fix broken internal link",
            P,
            1,
            "deterministic",
            "Re-point or remove a link that leads to an error.",
        ),
        _t(
            "refresh_content",
            "Refresh page content",
            P,
            4,
            "brief",
            "Write or update the content (a brief, nothing is written for you).",
        ),
        _t(
            "consolidate_pages",
            "Consolidate competing pages",
            P,
            5,
            "brief",
            "Merge or differentiate pages that compete for one query.",
        ),
        _t(
            "ssr_task",
            "Server-render the content",
            M,
            5,
            "brief",
            "Developer task: make the content part of the HTML sent to crawlers.",
        ),
        _t(
            "robots_review",
            "Review robots.txt",
            M,
            1,
            "brief",
            "Decide, by hand, what the site's robots.txt should allow.",
        ),
        _t(
            "noindex_review",
            "Review a noindex page",
            M,
            1,
            "brief",
            "Decide whether a noindex page should be indexed.",
        ),
        _t("dev_fix", "Technical fix", M, 3, "brief", "A technical fix that needs a developer."),
        _t(
            "author_eeat",
            "Show who is behind the content",
            M,
            3,
            "checklist",
            "Authors, credentials and dates (E-E-A-T).",
        ),
        _t(
            "earn_links",
            "Earn links and mentions",
            M,
            5,
            "checklist",
            "Earn editorial links; never buy or swap them.",
        ),
        _t(
            "collect_reviews", "Collect genuine reviews", M, 3, "checklist", "Ask real customers for reviews."
        ),
        _t("digital_pr", "Digital PR", M, 5, "checklist", "Publish something worth citing and pitch it."),
    ]
)

# Things that must never be done automatically, whatever the autopilot setting. `robots_review` and
# `noindex_review` only ever carry instructions for a person. The last two names are not action types at all: they
# exist so that nobody can add such a type later without a test failing.
NEVER_AUTOMATED: frozenset[str] = frozenset(
    {
        "robots_review",
        "noindex_review",
        "robots_disallow_change",
        "noindex_change",
        "delete_page",
        "link_exchange",
    }
)


def info_for(action_type: str) -> ActionTypeInfo:
    try:
        return ACTION_TYPES[action_type]
    except KeyError:
        raise ValueError(f"unknown action type {action_type!r}") from None


def risk_class_for(action_type: str) -> RiskClass:
    return info_for(action_type).risk_class


def effort_for(action_type: str) -> int:
    return info_for(action_type).effort


def may_auto_deploy(action_type: str) -> bool:
    """True only for the `auto` class, and never for anything on the never-automated list."""
    return action_type not in NEVER_AUTOMATED and risk_class_for(action_type) is RiskClass.auto
