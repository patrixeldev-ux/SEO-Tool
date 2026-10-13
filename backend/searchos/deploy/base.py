"""The contract between the deploy service and the three deployers (Cloudflare, WordPress, GitHub)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol

from searchos.core.config import Settings
from searchos.deploy.livefetch import PageFetcher
from searchos.models import Deployment, DeploymentStatus, DeployMethod, IntegrationConnection, Site


class DeployerError(Exception):
    """A deployer could not do what was asked. `str(error)` is safe to show (never contains a credential)."""


@dataclass(slots=True)
class DeployContext:
    site: Site
    connection: IntegrationConnection
    credentials: dict[str, Any]  # decrypted; never logged, never returned by an endpoint
    settings: Settings
    fetcher: PageFetcher
    # Deployments of this site that are live right now, per page URL (the Worker rebuilds a page's whole rule set).
    live: dict[str, list[Deployment]] = field(default_factory=dict)
    batch_id: uuid.UUID | None = None
    version: int = 0  # rule version / commit marker for this run

    @property
    def meta(self) -> dict[str, Any]:
        return self.connection.meta


@dataclass(slots=True)
class ItemResult:
    ok: bool
    status: DeploymentStatus
    error: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ConnectionTest:
    ok: bool
    message: str
    details: dict[str, Any] = field(default_factory=dict)


class Deployer(Protocol):
    method: DeployMethod

    async def apply(self, ctx: DeployContext, items: list[Deployment]) -> dict[uuid.UUID, ItemResult]:
        """Put the changes live (or open a pull request). Keys of the result are deployment ids."""
        ...

    async def rollback(self, ctx: DeployContext, items: list[Deployment]) -> dict[uuid.UUID, ItemResult]:
        """Undo the changes. `ctx.live` already excludes `items`."""
        ...

    async def test_connection(self, ctx: DeployContext) -> ConnectionTest: ...
