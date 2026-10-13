"""Cloudflare deployer: the Worker in `edge/` rewrites pages using rules it fetches from `GET /edge/rules/{token}`.

The API does not need a Cloudflare API token at all. Applying a deployment means "this change is now part of the
site's rule set"; the rule set is rebuilt from every live deployment each time the Worker asks (see api/edge.py),
so rolling back is simply marking the deployment reverted. The Worker caches rules for 5 minutes.

The owner's one-time setup (docs/DEPLOY_CLOUDFLARE.md) gives the Worker two values it must hold: the site token
(in the URL) and the signing secret (a Wrangler secret). The connection only counts as `connected` once the
Worker has been seen answering on the site (the `x-searchos-edge` response header).
"""

from __future__ import annotations

import time
import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from searchos.deploy.base import ConnectionTest, DeployContext, DeployerError, ItemResult
from searchos.deploy.signing import build_rules, normalise_path
from searchos.models import (
    ConnectionStatus,
    Deployment,
    DeploymentStatus,
    DeployMethod,
    IntegrationConnection,
    Provider,
    Site,
)

EDGE_HEADER = "x-searchos-edge"


def ops_of(deployments: list[Deployment]) -> list[dict[str, Any]]:
    """Operations of several deployments of one page, oldest first; a later `title` replaces an earlier one
    because the Worker applies them in order."""
    return [
        op
        for d in sorted(deployments, key=lambda x: (x.created_at, str(x.id)))
        for op in d.payload.get("ops", [])
    ]


def rules_body(
    site: Site,
    conn: IntegrationConnection,
    live: dict[str, list[Deployment]],
    *,
    now: int | None = None,
) -> str:
    """The unsigned JSON body the Worker receives for a site."""
    pages: dict[str, list[dict[str, Any]]] = {}
    for url, deployments in live.items():
        ops = ops_of(deployments)
        if ops:
            pages.setdefault(normalise_path(url), []).extend(ops)
    ts = int(time.time()) if now is None else now
    return build_rules(
        host=site.domain,
        version=ts,
        issued_at=ts,
        enabled=not bool(conn.meta.get("kill_switch")),
        pages=pages,
    )


async def find_connection(session: AsyncSession, site_token: str) -> IntegrationConnection | None:
    return await session.scalar(
        sa.select(IntegrationConnection).where(
            IntegrationConnection.provider == Provider.cloudflare,
            IntegrationConnection.meta["site_token"].astext == site_token,
            IntegrationConnection.status.in_((ConnectionStatus.connected, ConnectionStatus.pending)),
        )
    )


class CloudflareDeployer:
    method = DeployMethod.cloudflare

    @staticmethod
    def _check(ctx: DeployContext) -> None:
        if not ctx.credentials.get("signing_secret") or not ctx.meta.get("site_token"):
            raise DeployerError("The Cloudflare connection is incomplete; connect it again.")

    async def apply(self, ctx: DeployContext, items: list[Deployment]) -> dict[uuid.UUID, ItemResult]:
        self._check(ctx)
        return {
            d.id: ItemResult(True, DeploymentStatus.applied, meta={"rule_version": ctx.version})
            for d in items
        }

    async def rollback(self, ctx: DeployContext, items: list[Deployment]) -> dict[uuid.UUID, ItemResult]:
        self._check(ctx)
        return {d.id: ItemResult(True, DeploymentStatus.reverted) for d in items}

    async def test_connection(self, ctx: DeployContext) -> ConnectionTest:
        """Is our Worker answering on the site? Looks for its marker header on the home page."""
        host = f"www.{ctx.site.domain}" if ctx.site.uses_www else ctx.site.domain
        resp = await ctx.fetcher.get(f"https://{host}/", no_cache=True)
        if resp.error or resp.status is None:
            return ConnectionTest(False, f"Could not open your site ({resp.error or 'no answer'}).")
        mark = resp.headers.get(EDGE_HEADER)
        if mark is None:
            return ConnectionTest(
                False,
                "Your site answered, but the SearchOS Worker was not seen on it. Check that the route "
                f"{ctx.site.domain}/* points at the Worker (docs/DEPLOY_CLOUDFLARE.md, step 5).",
                {"http_status": resp.status},
            )
        if mark == "off":
            return ConnectionTest(
                True,
                "The Worker is installed, but it is switched off (SEARCHOS_DISABLED). Pages pass through unchanged.",
                {"worker_state": "off"},
            )
        return ConnectionTest(True, "The SearchOS Worker is installed and answering.", {"worker_state": mark})
