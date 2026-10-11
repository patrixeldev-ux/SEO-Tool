"""Job tests. These run a REAL Celery worker (in-process thread) against REAL Redis and Postgres."""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from searchos.models import Job, JobStatus
from tests.conftest import auth, register


@pytest.fixture(scope="module")
def celery_worker(test_env: dict[str, str]) -> Iterator[None]:
    from celery.contrib.testing.worker import start_worker

    from searchos.worker.app import app

    app.conf.broker_url = test_env["REDIS_URL"]
    with start_worker(app, perform_ping_check=False, pool="solo", loglevel="WARNING", shutdown_timeout=20):
        yield


async def poll_until_finished(
    client: AsyncClient, tokens: dict[str, Any], job_id: str
) -> list[dict[str, Any]]:
    """Poll GET /jobs/{id}; return every distinct observation until the job is done or failed."""
    seen: list[dict[str, Any]] = []
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        resp = await client.get(f"/jobs/{job_id}", headers=auth(tokens))
        assert resp.status_code == 200
        body = resp.json()
        if not seen or (seen[-1]["status"], seen[-1]["progress"]) != (body["status"], body["progress"]):
            seen.append(body)
        if body["status"] in ("done", "failed", "cancelled"):
            return seen
        await asyncio.sleep(0.03)
    raise AssertionError(f"job did not finish: {seen}")


async def test_demo_job_goes_queued_running_done(client: AsyncClient, celery_worker: None) -> None:
    tokens = await register(client, "a@example.com")
    resp = await client.post("/jobs/demo", json={"steps": 5, "step_seconds": 0.25}, headers=auth(tokens))
    assert resp.status_code == 202
    job = resp.json()
    assert job["status"] == "queued"
    assert job["type"] == "demo"
    assert job["progress"] == 0

    observed = await poll_until_finished(client, tokens, job["id"])
    statuses = [o["status"] for o in observed]
    assert "running" in statuses, observed
    assert statuses[-1] == "done"
    # status never goes backwards: queued* -> running* -> done
    order = {"queued": 0, "running": 1, "done": 2}
    assert [order[s] for s in statuses] == sorted(order[s] for s in statuses)
    progress = [o["progress"] for o in observed]
    assert progress == sorted(progress)
    final = observed[-1]
    assert final["progress"] == 100
    assert final["result"] == {"steps_completed": 5}
    assert final["error"] is None
    assert final["started_at"] and final["finished_at"]


async def test_failing_job_records_error(client: AsyncClient, celery_worker: None) -> None:
    tokens = await register(client, "a@example.com")
    resp = await client.post(
        "/jobs/demo", json={"steps": 4, "step_seconds": 0.02, "fail": True}, headers=auth(tokens)
    )
    observed = await poll_until_finished(client, tokens, resp.json()["id"])
    final = observed[-1]
    assert final["status"] == "failed"
    assert "RuntimeError: demo job asked to fail" in final["error"]
    assert final["finished_at"]


async def test_jobs_are_scoped_to_their_org(client: AsyncClient, celery_worker: None) -> None:
    a = await register(client, "a@example.com")
    b = await register(client, "b@example.com")
    job = (await client.post("/jobs/demo", json={"steps": 1, "step_seconds": 0}, headers=auth(a))).json()
    assert (await client.get(f"/jobs/{job['id']}", headers=auth(b))).status_code == 404
    assert (await client.get(f"/jobs/{uuid.uuid4()}", headers=auth(a))).status_code == 404
    await poll_until_finished(client, a, job["id"])


async def test_job_endpoints_require_auth(client: AsyncClient) -> None:
    assert (await client.get(f"/jobs/{uuid.uuid4()}")).status_code == 401
    assert (await client.post("/jobs/demo", json={})).status_code == 401


async def test_demo_job_input_is_bounded(client: AsyncClient) -> None:
    tokens = await register(client, "a@example.com")
    resp = await client.post("/jobs/demo", json={"steps": 10_000}, headers=auth(tokens))
    assert resp.status_code == 422


async def test_only_queued_jobs_can_be_claimed(session: AsyncSession, test_env: dict[str, str]) -> None:
    """A duplicate delivery of the same task must not run the job twice."""
    from searchos.core.db import get_engine
    from searchos.models import Org
    from searchos.worker.jobs import JobContext, run_job

    org = Org(name="x")
    session.add(org)
    await session.flush()
    job = Job(org_id=org.id, type="noop", status=JobStatus.done)
    session.add(job)
    await session.commit()

    calls: list[int] = []

    async def handler(_ctx: JobContext) -> dict[str, Any]:
        calls.append(1)
        return {}

    await run_job(job.id, handler, engine=get_engine())
    assert calls == []
    await session.refresh(job)
    assert job.status is JobStatus.done


async def test_run_job_state_transitions_without_a_broker(
    session: AsyncSession, test_env: dict[str, str]
) -> None:
    from searchos.core.db import get_engine
    from searchos.models import Org
    from searchos.worker.jobs import JobContext, run_job

    org = Org(name="x")
    session.add(org)
    await session.flush()
    job = Job(org_id=org.id, type="noop", payload={"n": 3})
    session.add(job)
    await session.commit()
    assert job.status is JobStatus.queued

    seen_status: list[JobStatus] = []

    async def handler(ctx: JobContext) -> dict[str, Any]:
        async with session.bind.connect() as conn:  # type: ignore[union-attr]
            row = await conn.execute(sa.select(Job.status).where(Job.id == ctx.job_id))
            seen_status.append(row.scalar_one())
        await ctx.set_progress(50)
        return {"n": ctx.payload["n"]}

    await run_job(job.id, handler, engine=get_engine())
    assert seen_status == [JobStatus.running]
    await session.refresh(job)
    assert job.status is JobStatus.done
    assert job.progress == 100
    assert job.result == {"n": 3}
