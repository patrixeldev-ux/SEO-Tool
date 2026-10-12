"""Test harness. Tests run against REAL Postgres and Redis (no SQLite, no fakes).

A throw-away database `searchos_test_<random>` is created per test session on the server named by
`TEST_DATABASE_URL` (or `DATABASE_URL`), migrated with Alembic (so migrations are exercised too), and dropped
at the end. Redis uses a separate logical DB (`TEST_REDIS_URL`, default db 15) which is flushed first.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import asyncpg
import pytest
import pytest_asyncio
import redis
from alembic import command
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession

TEST_JWT_SECRET = "test-only-secret-0123456789-0123456789-abcdef"
DEFAULT_DB_URL = "postgresql+asyncpg://searchos:searchos@localhost:5432/searchos"
BACKEND_DIR = Path(__file__).resolve().parent.parent
TEST_ORIGIN = "http://localhost:5173"
PASSWORD = "correct horse battery staple"


def _default_test_redis_url() -> str:
    """Same Redis server as REDIS_URL (e.g. the redis container) but logical DB 15, so dev data is safe."""
    base = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    return make_url(base).set(database="15").render_as_string(hide_password=False)


def _admin_dsn(base_url: str) -> str:
    url = make_url(base_url)
    return f"postgresql://{url.username}:{url.password}@{url.host}:{url.port or 5432}/postgres"


async def _run_admin(base_url: str, sql: str) -> None:
    conn = await asyncpg.connect(_admin_dsn(base_url))
    try:
        await conn.execute(sql)
    finally:
        await conn.close()


@pytest.fixture(scope="session")
def test_env() -> Iterator[dict[str, str]]:
    base_url = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL") or DEFAULT_DB_URL
    redis_url = os.environ.get("TEST_REDIS_URL") or _default_test_redis_url()
    db_name = f"searchos_test_{uuid.uuid4().hex[:8]}"
    db_url = make_url(base_url).set(database=db_name).render_as_string(hide_password=False)

    asyncio.run(_run_admin(base_url, f'CREATE DATABASE "{db_name}"'))

    env = {
        "ENVIRONMENT": "test",
        "DATABASE_URL": db_url,
        "REDIS_URL": redis_url,
        "JWT_SECRET": TEST_JWT_SECRET,
        "CORS_ORIGINS": TEST_ORIGIN,
    }
    previous = {key: os.environ.get(key) for key in env}
    os.environ.update(env)

    from searchos.core.config import get_settings

    get_settings.cache_clear()

    redis.Redis.from_url(redis_url).flushdb()

    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    command.upgrade(cfg, "head")

    yield env

    get_settings.cache_clear()
    for key, value in previous.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    asyncio.run(_run_admin(base_url, f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)'))


@pytest_asyncio.fixture(scope="session")
async def _engine_lifecycle(test_env: dict[str, str]) -> AsyncIterator[None]:
    yield
    from searchos.core.db import dispose_engine

    await dispose_engine()


@pytest_asyncio.fixture(autouse=True)
async def _clean_db(test_env: dict[str, str], _engine_lifecycle: None) -> AsyncIterator[None]:
    yield
    from searchos.core.db import get_engine

    async with get_engine().begin() as conn:
        await conn.execute(
            text("TRUNCATE jobs, sites, memberships, refresh_tokens, users, orgs RESTART IDENTITY CASCADE")
        )


@pytest_asyncio.fixture
async def client(test_env: dict[str, str]) -> AsyncIterator[AsyncClient]:
    from searchos.main import create_app

    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://testserver") as http:
        yield http


@pytest_asyncio.fixture
async def session(test_env: dict[str, str]) -> AsyncIterator[AsyncSession]:
    from searchos.core.db import get_sessionmaker

    async with get_sessionmaker()() as s:
        yield s


@pytest.fixture
def google_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Google OAuth client configured (the app otherwise reports "Google isn't set up yet")."""
    from searchos.core.config import get_settings

    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id.apps.googleusercontent.com")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setenv("WEB_BASE_URL", "http://web.test")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# ---- helpers ----------------------------------------------------------------------------------------
async def register(
    client: AsyncClient, email: str, org_name: str = "Acme Agency", password: str = PASSWORD
) -> dict[str, Any]:
    resp = await client.post(
        "/auth/register", json={"email": email, "password": password, "org_name": org_name}
    )
    assert resp.status_code == 201, resp.text
    data: dict[str, Any] = resp.json()
    return data


def auth(tokens: dict[str, Any], org_id: str | None = None) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    if org_id:
        headers["X-Org-Id"] = org_id
    return headers


# ---- fixture website --------------------------------------------------------------------------------
@pytest.fixture(scope="session")
def fixture_site() -> Iterator[Any]:
    """The static test website from `fixtures/site/`, served on a random local port."""
    from searchos.devtools.fixture_server import FixtureServer

    with FixtureServer() as server:
        yield server
