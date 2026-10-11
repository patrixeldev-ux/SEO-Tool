from __future__ import annotations

from importlib import metadata

import redis.asyncio as aioredis
import sqlalchemy as sa
from fastapi import APIRouter, HTTPException, status

from searchos.core.deps import SessionDep, SettingsDep

router = APIRouter(tags=["meta"])


def package_version() -> str:
    try:
        return metadata.version("searchos")
    except metadata.PackageNotFoundError:  # pragma: no cover - running from a bare checkout
        return "0.0.0"


@router.get("/health")
async def health() -> dict[str, str]:
    """Liveness: the process is up. Does not touch dependencies."""
    return {"status": "ok"}


@router.get("/health/ready")
async def ready(session: SessionDep, settings: SettingsDep) -> dict[str, str]:
    """Readiness: Postgres and Redis both answer."""
    try:
        await session.execute(sa.text("SELECT 1"))
        client = aioredis.from_url(settings.redis_url)
        try:
            await client.ping()
        finally:
            await client.aclose()
    except Exception as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "dependency unavailable") from exc
    return {"status": "ready"}


@router.get("/version")
async def version(settings: SettingsDep) -> dict[str, str | None]:
    return {
        "name": "searchos",
        "version": package_version(),
        "environment": settings.environment,
        "git_sha": settings.git_sha,
    }
