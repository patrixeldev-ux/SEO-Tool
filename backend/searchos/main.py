from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from searchos.api import (
    actions,
    ai_reports,
    ai_settings,
    audits,
    auth,
    deploy,
    edge,
    health,
    integrations,
    jobs,
    orgs,
    search,
    sites,
)
from searchos.core.config import get_settings
from searchos.core.db import dispose_engine


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    yield
    await dispose_engine()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Search OS API",
        version=health.package_version(),
        lifespan=lifespan,
        docs_url=None if settings.is_production else "/docs",
        redoc_url=None,
        openapi_url=None if settings.is_production else "/openapi.json",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,  # exact origins only; credentials are allowed
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Org-Id", "X-Client-Type"],
        max_age=600,
    )
    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(orgs.router)
    app.include_router(sites.router)
    app.include_router(audits.router)
    app.include_router(integrations.router)
    app.include_router(search.router)
    app.include_router(ai_reports.router)
    app.include_router(actions.router)
    app.include_router(ai_settings.router)
    app.include_router(deploy.router)
    app.include_router(edge.router)
    app.include_router(jobs.router)
    if not settings.is_production:
        app.include_router(jobs.dev_router)
    return app
