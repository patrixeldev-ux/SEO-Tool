"""The planner: findings -> candidates -> priced actions -> reconciled with the actions already stored.

`plan_site` is what the Celery task `plan_site` runs. The steps:

1. Load the latest finished audit, the current opportunities, Search Console numbers and the site's CTR curve.
2. Group findings by action type through the mapping table; run one builder per type (`planner/builders.py`).
3. Price every candidate (`planner/gain.py`).
4. Reconcile with the stored actions: update the open one with the same key, otherwise create it, unless a person
   rejected it less than 90 days ago and nothing material changed; close open actions whose finding is gone.
5. Ask the AI for the values that still need one (`planner/generate.py`).
"""

from __future__ import annotations

import logging
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from searchos.audit.catalog import load_catalog
from searchos.crawl.urls import match_key
from searchos.llm import LLMProvider, get_provider, provider_status
from searchos.models import Action, Site
from searchos.planner import gain
from searchos.planner.builders import BUILDERS
from searchos.planner.catalog import (
    AUTO_CLOSABLE,
    OPEN_STATUSES,
    ActionStatus,
    info_for,
)
from searchos.planner.generate import generate_values
from searchos.planner.loader import load_context
from searchos.planner.mapping import MAPPINGS
from searchos.planner.model import Candidate, Finding, PlanContext

log = logging.getLogger(__name__)

REJECTION_QUIET_DAYS = 90
MATERIAL_INCREASE = 1.5  # a rejected finding returns only when its priority is at least 1.5x what it was
MAX_CANDIDATES_PER_TYPE = 1000


# ---- candidates ------------------------------------------------------------------------------------
def findings_by_action(findings: list[Finding]) -> dict[str, list[Finding]]:
    grouped: dict[str, list[Finding]] = defaultdict(list)
    for f in findings:
        mapping = MAPPINGS.get(f.source)
        if mapping is None:
            continue
        for action_type in mapping.actions:
            grouped[action_type].append(f)
    return grouped


def build_candidates(ctx: PlanContext) -> list[Candidate]:
    """Every action the current findings call for (pure: no database, no AI)."""
    found: dict[tuple[str, str, str], Candidate] = {}
    for action_type, group in findings_by_action(ctx.findings).items():
        builder = BUILDERS.get(action_type)
        if builder is None:
            ctx.skipped.append({"type": action_type, "reason": "no builder for this action type yet"})
            continue
        built = builder(ctx, group)
        built.sort(key=lambda c: -(c.gain_clicks or 0.0))
        if len(built) > MAX_CANDIDATES_PER_TYPE:
            ctx.skipped.append(
                {
                    "type": action_type,
                    "reason": f"only the first {MAX_CANDIDATES_PER_TYPE} of {len(built)} were kept",
                }
            )
            built = built[:MAX_CANDIDATES_PER_TYPE]
        for candidate in built:
            found[candidate.key] = candidate
    return list(found.values())


@dataclass(slots=True)
class Priced:
    candidate: Candidate
    expected_gain: float
    confidence: float
    effort: int
    priority: float
    evidence: dict[str, Any] = field(default_factory=dict)


def price(ctx: PlanContext, candidate: Candidate) -> Priced:
    """Expected gain (extra clicks / 28 days), confidence and priority; says in `evidence` where the number comes from."""
    info = info_for(candidate.type)
    evidence = dict(candidate.evidence)
    clicks = 0
    if candidate.target_url:
        entry = ctx.gsc.get(match_key(candidate.target_url))
        clicks = entry.clicks if entry else 0
    if candidate.gain_clicks is not None:
        expected = float(candidate.gain_clicks)
        curve_conf = None
        if ctx.curve is not None:
            curve_conf = ctx.curve.summary
        conf = gain.confidence(candidate.impressions, curve_conf)
        evidence["gain_basis"] = "gsc"
        evidence["gain_note"] = (
            "Estimated from this page's Search Console numbers and the site's own CTR curve: "
            "impressions x (expected CTR - actual CTR)."
        )
    else:
        severity = None
        if candidate.type == "dev_fix":
            check = candidate.sources[0] if candidate.sources else ""
            known = load_catalog().get(check)
            severity = known.severity if known else None
        expected = gain.prior_gain(candidate.type, clicks, severity)
        conf = gain.PRIOR_CONFIDENCE
        evidence["gain_basis"] = "prior"
        evidence["gain_note"] = (
            "A prior, not measured: a small fixed guess until experiments show what this kind of change is worth."
        )
    return Priced(
        candidate,
        round(expected, 2),
        conf,
        info.effort,
        gain.priority(expected, info.effort),
        evidence,
    )


