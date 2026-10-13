"""Phase 4 test fakes: a pretend website, an edge-style fake deployer, and quick ways to create approved actions."""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from searchos.core.crypto import encrypt_json
from searchos.deploy import htmlpatch, ownership, registry
from searchos.deploy.base import ConnectionTest, DeployContext, ItemResult
from searchos.deploy.livefetch import LiveResponse
from searchos.models import (
    Action,
    ConnectionStatus,
    Deployment,
    DeploymentStatus,
    DeployMethod,
    IntegrationConnection,
    Provider,
    Site,
)
from searchos.planner.catalog import ActionStatus, risk_class_for

URL = "https://shop.example/page"
PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Old title</title>
<meta name="description" content="Old description">
<link rel="canonical" href="https://shop.example/page">
</head>
<body><h1>Coffee</h1>
<p>We sell coffee grinders and fresh beans for home brewing.</p>
<p>Read more about espresso machines in our guide.</p>
<img src="/logo.png"></body></html>
"""


class FakeWebsite:
    """A pretend website. `lag` is how many fetches of a changed page still return the old copy (a CDN that
    has not caught up yet)."""

    def __init__(self, pages: dict[str, str] | None = None, lag: int = 0) -> None:
        self.original = dict(pages if pages is not None else {URL: PAGE})
        self.rules: dict[str, dict[uuid.UUID, list[dict[str, Any]]]] = defaultdict(dict)
        self.lag = lag
        self.fetches: dict[str, int] = defaultdict(int)
        self.fail_status: dict[str, int] = {}

    def render(self, url: str) -> str:
        ops = [op for chunk in self.rules.get(url, {}).values() for op in chunk]
        return htmlpatch.apply_ops(self.original[url], ops, url)

    async def get(self, url: str, *, no_cache: bool = False) -> LiveResponse:
        base = url.split("?")[0]
        if base in self.fail_status:
            return LiveResponse(url=url, status=self.fail_status[base], text="")
        if base not in self.original:
            return LiveResponse(url=url, status=404, text="")
        self.fetches[base] += 1
        if self.rules.get(base) and self.fetches[base] <= self.lag:
            return LiveResponse(url=url, status=200, text=self.original[base])
        return LiveResponse(url=url, status=200, text=self.render(base))


class FakeDeployer:
    """Edge-style deployer: remembers ops per page and changes what the FakeWebsite shows."""

    def __init__(self, site: FakeWebsite, method: DeployMethod = DeployMethod.cloudflare) -> None:
        self.method = method
        self.site = site
        self.applied: list[uuid.UUID] = []
        self.rolled_back: list[uuid.UUID] = []
        self.fail_urls: set[str] = set()
        self.fail_rollback = False

    async def apply(self, ctx: DeployContext, items: list[Deployment]) -> dict[uuid.UUID, ItemResult]:
        out: dict[uuid.UUID, ItemResult] = {}
        for d in items:
            if d.url in self.fail_urls:
                out[d.id] = ItemResult(False, DeploymentStatus.failed, "the fake deployer was told to fail")
                continue
            self.site.rules[d.url][d.id] = list(d.payload["ops"])
            self.applied.append(d.id)
            out[d.id] = ItemResult(True, DeploymentStatus.applied)
        return out

    async def rollback(self, ctx: DeployContext, items: list[Deployment]) -> dict[uuid.UUID, ItemResult]:
        out: dict[uuid.UUID, ItemResult] = {}
        for d in items:
            if self.fail_rollback:
                out[d.id] = ItemResult(False, DeploymentStatus.failed, "the fake rollback failed")
                continue
            self.site.rules[d.url].pop(d.id, None)
            self.rolled_back.append(d.id)
            out[d.id] = ItemResult(True, DeploymentStatus.reverted)
        return out

    async def test_connection(self, ctx: DeployContext) -> ConnectionTest:
        return ConnectionTest(True, "ok")


@pytest.fixture
def fake_registry() -> Iterator[Any]:
    """Empties the deployer registry around a test so fakes can be registered."""
    saved = dict(registry._DEPLOYERS)
    registry._DEPLOYERS.clear()
    yield registry
    registry._DEPLOYERS.clear()
    registry._DEPLOYERS.update(saved)


async def verify_site(session: AsyncSession, site_id: uuid.UUID | str, now: datetime | None = None) -> Site:
    site = await session.get(Site, uuid.UUID(str(site_id)))
    assert site is not None
    now = now or datetime.now(UTC)
    ownership.ensure_token(site)
    site.verified_at = now
    site.verify_checked_at = now
    site.verify_method = "dns"
    await session.commit()
    return site


async def connect(
    session: AsyncSession,
    site: Site,
    provider: Provider = Provider.cloudflare,
    *,
    creds: dict[str, Any] | None = None,
    meta: dict[str, Any] | None = None,
) -> IntegrationConnection:
    conn = IntegrationConnection(
        org_id=site.org_id,
        site_id=site.id,
        provider=provider,
        status=ConnectionStatus.connected,
        credentials_encrypted=encrypt_json(creds or {"api_token": "tok-1234567890abcd"}),
        meta=meta or {},
    )
    session.add(conn)
    await session.commit()
    return conn


DEFAULT_FIELDS = {
    "rewrite_title": "title",
    "rewrite_meta": "meta_description",
    "fix_canonical": "canonical",
    "add_alt": "alt:https://shop.example/logo.png",
    "add_jsonld": "jsonld:Organization",
    "fix_jsonld": "jsonld:Organization",
    "add_internal_links": "internal_links",
}


async def make_action(
    session: AsyncSession,
    site: Site,
    action_type: str = "rewrite_title",
    *,
    url: str = URL,
    field: str | None = None,
    current: Any = "Old title",
    proposed: Any = "A much better title for the coffee page",
    status: ActionStatus = ActionStatus.approved,
    priority: float = 1.0,
) -> Action:
    action = Action(
        org_id=site.org_id,
        site_id=site.id,
        type=action_type,
        risk_class=risk_class_for(action_type),
        target_url=url,
        field=field or DEFAULT_FIELDS.get(action_type, action_type),
        current_value=current,
        proposed_value=proposed,
        effort=1,
        priority=priority,
        status=status,
    )
    session.add(action)
    await session.commit()
    return action
