"""Development helper: a live smoke run of deploy -> verify -> rollback against a pretend website.

    python -m searchos.dev_deploy_smoke

Needs PostgreSQL and Redis (the same ones the app uses; see .env). It creates a throw-away database, starts the
real API (uvicorn) and the real Celery worker as sub-processes, and serves a fake website that behaves like our
Cloudflare Worker (devtools/fake_site.py: fetches the signed rules from the API, checks the signature, applies
them). Then it walks the whole path through the HTTP API:

    approve -> deploy -> the worker verifies it in the raw HTML -> rollback -> the original page is back
    autopilot (daily task, run by the worker) -> notification -> rollback of that batch

Two dev-only shortcuts stand in for things that need the public internet: the site's ownership proof and the
Worker connection test are marked as passed directly in the database, and DEPLOY_ALLOW_PRIVATE_HOSTS lets the
tools fetch 127.0.0.1. Refuses to run when ENVIRONMENT=production. Exit code 0 = every step passed.
"""

from __future__ import annotations

import asyncio
import os
import re
import subprocess
import sys
import time
import uuid
from collections.abc import Callable
from typing import Any

import httpx
import redis
import sqlalchemy as sa
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

API_PORT = 18080
SITE_PORT = 18081
REDIS_DB = 7
RESULTS: list[tuple[str, bool, str]] = []


