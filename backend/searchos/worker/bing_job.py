"""The Bing Webmaster sync job: fetch query and page statistics and store them."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import httpx
import sqlalchemy as sa

from searchos.core.crypto import DecryptionError, decrypt_json
from searchos.integrations.bing import sync_site
from searchos.integrations.errors import ProviderApiError
from searchos.models import ConnectionStatus, IntegrationConnection
from searchos.worker.jobs import JobContext

REJECTED = frozenset({400, 401, 403})  # Bing answers these for an invalid or revoked API key


async def bing_sync_handler(ctx: JobContext) -> dict[str, Any]:
    connection_id = uuid.UUID(str(ctx.payload["connection_id"]))
    async with ctx.sessions() as session:
        conn = await session.get(IntegrationConnection, connection_id)
        if conn is None or not conn.credentials_encrypted or not conn.property_url:
            raise RuntimeError("The Bing connection is gone or incomplete.")
        site_id, site_url, token = conn.site_id, conn.property_url, conn.credentials_encrypted
    try:
        api_key = str(decrypt_json(token)["api_key"])
    except (DecryptionError, KeyError):
        await _record(
            ctx,
            connection_id,
            ConnectionStatus.needs_reauth,
            "The stored Bing key can't be read. Enter it again.",
        )
        raise RuntimeError("The stored Bing key can't be read. Enter it again.") from None
    await ctx.set_progress(10)
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            result = await sync_site(ctx.sessions, client, site_id, site_url, api_key)
    except ProviderApiError as exc:
        status = ConnectionStatus.needs_reauth if exc.status in REJECTED else ConnectionStatus.connected
        await _record(ctx, connection_id, status, exc.message)
        raise
    await _record(ctx, connection_id, ConnectionStatus.connected, None, synced=True)
    return result


async def _record(
    ctx: JobContext,
    connection_id: uuid.UUID,
    status: ConnectionStatus,
    error: str | None,
    *,
    synced: bool = False,
) -> None:
    values: dict[str, Any] = {"status": status, "last_error": error}
    if synced:
        values["last_sync_at"] = datetime.now(UTC)
    async with ctx.sessions() as session:
        await session.execute(
            sa.update(IntegrationConnection).where(IntegrationConnection.id == connection_id).values(**values)
        )
        await session.commit()
