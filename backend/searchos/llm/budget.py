"""Per-organisation monthly AI token budget, enforced before every call and logged in `llm_usage`."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from searchos.core.config import get_settings
from searchos.llm.base import LLMBudgetExceeded, Usage
from searchos.models import LlmUsage, Org


def month_start(now: datetime | None = None) -> datetime:
    now = now or datetime.now(UTC)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


async def budget_for(session: AsyncSession, org_id: uuid.UUID) -> int:
    custom = await session.scalar(sa.select(Org.llm_token_budget).where(Org.id == org_id))
    return custom if custom is not None else get_settings().llm_default_monthly_token_budget


async def used_this_month(session: AsyncSession, org_id: uuid.UUID, now: datetime | None = None) -> int:
    total = await session.scalar(
        sa.select(sa.func.coalesce(sa.func.sum(LlmUsage.input_tokens + LlmUsage.output_tokens), 0)).where(
            LlmUsage.org_id == org_id, LlmUsage.created_at >= month_start(now)
        )
    )
    return int(total or 0)


async def ensure_within_budget(
    session: AsyncSession, org_id: uuid.UUID, estimate: int, now: datetime | None = None
) -> None:
    """Raises `LLMBudgetExceeded` when the call (estimated at `estimate` tokens) would pass the monthly budget."""
    budget = await budget_for(session, org_id)
    used = await used_this_month(session, org_id, now)
    if used + max(0, estimate) > budget:
        raise LLMBudgetExceeded(
            f"The monthly AI token budget ({budget:,} tokens) is used up ({used:,} used). "
            "It resets on the 1st; an admin can raise it in Settings > AI."
        )


async def record_usage(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    site_id: uuid.UUID | None,
    provider: str,
    model: str,
    purpose: str,
    usage: Usage,
    ok: bool,
) -> None:
    session.add(
        LlmUsage(
            org_id=org_id,
            site_id=site_id,
            provider=provider,
            model=model[:80],
            purpose=purpose[:40],
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            ok=ok,
        )
    )
    await session.commit()


def estimate_tokens(*texts: str, output: int = 600) -> int:
    """A cheap upper-ish estimate before a call: about 4 characters per token, plus the expected answer."""
    return sum(len(t) for t in texts) // 3 + output