def step(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    print(f"[{'ok' if ok else 'FAIL'}] {name}" + (f"  ({detail})" if detail else ""), flush=True)
    if not ok:
        raise SystemExit(1)


def title_of(html: str) -> str:
    m = re.search(r"<title>(.*?)</title>", html, re.S)
    return m.group(1) if m else ""


def description_of(html: str) -> str:
    m = re.search(r'<meta name="description" content="(.*?)"', html)
    return m.group(1) if m else ""


def wait_for(fn: Callable[[], bool], seconds: float, what: str) -> None:
    end = time.time() + seconds
    while time.time() < end:
        if fn():
            return
        time.sleep(1)
    step(f"waiting for {what}", False, f"gave up after {seconds:.0f}s")


async def sql(url: str, statements: list[tuple[str, dict[str, Any]]]) -> None:
    engine = create_async_engine(url)
    try:
        async with engine.begin() as conn:
            for text, params in statements:
                await conn.execute(sa.text(text), params)
    finally:
        await engine.dispose()


async def insert_actions(url: str, org_id: str, site_id: str, base: str) -> dict[str, str]:
    from searchos.models import Action
    from searchos.planner.catalog import ActionStatus, risk_class_for

    engine = create_async_engine(url)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    ids: dict[str, str] = {}
    try:
        async with maker() as session:
            for key, kind, path, field, current, proposed, status, priority in (
                (
                    "title",
                    "rewrite_title",
                    "/grinders",
                    "title",
                    "Grinders",
                    "Burr coffee grinders for home brewing",
                    ActionStatus.approved,
                    5.0,
                ),
                (
                    "meta",
                    "rewrite_meta",
                    "/",
                    "meta_description",
                    "Coffee.",
                    "Small-batch coffee roasted in Harbor Town and shipped within two days of roasting.",
                    ActionStatus.proposed,
                    3.0,
                ),
            ):
                action = Action(
                    org_id=uuid.UUID(org_id),
                    site_id=uuid.UUID(site_id),
                    type=kind,
                    risk_class=risk_class_for(kind),
                    target_url=f"{base}{path}",
                    field=field,
                    current_value=current,
                    proposed_value=proposed,
                    effort=1,
                    priority=priority,
                    status=status,
                )
                session.add(action)
                await session.flush()
                ids[key] = str(action.id)
            await session.commit()
    finally:
        await engine.dispose()
    return ids


def main() -> int:
    if os.environ.get("ENVIRONMENT") == "production":
        print("Refusing to run in production.")
        return 2
    from dotenv import dotenv_values  # python-dotenv ships with pydantic-settings

    env_file = {k: v for k, v in dotenv_values(".env").items() if v is not None}
    base_db = os.environ.get("DATABASE_URL") or env_file.get(
        "DATABASE_URL", "postgresql+asyncpg://searchos:searchos@localhost:5432/searchos"
    )
    redis_base = os.environ.get("REDIS_URL") or env_file.get("REDIS_URL", "redis://localhost:6379/0")
    redis_url = redis_base.rsplit("/", 1)[0] + f"/{REDIS_DB}"
    db_name = f"searchos_smoke_{uuid.uuid4().hex[:8]}"
    db_url = make_url(base_db).set(database=db_name).render_as_string(hide_password=False)

    asyncio.run(_admin(base_db, f'CREATE DATABASE "{db_name}"'))
    child_env = {
        **os.environ,
        "ENVIRONMENT": "development",
        "DATABASE_URL": db_url,
        "REDIS_URL": redis_url,
        "JWT_SECRET": "smoke-only-secret-0123456789-0123456789",
        "DEPLOY_ALLOW_PRIVATE_HOSTS": "true",
        "PUBLIC_API_URL": f"http://127.0.0.1:{API_PORT}",
        "CORS_ORIGINS": "http://localhost:5173",
    }
    procs: list[subprocess.Popen[bytes]] = []
    try:
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            env=child_env,
            check=True,
            capture_output=True,
        )
        redis.Redis.from_url(redis_url).flushdb()
        procs.append(
            subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "searchos.main:create_app",
                    "--factory",
                    "--port",
                    str(API_PORT),
                    "--log-level",
                    "warning",
                ],
                env=child_env,
            )
        )
        procs.append(
            subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "celery",
                    "-A",
                    "searchos.worker.app",
                    "worker",
                    "-P",
                    "solo",
                    "-l",
                    "info",
                ],
                env=child_env,
            )
        )
        api = f"http://127.0.0.1:{API_PORT}"
        wait_for(lambda: _up(f"{api}/health"), 60, "the API")
        run(api, db_url, child_env, procs)
    finally:
        for p in procs:
            p.terminate()
        for p in procs:
            try:
                p.wait(10)
            except subprocess.TimeoutExpired:
                p.kill()
        redis.Redis.from_url(redis_url).flushdb()
        asyncio.run(_admin(base_db, f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)'))
    print(f"\n{sum(1 for _, ok, _ in RESULTS if ok)} of {len(RESULTS)} steps passed.")
    return 0


async def _admin(base_url: str, statement: str) -> None:
    engine = create_async_engine(make_url(base_url).set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            await conn.execute(sa.text(statement))
    finally:
        await engine.dispose()


def _up(url: str) -> bool:
    try:
        return httpx.get(url, timeout=2).status_code < 500
    except httpx.HTTPError:
        return False


def run(api: str, db_url: str, child_env: dict[str, str], procs: list[subprocess.Popen[bytes]]) -> None:
    http = httpx.Client(base_url=api, timeout=30)
    tokens = http.post(
        "/auth/register",
        json={"email": "smoke@example.com", "password": "smoke-password-123", "org_name": "Smoke"},
    ).json()
    http.headers["Authorization"] = f"Bearer {tokens['access_token']}"
    site = http.post("/sites", json={"domain": "shop.test", "display_name": "Smoke shop"}).json()
    site_id, org_id = site["id"], site["org_id"]
    base = f"http://127.0.0.1:{SITE_PORT}"
    step("registered a user and a site", bool(site_id))

    http.get(f"/sites/{site_id}/deploy/ownership")  # creates the proof token
    asyncio.run(
        sql(
            db_url,
            [
                (
                    "UPDATE sites SET domain = '127.0.0.1', verified_at = now(), verify_checked_at = now(), "
                    "verify_method = 'dns' WHERE id = :id",
                    {"id": uuid.UUID(site_id)},
                )
            ],
        )
    )
    setup = http.put(f"/sites/{site_id}/deploy/connections/cloudflare").json()
    asyncio.run(
        sql(
            db_url,
            [
                (
                    "UPDATE integration_connections SET status = 'connected' WHERE site_id = :id",
                    {"id": uuid.UUID(site_id)},
                )
            ],
        )
    )
    chosen = http.put(f"/sites/{site_id}/deploy/method", json={"method": "cloudflare"})
    step(
        "Cloudflare connection created and chosen (dev shortcut for the live test)",
        chosen.status_code == 200,
        chosen.text[:80],
    )

    procs.append(
        subprocess.Popen(
            [
                sys.executable,
                "-m",
                "searchos.devtools.fake_site",
                "--port",
                str(SITE_PORT),
                "--edge-api",
                f"http://127.0.0.1:{API_PORT}",
                f"--edge-token={setup['site_token']}",
                f"--edge-secret={setup['signing_secret']}",
            ],
            env=child_env,
            stdout=subprocess.DEVNULL,
        )
    )
    wait_for(lambda: _up(f"{base}/"), 20, "the fake website")
    page = httpx.get(f"{base}/grinders")
    step(
        "the website serves its original page",
        title_of(page.text) == "Grinders",
        page.headers.get("x-searchos-edge", ""),
    )

    ids = asyncio.run(insert_actions(db_url, org_id, site_id, base))
    deploy = http.post(f"/sites/{site_id}/deploy", json={"all_approved": True})
    body = deploy.json()
    step("deploy accepted", deploy.status_code == 201 and len(body["deployments"]) == 1, deploy.text[:120])
    dep_id = body["deployments"][0]["id"]
    live = httpx.get(f"{base}/grinders")
    step(
        "the change is live through the Worker",
        title_of(live.text) == "Burr coffee grinders for home brewing"
        and live.headers["x-searchos-edge"] == "on",
        title_of(live.text),
    )

    def verified() -> bool:
        rows = http.get(f"/sites/{site_id}/deploy/deployments").json()
        return any(d["id"] == dep_id and d["status"] == "verified" for d in rows)

    wait_for(verified, 150, "the worker to verify the deployment")
    step("the worker verified it in the raw HTML", True)

    rb = http.post(f"/sites/{site_id}/deploy/rollback", json={"deployment_ids": [dep_id]})
    after = httpx.get(f"{base}/grinders")
    step(
        "rollback restored the original page",
        rb.status_code == 200 and len(rb.json()["reverted"]) == 1 and after.text == page.text,
        title_of(after.text),
    )
    again = http.post(f"/sites/{site_id}/deploy/rollback", json={"deployment_ids": [dep_id]})
    step(
        "a second rollback does nothing and does not fail",
        again.status_code == 200 and again.json()["reverted"] == [],
    )

    http.put(f"/sites/{site_id}/deploy/autopilot", json={"enabled": True, "weekly_cap": 5})
    del ids  # the autopilot picks the proposed meta action by itself
    os.environ.update({k: child_env[k] for k in ("DATABASE_URL", "REDIS_URL", "JWT_SECRET")})
    from searchos.core.config import get_settings

    get_settings.cache_clear()
    from searchos.worker.tasks import deploy_autopilot

    deploy_autopilot.apply_async()

    def autopilot_batch() -> bool:
        batches = http.get(f"/sites/{site_id}/deploy/batches").json()
        return any(b["trigger"] == "autopilot" and b["status"] == "verified" for b in batches)

    wait_for(autopilot_batch, 150, "autopilot to deploy and the worker to verify")
    home = httpx.get(f"{base}/")
    step(
        "autopilot changed the home page description",
        description_of(home.text).startswith("Small-batch coffee"),
        description_of(home.text)[:40],
    )
    notes = http.get("/notifications").json()
    step(
        "one notification was written",
        notes["unread"] == 1 and notes["items"][0]["kind"] == "autopilot_batch",
        notes["items"][0]["title"],
    )
    batch = next(
        b for b in http.get(f"/sites/{site_id}/deploy/batches").json() if b["trigger"] == "autopilot"
    )
    rb2 = http.post(f"/sites/{site_id}/deploy/batches/{batch['id']}/rollback")
    home2 = httpx.get(f"{base}/")
    step(
        "rolling back the autopilot batch restores the home page",
        rb2.status_code == 200 and description_of(home2.text) == "Coffee.",
        description_of(home2.text),
    )


if __name__ == "__main__":
    raise SystemExit(main())
