"""Settings > AI: which provider is in use, whether keys are present (never the keys), and the monthly token budget."""

from __future__ import annotations

from typing import Annotated

import sqlalchemy as sa
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from searchos.api.actions import AiStatusOut, ai_status_out
from searchos.core.deps import OrgContext, require_role
from searchos.llm.budget import budget_for, month_start, used_this_month
from searchos.models import LlmUsage, Org, Role

router = APIRouter(prefix="/ai", tags=["ai"])

ReadCtx = Annotated[OrgContext, Depends(require_role(Role.client_viewer))]
AdminCtx = Annotated[OrgContext, Depends(require_role(Role.admin))]


class UsageRow(BaseModel):
    purpose: str
    calls: int
    tokens: int


class AiSettingsOut(AiStatusOut):
    monthly_token_budget: int
    budget_is_custom: bool
    tokens_used_this_month: int
    month_starts: str
    usage_by_purpose: list[UsageRow]


class BudgetBody(BaseModel):
    # None = back to the default (LLM_DEFAULT_MONTHLY_TOKEN_BUDGET)
    monthly_token_budget: int | None = Field(default=None, ge=0, le=1_000_000_000)


async def settings_out(ctx: OrgContext) -> AiSettingsOut:
    org = await ctx.session.get(Org, ctx.org_id)
    tokens = sa.func.coalesce(sa.func.sum(LlmUsage.input_tokens + LlmUsage.output_tokens), 0).label("tokens")
    rows = (
        await ctx.session.execute(
            sa.select(LlmUsage.purpose, sa.func.count(), tokens)
            .where(LlmUsage.org_id == ctx.org_id, LlmUsage.created_at >= month_start())
            .group_by(LlmUsage.purpose)
            .order_by(tokens.desc(), LlmUsage.purpose)
        )
    ).all()
    return AiSettingsOut(
        **ai_status_out().model_dump(),
        monthly_token_budget=await budget_for(ctx.session, ctx.org_id),
        budget_is_custom=bool(org and org.llm_token_budget is not None),
        tokens_used_this_month=await used_this_month(ctx.session, ctx.org_id),
        month_starts=month_start().date().isoformat(),
        usage_by_purpose=[UsageRow(purpose=p, calls=int(n), tokens=int(t)) for p, n, t in rows],
    )


@router.get("/status", response_model=AiSettingsOut)
async def ai_status(ctx: ReadCtx) -> AiSettingsOut:
    """Provider and key presence (never a key) plus this month's token use against the organisation's budget."""
    return await settings_out(ctx)


@router.put("/budget", response_model=AiSettingsOut)
async def set_budget(body: BudgetBody, ctx: AdminCtx) -> AiSettingsOut:
    org = await ctx.session.get(Org, ctx.org_id)
    assert org is not None  # noqa: S101 - the caller belongs to it
    org.llm_token_budget = body.monthly_token_budget
    await ctx.session.commit()
    return await settings_out(ctx)
