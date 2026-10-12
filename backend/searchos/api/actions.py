"""The action plan of a site: list it, summarise it, change the status of single actions or many at once."""

from __future__ import annotations

import csv
import io
import json
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi import status as http_status
from pydantic import BaseModel, Field

from searchos.api.schemas import JobOut
from searchos.core.deps import OrgContext, require_role
from searchos.llm import provider_status
from searchos.models import Action, Job, Role, Site
from searchos.planner import handoff, service
from searchos.planner.catalog import ACTION_TYPES, ActionStatus, RiskClass

router = APIRouter(prefix="/sites/{site_id}/actions", tags=["actions"])

ReadCtx = Annotated[OrgContext, Depends(require_role(Role.client_viewer))]
WriteCtx = Annotated[OrgContext, Depends(require_role(Role.member))]
MAX_LIMIT = 2000


class ActionOut(BaseModel):
    id: uuid.UUID
    site_id: uuid.UUID
    type: str
    type_label: str
    risk_class: RiskClass
    target_url: str | None
    field: str
    current_value: Any | None
    proposed_value: Any | None
    original_value: Any | None
    edited_by_user: bool
    evidence: dict[str, Any]
    expected_gain: float
    confidence: float
    effort: int
    priority: float
    status: ActionStatus
    created_by: str
    source_kind: str
    sources: list[str]
    generation: dict[str, Any]
    checklist_done: list[str]
    reject_reason: str | None
    decided_at: datetime | None
    created_at: datetime
    updated_at: datetime


def action_out(action: Action) -> ActionOut:
    info = ACTION_TYPES[action.type]
    return ActionOut(
        id=action.id,
        site_id=action.site_id,
        type=action.type,
        type_label=info.label,
        risk_class=action.risk_class,
        target_url=action.target_url,
        field=action.field,
        current_value=action.current_value,
        proposed_value=action.proposed_value,
        original_value=action.original_value,
        edited_by_user=action.edited_by_user,
        evidence=action.evidence,
        expected_gain=action.expected_gain,
        confidence=action.confidence,
        effort=action.effort,
        priority=action.priority,
        status=action.status,
        created_by=action.created_by,
        source_kind=action.source_kind,
        sources=list(action.sources),
        generation=action.generation,
        checklist_done=list(action.checklist_done),
        reject_reason=action.reject_reason,
        decided_at=action.decided_at,
        created_at=action.created_at,
        updated_at=action.updated_at,
    )


async def site_or_404(ctx: OrgContext, site_id: uuid.UUID) -> Site:
    return await ctx.get_or_404(Site, site_id, "Site")


