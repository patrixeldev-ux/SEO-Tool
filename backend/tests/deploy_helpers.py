"""Shared helpers for the Phase 4 tests: a signed-in owner with a site, and fake ownership checkers."""

from __future__ import annotations

import uuid
from typing import Any

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from searchos.deploy import ownership
from searchos.deploy.livefetch import LiveResponse
from searchos.models import Membership, Role
from tests.conftest import auth, register


class FakeResolver:
    def __init__(self, records: dict[str, list[str]] | None = None) -> None:
        self.records = records or {}

    async def txt(self, name: str) -> list[str]:
        return list(self.records.get(name, []))


class FakeFetcher:
    """Serves canned pages: {url: (status, text)}; anything else is a 404."""

    def __init__(self, pages: dict[str, tuple[int, str]] | None = None) -> None:
        self.pages = pages or {}
        self.calls: list[str] = []

    async def get(self, url: str, *, no_cache: bool = False) -> LiveResponse:
        self.calls.append(url)
        base = url.split("?")[0] if no_cache else url
        status, text = self.pages.get(base, (404, ""))
        return LiveResponse(url=url, status=status, text=text)


def checkers(resolver: FakeResolver | None = None, fetcher: FakeFetcher | None = None) -> ownership.Checkers:
    return ownership.Checkers(resolver or FakeResolver(), fetcher or FakeFetcher())  # type: ignore[arg-type]


async def owner_with_site(
    client: AsyncClient, domain: str = "shop.example", email: str = "owner@example.com"
) -> tuple[dict[str, str], str, dict[str, Any]]:
    """(headers, site_id, tokens) for a fresh organisation that owns one site."""
    tokens = await register(client, email)
    headers = auth(tokens)
    resp = await client.post("/sites", json={"domain": domain, "display_name": "Shop"}, headers=headers)
    assert resp.status_code == 201, resp.text
    return headers, resp.json()["id"], tokens


async def add_member(
    client: AsyncClient, session: AsyncSession, org_id: str, email: str, role: Role
) -> dict[str, str]:
    tokens = await register(client, email)
    session.add(Membership(org_id=uuid.UUID(org_id), user_id=uuid.UUID(tokens["user"]["id"]), role=role))
    await session.commit()
    return auth(tokens, org_id)