# ---- reconcile ---------------------------------------------------------------------------------------
@dataclass(slots=True)
class PlanStats:
    created: int = 0
    updated: int = 0
    obsolete: int = 0
    skipped_rejected: int = 0
    created_ids: list[uuid.UUID] = field(default_factory=list)


def _initial_status(candidate: Candidate) -> ActionStatus:
    if candidate.proposed_value is None and info_for(candidate.type).value_kind == "ai":
        return ActionStatus.needs_ai
    return ActionStatus.proposed


def reconcile(
    session: AsyncSession,
    ctx: PlanContext,
    priced: list[Priced],
    existing: list[Action],
    *,
    org_id: uuid.UUID,
    site_id: uuid.UUID,
    now: datetime,
) -> PlanStats:
    stats = PlanStats()
    open_by_key: dict[tuple[str, str, str], Action] = {}
    rejected_by_key: dict[tuple[str, str, str], Action] = {}
    for old in existing:
        key = (old.type, old.target_url or "", old.field)
        if old.status in OPEN_STATUSES:
            open_by_key[key] = old
        elif (
            old.status is ActionStatus.rejected
            and old.decided_at is not None
            and now - old.decided_at <= timedelta(days=REJECTION_QUIET_DAYS)
        ):
            previous = rejected_by_key.get(key)
            if previous is None or (previous.decided_at and old.decided_at > previous.decided_at):
                rejected_by_key[key] = old

    seen: set[tuple[str, str, str]] = set()
    for item in priced:
        c = item.candidate
        seen.add(c.key)
        info = info_for(c.type)
        row = open_by_key.get(c.key)
        if row is not None:
            row.evidence = item.evidence
            row.current_value = c.current_value
            row.sources = c.sources
            row.source_kind = c.source_kind
            row.risk_class = info.risk_class
            row.effort = item.effort
            row.expected_gain = item.expected_gain
            row.confidence = item.confidence
            row.priority = item.priority
            row.updated_at = now
            if (
                c.proposed_value is not None
                and not row.edited_by_user
                and row.status
                in (
                    ActionStatus.proposed,
                    ActionStatus.needs_ai,
                    ActionStatus.no_suggestion,
                )
            ):
                row.proposed_value = c.proposed_value
                row.status = ActionStatus.proposed
            stats.updated += 1
            continue
        rejected = rejected_by_key.get(c.key)
        if rejected is not None:
            before = rejected.priority_at_decision or 0.0
            if not (before > 0 and item.priority >= MATERIAL_INCREASE * before):
                stats.skipped_rejected += 1
                continue
        new = Action(
            id=uuid.uuid4(),
            org_id=org_id,
            site_id=site_id,
            type=c.type,
            risk_class=info.risk_class,
            target_url=c.target_url,
            field=c.field,
            current_value=c.current_value,
            proposed_value=c.proposed_value,
            original_value=c.proposed_value,
            evidence=item.evidence,
            expected_gain=item.expected_gain,
            confidence=item.confidence,
            effort=item.effort,
            priority=item.priority,
            status=_initial_status(c),
            created_by="system",
            source_kind=c.source_kind,
            sources=c.sources,
            generation={},
            updated_at=now,
        )
        session.add(new)
        stats.created += 1
        stats.created_ids.append(new.id)

    available = {"site"}
    if ctx.has_audit:
        available.add("audit")
    if ctx.has_gsc:
        available.add("gsc")
    for key, row in open_by_key.items():
        if (
            key not in seen
            and row.status in AUTO_CLOSABLE
            and row.created_by == "system"
            and row.source_kind in available
        ):
            row.status = ActionStatus.obsolete
            row.updated_at = now
            stats.obsolete += 1
    return stats