@router.get("", response_model=list[ActionOut])
async def list_actions(
    site_id: uuid.UUID,
    ctx: ReadCtx,
    status: Annotated[list[ActionStatus] | None, Query()] = None,
    risk_class: Annotated[list[RiskClass] | None, Query()] = None,
    type: Annotated[list[str] | None, Query()] = None,
    manual: bool | None = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = 500,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[ActionOut]:
    """Actions of the site, highest priority first. Without a `status` filter, closed ones (obsolete) are left out."""
    await site_or_404(ctx, site_id)
    query = ctx.select(Action).where(Action.site_id == site_id)
    if status:
        query = query.where(Action.status.in_(status))
    else:
        query = query.where(Action.status != ActionStatus.obsolete)
    if risk_class:
        query = query.where(Action.risk_class.in_(risk_class))
    if type:
        unknown = [t for t in type if t not in ACTION_TYPES]
        if unknown:
            raise HTTPException(422, f"Unknown action type: {unknown[0]}")
        query = query.where(Action.type.in_(type))
    if manual is not None:
        is_manual = Action.risk_class == RiskClass.manual
        query = query.where(is_manual if manual else sa.not_(is_manual))
    query = query.order_by(Action.priority.desc(), Action.created_at, Action.id).limit(limit).offset(offset)
    rows = (await ctx.session.scalars(query)).all()
    return [action_out(a) for a in rows]


@router.post("/replan", response_model=JobOut, status_code=http_status.HTTP_202_ACCEPTED)
async def replan(site_id: uuid.UUID, ctx: WriteCtx) -> Job:
    """Queue a fresh plan from the latest audit and Search Console data. Rejected actions stay rejected."""
    from searchos.worker.launch import start_plan

    site = await site_or_404(ctx, site_id)
    return await start_plan(ctx.session, site.id, site.org_id, trigger="manual", created_by=ctx.user.id)


# ---- summary ---------------------------------------------------------------------------------------------
BOARD_STATUSES = (
    ActionStatus.proposed,
    ActionStatus.needs_ai,
    ActionStatus.no_suggestion,
    ActionStatus.approved,
)


class PlanRunOut(BaseModel):
    id: uuid.UUID
    status: str
    created_at: datetime
    finished_at: datetime | None
    trigger: str | None
    result: dict[str, Any] | None
    error: str | None


class AiStatusOut(BaseModel):
    configured: bool
    provider: str | None
    model: str | None
    selected_by: str | None
    anthropic_key_present: bool
    gemini_key_present: bool
    gemini_model_set: bool
    problem: str | None


class SummaryOut(BaseModel):
    total: int
    by_status: dict[str, int]
    by_risk: dict[str, int]  # actions still on the board (proposed, needs AI, no suggestion, approved)
    by_type: dict[str, int]
    needs_ai: int
    # Extra clicks per 28 days if everything on the board were done. `prior` is guesses, `measured` is from
    # Search Console numbers; they are never added together silently.
    gain_measured: float
    gain_prior: float
    gain_approved: float
    last_plan: PlanRunOut | None
    plan_running: bool
    ai: AiStatusOut
    autopilot_enabled: bool


def plan_run_out(job: Job) -> PlanRunOut:
    payload = job.payload or {}
    return PlanRunOut(
        id=job.id,
        status=job.status.value,
        created_at=job.created_at,
        finished_at=job.finished_at,
        trigger=payload.get("trigger"),
        result=job.result,
        error=job.error,
    )


def ai_status_out() -> AiStatusOut:
    st = provider_status()
    return AiStatusOut(
        configured=st.configured,
        provider=st.provider,
        model=st.model,
        selected_by=st.selected_by,
        anthropic_key_present=st.anthropic_key_present,
        gemini_key_present=st.gemini_key_present,
        gemini_model_set=st.gemini_model_set,
        problem=st.problem,
    )


@router.get("/summary", response_model=SummaryOut)
async def summary(site_id: uuid.UUID, ctx: ReadCtx) -> SummaryOut:
    site = await site_or_404(ctx, site_id)
    base = (Action.site_id == site_id, Action.status != ActionStatus.obsolete)
    by_status = {
        st.value: n
        for st, n in (
            await ctx.session.execute(
                sa.select(Action.status, sa.func.count()).where(*base).group_by(Action.status)
            )
        ).all()
    }
    board = Action.status.in_(BOARD_STATUSES)
    by_risk = {
        r.value: n
        for r, n in (
            await ctx.session.execute(
                sa.select(Action.risk_class, sa.func.count()).where(*base, board).group_by(Action.risk_class)
            )
        ).all()
    }
    type_rows = (
        await ctx.session.execute(
            sa.select(Action.type, sa.func.count()).where(*base, board).group_by(Action.type)
        )
    ).all()
    by_type = {t: n for t, n in type_rows}  # noqa: C416 - Row is not a 2-tuple for mypy's dict()
    basis = Action.evidence["gain_basis"].astext
    gains = {
        key: float(total or 0)
        for key, total in (
            await ctx.session.execute(
                sa.select(basis, sa.func.sum(Action.expected_gain)).where(*base, board).group_by(basis)
            )
        ).all()
    }
    approved_gain = await ctx.session.scalar(
        sa.select(sa.func.coalesce(sa.func.sum(Action.expected_gain), 0)).where(
            Action.site_id == site_id, Action.status == ActionStatus.approved
        )
    )
    plans = (
        await ctx.session.scalars(
            sa.select(Job)
            .where(Job.site_id == site_id, Job.type == "plan", Job.org_id == ctx.org_id)
            .order_by(Job.created_at.desc())
            .limit(5)
        )
    ).all()
    last_done = next((j for j in plans if j.status.value in ("done", "failed")), None)
    return SummaryOut(
        total=sum(by_status.values()),
        by_status=by_status,
        by_risk=by_risk,
        by_type=by_type,
        needs_ai=by_status.get(ActionStatus.needs_ai.value, 0),
        gain_measured=round(gains.get("gsc", 0.0), 1),
        gain_prior=round(sum(v for k, v in gains.items() if k != "gsc"), 1),
        gain_approved=round(float(approved_gain or 0), 1),
        last_plan=plan_run_out(last_done) if last_done else None,
        plan_running=any(j.status.value in ("queued", "running") for j in plans),
        ai=ai_status_out(),
        autopilot_enabled=site.autopilot_enabled,
    )


# ---- export -----------------------------------------------------------------------------------------------
CSV_COLUMNS = (
    "priority",
    "status",
    "risk_class",
    "type",
    "page",
    "field",
    "current_value",
    "proposed_value",
    "why",
    "expected_extra_clicks_28d",
    "gain_basis",
    "confidence",
    "effort_1_to_5",
    "edited_by_person",
    "reject_reason",
)


def safe_cell(value: Any) -> str:
    """A spreadsheet cell that cannot run as a formula (CSV injection): leading = + - @ tab CR are defused."""
    if value is None:
        return ""
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


@router.get("/export.csv")
async def export_csv(
    site_id: uuid.UUID,
    ctx: ReadCtx,
    status: Annotated[list[ActionStatus] | None, Query()] = None,
) -> Response:
    site = await site_or_404(ctx, site_id)
    query = ctx.select(Action).where(Action.site_id == site_id)
    query = query.where(Action.status.in_(status) if status else Action.status.in_(BOARD_STATUSES))
    rows = (await ctx.session.scalars(query.order_by(Action.priority.desc(), Action.id))).all()
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(CSV_COLUMNS)
    for a in rows:
        why = (a.evidence or {}).get("why") or []
        writer.writerow(
            [
                safe_cell(round(a.priority, 4)),
                a.status.value,
                a.risk_class.value,
                a.type,
                safe_cell(a.target_url),
                safe_cell(a.field),
                safe_cell(a.current_value),
                safe_cell(a.proposed_value),
                safe_cell(why[0] if why else ""),
                safe_cell(round(a.expected_gain, 2)),
                (a.evidence or {}).get("gain_basis", ""),
                round(a.confidence, 2),
                a.effort,
                "yes" if a.edited_by_user else "no",
                safe_cell(a.reject_reason),
            ]
        )
    name = f"{site.domain}-action-plan-{datetime.now(UTC):%Y-%m-%d}.csv"
    return Response(
        out.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


class HandoffOut(BaseModel):
    text: str
    count: int
    generated_on: str
    includes_unapproved: bool


@router.get("/handoff", response_model=HandoffOut)
async def developer_handoff(site_id: uuid.UUID, ctx: ReadCtx, include_proposed: bool = False) -> HandoffOut:
    """A paste-able text for a developer: the approved changes, plus technical tasks that need no approval."""
    site = await site_or_404(ctx, site_id)
    ticket = sa.and_(
        Action.type.in_(handoff.TICKET_TYPES),
        Action.status.in_((ActionStatus.proposed, ActionStatus.approved)),
    )
    approved = Action.status == ActionStatus.approved
    proposed_page_changes = sa.and_(
        Action.status == ActionStatus.proposed,
        Action.risk_class != RiskClass.manual,
        Action.proposed_value.is_not(None),
    )
    wanted = sa.or_(approved, ticket, proposed_page_changes) if include_proposed else sa.or_(approved, ticket)
    rows = (
        await ctx.session.scalars(
            ctx.select(Action)
            .where(Action.site_id == site_id, wanted, Action.risk_class != RiskClass.manual)
            .order_by(Action.priority.desc(), Action.id)
        )
    ).all()
    manual_tickets = (
        await ctx.session.scalars(
            ctx.select(Action)
            .where(Action.site_id == site_id, ticket)
            .order_by(Action.priority.desc(), Action.id)
        )
    ).all()
    seen = {a.id for a in rows}
    rows = [*rows, *(a for a in manual_tickets if a.id not in seen)]
    today = f"{datetime.now(UTC):%Y-%m-%d}"
    text = handoff.render(site.display_name, site.domain, rows, generated_on=today)
    return HandoffOut(text=text, count=len(rows), generated_on=today, includes_unapproved=include_proposed)


# ---- decisions --------------------------------------------------------------------------------------------
class RejectBody(BaseModel):
    reason: str = Field(min_length=1, max_length=service.MAX_REASON_CHARS * 2)


class EditBody(BaseModel):
    proposed_value: Any | None = None
    checklist_done: list[str] | None = Field(default=None, max_length=100)


class BulkBody(BaseModel):
    ids: list[uuid.UUID] = Field(min_length=1, max_length=500)
    op: Literal["approve", "reject", "reopen", "done"]
    reason: str | None = None


class BulkFailure(BaseModel):
    id: uuid.UUID
    error: str


class BulkOut(BaseModel):
    updated: list[ActionOut]
    failed: list[BulkFailure]


async def locked(ctx: OrgContext, site_id: uuid.UUID, ids: list[uuid.UUID]) -> dict[uuid.UUID, Action]:
    rows = await ctx.session.scalars(
        ctx.select(Action)
        .where(Action.site_id == site_id, Action.id.in_(ids))
        .order_by(Action.id)
        .with_for_update()
    )
    return {a.id: a for a in rows}


async def apply_op(ctx: OrgContext, action: Action, op: str, reason: str | None) -> str | None:
    if op == "approve":
        return service.approve(action, ctx.user.id)
    if op == "reject":
        return service.reject(action, ctx.user.id, reason or "")
    if op == "reopen":
        return await service.reopen(ctx.session, action)
    return service.complete(action, ctx.user.id)


@router.post("/bulk", response_model=BulkOut)
async def bulk(site_id: uuid.UUID, body: BulkBody, ctx: WriteCtx) -> BulkOut:
    """Approve, reject (one reason for all), reopen or complete many actions. Each one succeeds or fails alone."""
    await site_or_404(ctx, site_id)
    found = await locked(ctx, site_id, list(dict.fromkeys(body.ids)))
    updated: list[ActionOut] = []
    failed: list[BulkFailure] = []
    for action_id in dict.fromkeys(body.ids):
        action = found.get(action_id)
        if action is None:
            failed.append(BulkFailure(id=action_id, error="Action not found"))
            continue
        error = await apply_op(ctx, action, body.op, body.reason)
        if error:
            failed.append(BulkFailure(id=action_id, error=error))
        else:
            updated.append(action_out(action))
    await ctx.session.commit()
    return BulkOut(updated=updated, failed=failed)


async def one(ctx: OrgContext, site_id: uuid.UUID, action_id: uuid.UUID) -> Action:
    found = await locked(ctx, site_id, [action_id])
    action = found.get(action_id)
    if action is None:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, "Action not found")
    return action


@router.get("/{action_id}", response_model=ActionOut)
async def get_action(site_id: uuid.UUID, action_id: uuid.UUID, ctx: ReadCtx) -> ActionOut:
    await site_or_404(ctx, site_id)
    action = await ctx.session.scalar(
        ctx.select(Action).where(Action.site_id == site_id, Action.id == action_id)
    )
    if action is None:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, "Action not found")
    return action_out(action)


