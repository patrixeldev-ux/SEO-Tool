"""Connecting a site to a deployer: stored (encrypted) credentials, a connection test, and the public view.

Every deployer keeps its secrets in `credentials_encrypted` and only non-secret facts in `meta`, so `meta` can be
shown to the owner as is. A connection becomes `connected` only when its test passes; deploying uses
`connected` connections only.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from searchos.core.config import get_settings
from searchos.core.crypto import encrypt_json
from searchos.deploy.base import ConnectionTest, DeployContext, DeployerError
from searchos.deploy.livefetch import PageFetcher, default_fetcher
from searchos.deploy.registry import get_deployer
from searchos.deploy.service import DEPLOY_PROVIDERS, DeployError, credentials_of
from searchos.models import (
    ConnectionStatus,
    DeployMethod,
    IntegrationConnection,
    Provider,
    Site,
)

log = logging.getLogger(__name__)


async def get_connection(
    session: AsyncSession, site_id: uuid.UUID, method: DeployMethod
) -> IntegrationConnection | None:
    return await session.scalar(
        sa.select(IntegrationConnection).where(
            IntegrationConnection.site_id == site_id,
            IntegrationConnection.provider == Provider(method.value),
        )
    )


async def list_connections(session: AsyncSession, site_id: uuid.UUID) -> list[IntegrationConnection]:
    return list(
        (
            await session.scalars(
                sa.select(IntegrationConnection).where(
                    IntegrationConnection.site_id == site_id,
                    IntegrationConnection.provider.in_(DEPLOY_PROVIDERS),
                )
            )
        ).all()
    )


async def save_connection(
    session: AsyncSession,
    site: Site,
    method: DeployMethod,
    *,
    user_id: uuid.UUID | None,
    credentials: dict[str, Any],
    meta: dict[str, Any],
    label: str | None = None,
) -> IntegrationConnection:
    """Create or replace the connection of one method. It starts `pending` until the test passes."""
    conn = await get_connection(session, site.id, method)
    if conn is None:
        conn = IntegrationConnection(
            org_id=site.org_id, site_id=site.id, provider=Provider(method.value), created_by=user_id
        )
        session.add(conn)
    conn.credentials_encrypted = encrypt_json(credentials)
    conn.meta = meta
    conn.account_label = label
    conn.status = ConnectionStatus.pending
    conn.last_error = None
    await session.flush()
    return conn


async def run_test(
    session: AsyncSession,
    site: Site,
    conn: IntegrationConnection,
    fetcher: PageFetcher | None = None,
) -> ConnectionTest:
    """Test the connection; on success it becomes `connected`, otherwise it keeps (or gets) the error."""
    method = DeployMethod(conn.provider.value)
    try:
        ctx = DeployContext(
            site=site,
            connection=conn,
            credentials=credentials_of(conn),
            settings=get_settings(),
            fetcher=fetcher or default_fetcher(),
        )
        result = await get_deployer(method).test_connection(ctx)
    except (DeployerError, DeployError) as exc:
        result = ConnectionTest(False, str(exc)[:300])
    except Exception as exc:
        log.exception("connection test for %s crashed", method.value)
        result = ConnectionTest(False, f"The test failed unexpectedly ({type(exc).__name__}).")
    conn.last_sync_at = datetime.now(UTC)
    if result.ok:
        conn.status = ConnectionStatus.connected
        conn.last_error = None
        conn.meta = {**conn.meta, **{k: v for k, v in result.details.items() if k in _SAFE_DETAILS}}
    else:
        conn.status = ConnectionStatus.error if conn.status is ConnectionStatus.connected else conn.status
        conn.last_error = result.message[:500]
    await session.flush()
    return result


_SAFE_DETAILS = {"worker_state", "wp_version", "plugin_version", "default_branch", "repo", "user"}


async def remove_connection(session: AsyncSession, site: Site, conn: IntegrationConnection) -> None:
    await session.delete(conn)
    if site.deploy_method == conn.provider.value:
        site.deploy_method = None
    await session.flush()