async def generate_open_values(
    sessions: async_sessionmaker[AsyncSession],
    session: AsyncSession,
    provider: LLMProvider | None,
    ctx: PlanContext,
    site: Site,
    now: datetime,
) -> dict[str, Any]:
    """Ask the AI for the values that open actions still lack (or record why that is not possible)."""
    open_rows = list(
        (
            await session.scalars(
                sa.select(Action).where(Action.site_id == site.id, Action.status.in_(OPEN_STATUSES))
            )
        ).all()
    )
    status = provider_status()
    summary = await generate_values(
        sessions,
        session,
        provider,
        ctx,
        open_rows,
        org_id=site.org_id,
        site_id=site.id,
        no_key_reason=status.problem or NO_KEY_REASON,
        now=now,
    )
    await session.commit()
    return summary


# ---- entry point -----------------------------------------------------------------------------------
class _Unset:
    pass


UNSET = _Unset()
NO_KEY_REASON = (
    "No AI key is set. Add ANTHROPIC_API_KEY (or GEMINI_API_KEY and GEMINI_MODEL) to .env, restart, and re-plan. "
    "See Settings > AI."
)


async def plan_site(
    sessions: async_sessionmaker[AsyncSession],
    site_id: uuid.UUID,
    *,
    trigger: str = "manual",
    now: datetime | None = None,
    provider: LLMProvider | _Unset | None = UNSET,
) -> dict[str, Any]:
    """Re-plan one site. Returns the summary stored as the job's result.

    `provider` defaults to the one configured in the environment; pass None to plan without AI (tests)."""
    now = now or datetime.now(UTC)
    ai: LLMProvider | None = get_provider() if isinstance(provider, _Unset) else provider
    async with sessions() as session:
        site = await session.get(Site, site_id)
        if site is None:
            return {"skipped": "site no longer exists"}
        ctx = await load_context(session, site)
        if not ctx.has_audit and not ctx.has_gsc:
            return {
                "trigger": trigger,
                "skipped": "Nothing to plan from yet: run an audit or connect Search Console first.",
            }
        candidates = build_candidates(ctx)
        priced = [price(ctx, c) for c in candidates]
        existing = (
            await session.scalars(
                sa.select(Action).where(
                    Action.site_id == site_id,
                    sa.or_(
                        Action.status.in_(OPEN_STATUSES),
                        sa.and_(
                            Action.status == ActionStatus.rejected,
                            Action.decided_at > now - timedelta(days=REJECTION_QUIET_DAYS),
                        ),
                    ),
                )
            )
        ).all()
        stats = reconcile(session, ctx, priced, list(existing), org_id=site.org_id, site_id=site_id, now=now)
        await session.commit()

        ai_summary = await generate_open_values(sessions, session, ai, ctx, site, now)

        counts = {
            status.value: n
            for status, n in (
                await session.execute(
                    sa.select(Action.status, sa.func.count())
                    .where(Action.site_id == site_id, Action.status != ActionStatus.obsolete)
                    .group_by(Action.status)
                )
            ).all()
        }
    return {
        "trigger": trigger,
        "crawl_id": ctx.crawl_id,
        "findings": len(ctx.findings),
        "candidates": len(candidates),
        "created": stats.created,
        "updated": stats.updated,
        "obsolete": stats.obsolete,
        "skipped_rejected": stats.skipped_rejected,
        "by_status": counts,
        "ai": ai_summary,
        "skipped": ctx.skipped[:50],
    }