async def decide(
    ctx: OrgContext, site_id: uuid.UUID, action_id: uuid.UUID, op: str, reason: str | None = None
) -> ActionOut:
    await site_or_404(ctx, site_id)
    action = await one(ctx, site_id, action_id)
    error = await apply_op(ctx, action, op, reason)
    if error:
        await ctx.session.rollback()
        raise HTTPException(http_status.HTTP_409_CONFLICT, error)
    await ctx.session.commit()
    return action_out(action)


@router.post("/{action_id}/approve", response_model=ActionOut)
async def approve_action(site_id: uuid.UUID, action_id: uuid.UUID, ctx: WriteCtx) -> ActionOut:
    return await decide(ctx, site_id, action_id, "approve")


@router.post("/{action_id}/reject", response_model=ActionOut)
async def reject_action(
    site_id: uuid.UUID, action_id: uuid.UUID, body: RejectBody, ctx: WriteCtx
) -> ActionOut:
    return await decide(ctx, site_id, action_id, "reject", body.reason)


@router.post("/{action_id}/reopen", response_model=ActionOut)
async def reopen_action(site_id: uuid.UUID, action_id: uuid.UUID, ctx: WriteCtx) -> ActionOut:
    return await decide(ctx, site_id, action_id, "reopen")


@router.post("/{action_id}/done", response_model=ActionOut)
async def complete_action(site_id: uuid.UUID, action_id: uuid.UUID, ctx: WriteCtx) -> ActionOut:
    return await decide(ctx, site_id, action_id, "done")


