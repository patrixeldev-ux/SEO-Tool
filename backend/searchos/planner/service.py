"""What a person can do with an action: approve, reject, reopen, mark done, edit, restore the AI's version.

Shared by the single-action and bulk endpoints. Every function returns an error sentence (or problems for an
edit) instead of raising, so the bulk endpoint can report a result per action. None of them commits.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from searchos.models import Action, Site
from searchos.planner.catalog import OPEN_STATUSES, ActionStatus, info_for
from searchos.planner.lifecycle import restored_status, transition_error
from searchos.planner.loader import load_context
from searchos.planner.proposals import Uniqueness, validate_proposal

S = ActionStatus
MAX_REASON_CHARS = 500
EDITABLE_KINDS = ("ai", "deterministic")
EDITABLE_BRIEF_TYPES = ("refresh_content", "consolidate_pages")
REOPENABLE = (S.approved, S.rejected, S.manual_done)


def has_value(action: Action) -> bool:
    return action.proposed_value is not None


def approve(action: Action, user_id: uuid.UUID, now: datetime | None = None) -> str | None:
    error = transition_error(action.type, action.status, S.approved, has_value=has_value(action))
    if error:
        return error
    action.status = S.approved
    action.decided_by = user_id
    action.decided_at = now or datetime.now(UTC)
    action.updated_at = action.decided_at
    return None


def reject(action: Action, user_id: uuid.UUID, reason: str, now: datetime | None = None) -> str | None:
    reason = " ".join((reason or "").split())
    if not reason:
        return "Say why you are rejecting it; the reason is kept so the same suggestion is not repeated."
    if len(reason) > MAX_REASON_CHARS:
        return f"Keep the reason under {MAX_REASON_CHARS} characters."
    error = transition_error(action.type, action.status, S.rejected, has_value=has_value(action))
    if error:
        return error
    action.status = S.rejected
    action.reject_reason = reason
    action.priority_at_decision = action.priority
    action.decided_by = user_id
    action.decided_at = now or datetime.now(UTC)
    action.updated_at = action.decided_at
    return None


async def reopen(session: AsyncSession, action: Action) -> str | None:
    """Rejected / approved / done -> back on the board. Refused if another open action already holds the slot."""
    if action.status not in REOPENABLE:
        return f"A {action.status.value.replace('_', ' ')} action cannot be reopened."
    target = restored_status(action.type, has_value=has_value(action))
    clash = await session.scalar(
        sa.select(sa.func.count())
        .select_from(Action)
        .where(
            Action.site_id == action.site_id,
            Action.type == action.type,
            sa.func.coalesce(Action.target_url, "") == (action.target_url or ""),
            Action.field == action.field,
            Action.status.in_(OPEN_STATUSES),
            Action.id != action.id,
        )
    )
    if clash:
        return "The planner has already proposed this again; use that one."
    action.status = target
    action.reject_reason = None
    action.decided_by = None
    action.decided_at = None
    action.priority_at_decision = None
    action.updated_at = datetime.now(UTC)
    return None


def complete(action: Action, user_id: uuid.UUID, now: datetime | None = None) -> str | None:
    error = transition_error(action.type, action.status, S.manual_done, has_value=has_value(action))
    if error:
        return error
    action.status = S.manual_done
    action.decided_by = user_id
    action.decided_at = now or datetime.now(UTC)
    action.updated_at = action.decided_at
    return None


# ---- editing --------------------------------------------------------------------------------------------
@dataclass(slots=True)
class EditResult:
    ok: bool
    problems: list[str] = field(default_factory=list)


def is_editable(action: Action) -> bool:
    info = info_for(action.type)
    return info.value_kind in EDITABLE_KINDS or action.type in EDITABLE_BRIEF_TYPES


async def edit_value(
    session: AsyncSession, site: Site, action: Action, value: Any, user_id: uuid.UUID
) -> EditResult:
    """Replace the proposed value with a person's own, after the same validators the AI's answers pass."""
    if action.status not in (S.proposed, S.needs_ai, S.no_suggestion, S.approved):
        return EditResult(False, [f"A {action.status.value.replace('_', ' ')} action cannot be edited."])
    if not is_editable(action):
        return EditResult(False, ["This task has no value to edit; tick off its steps instead."])
    ctx = await load_context(session, site)
    uniq = Uniqueness.from_pages(ctx.pages.values())
    others = await session.scalars(
        sa.select(Action).where(
            Action.site_id == action.site_id,
            Action.id != action.id,
            Action.type.in_(("rewrite_title", "rewrite_meta")),
            Action.status.in_((S.proposed, S.approved)),
            Action.proposed_value.is_not(None),
        )
    )
    for other in others:
        if isinstance(other.proposed_value, str):
            current = other.current_value if isinstance(other.current_value, str) else None
            if other.type == "rewrite_title":
                uniq.swap_title(current, other.proposed_value)
            else:
                uniq.swap_description(current, other.proposed_value)
    cleaned, problems = validate_proposal(
        ctx,
        action_type=action.type,
        target_url=action.target_url,
        field_name=action.field,
        value=value,
        current_value=action.current_value,
        evidence=action.evidence,
        uniqueness=uniq,
    )
    if problems:
        return EditResult(False, problems)
    if not action.edited_by_user and action.original_value is None and action.proposed_value is not None:
        action.original_value = action.proposed_value
    action.proposed_value = cleaned
    action.edited_by_user = True
    action.generation = {**(action.generation or {}), "state": "edited"}
    if action.status in (S.needs_ai, S.no_suggestion, S.approved):
        action.status = S.proposed  # a changed value needs a fresh approval
    action.decided_by = None
    action.decided_at = None
    action.updated_at = datetime.now(UTC)
    return EditResult(True)


def restore_original(action: Action) -> str | None:
    if not action.edited_by_user:
        return "This action has not been edited."
    if action.status not in (S.proposed, S.approved):
        return f"A {action.status.value.replace('_', ' ')} action cannot be changed."
    action.proposed_value = action.original_value
    action.edited_by_user = False
    if action.proposed_value is None:
        action.status = S.needs_ai
    elif action.status is S.approved:
        action.status = S.proposed
    action.generation = {**(action.generation or {}), "state": "ok" if action.proposed_value else "restored"}
    action.updated_at = datetime.now(UTC)
    return None


def set_checklist(action: Action, done: list[str]) -> str | None:
    """Tick items of a manual task. Unknown ids are refused so a typo cannot silently lose a tick."""
    value = action.proposed_value
    if not isinstance(value, dict) or value.get("kind") != "checklist":
        return "This task has no checklist."
    known = {str(i.get("id")) for i in value.get("items", []) if isinstance(i, dict)}
    unknown = [d for d in done if d not in known]
    if unknown:
        return f'Unknown checklist item: "{unknown[0]}".'
    if action.status not in (S.proposed, S.manual_done):
        return f"A {action.status.value.replace('_', ' ')} task cannot be changed."
    action.checklist_done = list(dict.fromkeys(done))
    action.updated_at = datetime.now(UTC)
    return None
