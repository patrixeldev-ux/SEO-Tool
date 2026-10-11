from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from searchos.api.schemas import DemoJobRequest, JobOut
from searchos.core.deps import OrgContext, require_role
from searchos.models import Job, JobStatus, Role

router = APIRouter(prefix="/jobs", tags=["jobs"])
# Registered only outside production (see main.create_app).
dev_router = APIRouter(prefix="/jobs", tags=["jobs"])

ReadCtx = Annotated[OrgContext, Depends(require_role(Role.client_viewer))]
WriteCtx = Annotated[OrgContext, Depends(require_role(Role.member))]


@router.get("/{job_id}", response_model=JobOut)
async def get_job(job_id: uuid.UUID, ctx: ReadCtx) -> Job:
    return await ctx.get_or_404(Job, job_id, "Job")


@dev_router.post("/demo", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED)
async def start_demo_job(body: DemoJobRequest, ctx: WriteCtx) -> Job:
    """Enqueue the demo task (development/testing only; not registered in production)."""
    # Imported here so the API process only needs the Celery client when a job is actually enqueued.
    from searchos.worker.tasks import demo_task

    job = Job(
        org_id=ctx.org_id,
        created_by=ctx.user.id,
        type="demo",
        payload=body.model_dump(),
    )
    ctx.session.add(job)
    await ctx.session.commit()  # the row must be visible to the worker before it is enqueued
    try:
        demo_task.delay(str(job.id))
    except Exception:
        job.status = JobStatus.failed
        job.error = "Could not enqueue job: broker unavailable"
        await ctx.session.commit()
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Job queue unavailable") from None
    return job
