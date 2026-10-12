"""Which status changes a person may make, and what each needs. The planner and the API both use this."""

from __future__ import annotations

from searchos.planner.catalog import ActionStatus, RiskClass, info_for

S = ActionStatus

# status -> statuses a person may move it to.
USER_TRANSITIONS: dict[ActionStatus, frozenset[ActionStatus]] = {
    S.proposed: frozenset({S.approved, S.rejected, S.manual_done}),
    S.needs_ai: frozenset({S.rejected}),
    S.no_suggestion: frozenset({S.rejected}),
    S.approved: frozenset({S.proposed, S.rejected}),
    S.rejected: frozenset({S.proposed}),
    S.manual_done: frozenset({S.proposed}),
}


def transition_error(
    action_type: str, current: ActionStatus, target: ActionStatus, *, has_value: bool
) -> str | None:
    """None when the change is allowed, otherwise a sentence for the person."""
    info = info_for(action_type)
    if target not in USER_TRANSITIONS.get(current, frozenset()):
        return f"A {current.value.replace('_', ' ')} action cannot be changed to {target.value.replace('_', ' ')}."
    if target is S.approved:
        if info.risk_class is RiskClass.manual:
            return "This is a manual task: tick it off when it is done instead of approving it."
        if not has_value and info.value_kind in ("ai", "deterministic"):
            return (
                "There is no proposed value yet. Edit the action to write one, or add an AI key and re-plan."
            )
    if target is S.manual_done and info.risk_class is not RiskClass.manual:
        return "Only manual tasks can be marked done; other actions are approved and then deployed."
    return None


def restored_status(action_type: str, *, has_value: bool) -> ActionStatus:
    """Where a rejected / completed action goes when it is reopened."""
    info = info_for(action_type)
    if has_value or info.value_kind in ("brief", "checklist"):
        return S.proposed
    return S.needs_ai