@router.post("/{action_id}/restore", response_model=ActionOut)
async def restore_action(site_id: uuid.UUID, action_id: uuid.UUID, ctx: WriteCtx) -> ActionOut:
    """Put the AI's original wording back after a person edited it."""
    await site_or_404(ctx, site_id)
    action = await one(ctx, site_id, action_id)
    error = service.restore_original(action)
    if error:
        await ctx.session.rollback()
        raise HTTPException(http_status.HTTP_409_CONFLICT, error)
    await ctx.session.commit()
    return action_out(action)


@router.patch("/{action_id}", response_model=ActionOut)
async def edit_action(site_id: uuid.UUID, action_id: uuid.UUID, body: EditBody, ctx: WriteCtx) -> ActionOut:
    """Edit the proposed value (it must pass the same checks as the AI's) and/or tick checklist items."""
    site = await site_or_404(ctx, site_id)
    action = await one(ctx, site_id, action_id)
    fields = body.model_fields_set
    if not fields & {"proposed_value", "checklist_done"}:
        raise HTTPException(422, "Nothing to change")
    if "proposed_value" in fields:
        result = await service.edit_value(ctx.session, site, action, body.proposed_value, ctx.user.id)
        if not result.ok:
            await ctx.session.rollback()
            raise HTTPException(
                422,
                {"message": "The new value does not pass the checks.", "problems": result.problems},
            )
    if "checklist_done" in fields:
        error = service.set_checklist(action, body.checklist_done or [])
        if error:
            await ctx.session.rollback()
            raise HTTPException(http_status.HTTP_409_CONFLICT, error)
    await ctx.session.commit()
    return action_out(action)
