"""Integration endpoints: which providers are configured, per-site connections, Google OAuth.

Tokens and API keys are never returned by any endpoint. Connecting or disconnecting needs the admin role.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Annotated, Any, Literal
from urllib.parse import urlencode

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict

from searchos.api.schemas import JobOut
from searchos.core.config import Settings
from searchos.core.crypto import DecryptionError, decrypt_json, encrypt_json, encryption_available
from searchos.core.crypto import EncryptionUnavailableError as EncryptionUnavailableError
from searchos.core.db import get_sessionmaker
from searchos.core.deps import OrgContext, SessionDep, SettingsDep, require_role
from searchos.integrations import google_oauth, gsc_sync
from searchos.integrations.errors import (
    IntegrationError,
    NeedsReauthError,
    NotConfiguredError,
    ProviderApiError,
)
from searchos.integrations.google_api import GoogleSession, list_properties, match_property
from searchos.models import (
    ConnectionStatus,
    IntegrationConnection,
    Job,
    Membership,
    Provider,
    Role,
    Site,
    User,
    role_at_least,
)

log = logging.getLogger(__name__)
router = APIRouter(tags=["integrations"])

ReadCtx = Annotated[OrgContext, Depends(require_role(Role.client_viewer))]
AdminCtx = Annotated[OrgContext, Depends(require_role(Role.admin))]


class ConnectionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    provider: Provider
    status: ConnectionStatus
    account_label: str | None
    property_url: str | None
    last_sync_at: datetime | None
    last_error: str | None
    meta: dict[str, Any]


class PsiOut(BaseModel):
    enabled: bool
    key_configured: bool


class SiteIntegrationsOut(BaseModel):
    google: ConnectionOut | None
    bing: ConnectionOut | None
    psi: PsiOut


class ProviderStatus(BaseModel):
    configured: bool
    message: str


class IntegrationStatusOut(BaseModel):
    google: ProviderStatus
    encryption: ProviderStatus
    psi: PsiOut
    redirect_uri: str
    setup_guide: str = "docs/GOOGLE_SETUP.md"


class ConnectOut(BaseModel):
    authorization_url: str


class PropertyOut(BaseModel):
    site_url: str
    permission_level: str
    usable: bool


class PropertiesOut(BaseModel):
    properties: list[PropertyOut]
    suggested: str | None
    selected: str | None


class PropertyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    site_url: str


def integration_status(settings: Settings) -> IntegrationStatusOut:
    google_ok = settings.google_configured
    enc_ok = encryption_available(settings)
    return IntegrationStatusOut(
        google=ProviderStatus(
            configured=google_ok and enc_ok,
            message="Google is ready to connect."
            if google_ok and enc_ok
            else "Google isn't set up yet."
            if not google_ok
            else "TOKEN_ENCRYPTION_KEY is missing, so connections can't be stored safely.",
        ),
        encryption=ProviderStatus(
            configured=enc_ok,
            message="Credentials are stored encrypted."
            if enc_ok
            else "TOKEN_ENCRYPTION_KEY is not set; credentials can't be stored.",
        ),
        psi=PsiOut(enabled=settings.psi_enabled, key_configured=settings.psi_api_key is not None),
        redirect_uri=settings.google_redirect_uri,
    )


@router.get("/integrations/status", response_model=IntegrationStatusOut)
async def get_integration_status(settings: SettingsDep) -> IntegrationStatusOut:
    """Which integrations the server is set up for. Public and boolean-only: it reveals no secret."""
    return integration_status(settings)


async def _site(ctx: OrgContext, site_id: uuid.UUID) -> Site:
    return await ctx.get_or_404(Site, site_id, "Site")


async def get_connection(
    ctx: OrgContext, site_id: uuid.UUID, provider: Provider
) -> IntegrationConnection | None:
    return await ctx.session.scalar(
        ctx.select(IntegrationConnection).where(
            IntegrationConnection.site_id == site_id, IntegrationConnection.provider == provider
        )
    )


@router.get("/sites/{site_id}/integrations", response_model=SiteIntegrationsOut)
async def get_site_integrations(
    site_id: uuid.UUID, ctx: ReadCtx, settings: SettingsDep
) -> SiteIntegrationsOut:
    await _site(ctx, site_id)
    rows = (
        await ctx.session.scalars(
            ctx.select(IntegrationConnection).where(IntegrationConnection.site_id == site_id)
        )
    ).all()
    by_provider = {row.provider: row for row in rows}

    def out(provider: Provider) -> ConnectionOut | None:
        row = by_provider.get(provider)
        return ConnectionOut.model_validate(row) if row else None

    return SiteIntegrationsOut(
        google=out(Provider.google),
        bing=out(Provider.bing),
        psi=PsiOut(enabled=settings.psi_enabled, key_configured=settings.psi_api_key is not None),
    )


# ---- Google: connect ----------------------------------------------------------------------------
def _require_google(settings: Settings) -> None:
    if not settings.google_configured:
        raise HTTPException(status.HTTP_409_CONFLICT, "Google isn't set up yet. See docs/GOOGLE_SETUP.md.")
    if not encryption_available(settings):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "TOKEN_ENCRYPTION_KEY is not set, so Google tokens can't be stored safely.",
        )


@router.post("/sites/{site_id}/integrations/google/connect", response_model=ConnectOut)
async def start_google_connect(site_id: uuid.UUID, ctx: AdminCtx, settings: SettingsDep) -> ConnectOut:
    """Returns the Google consent-screen URL; the browser navigates there and comes back via the callback."""
    _require_google(settings)
    await _site(ctx, site_id)
    verifier, challenge = google_oauth.make_pkce()
    state = google_oauth.build_state(ctx.user.id, site_id, ctx.org_id, verifier, settings)
    return ConnectOut(authorization_url=google_oauth.authorization_url(settings, state, challenge))


def _web_redirect(settings: Settings, **params: str) -> RedirectResponse:
    base = settings.web_base_url.rstrip("/")
    return RedirectResponse(f"{base}/settings?{urlencode(params)}", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/integrations/google/callback", include_in_schema=False)
async def google_callback(
    session: SessionDep,
    settings: SettingsDep,
    code: Annotated[str | None, Query()] = None,
    state: Annotated[str | None, Query()] = None,
    error: Annotated[str | None, Query()] = None,
) -> RedirectResponse:
    """Google sends the browser here. There is no bearer token on this request: the encrypted `state` created
    by `start_google_connect` is what binds it to a user and site."""
    if not state:
        return _web_redirect(settings, google="error", reason="missing_state")
    try:
        parsed = google_oauth.read_state(state, settings)
    except (DecryptionError, EncryptionUnavailableError):
        return _web_redirect(settings, google="error", reason="state_invalid")
    site_params = {"site": str(parsed.site_id)}
    if error:
        reason = "denied" if error == "access_denied" else "google_error"
        return _web_redirect(settings, google="error", reason=reason, **site_params)
    if not code:
        return _web_redirect(settings, google="error", reason="missing_code", **site_params)

    # Re-check that the user may still do this (membership could have changed during the consent screen).
    user = await session.get(User, parsed.user_id)
    membership = await session.scalar(
        sa.select(Membership).where(Membership.user_id == parsed.user_id, Membership.org_id == parsed.org_id)
    )
    site = await session.scalar(
        sa.select(Site).where(Site.id == parsed.site_id, Site.org_id == parsed.org_id)
    )
    if (
        user is None
        or not user.is_active
        or membership is None
        or not role_at_least(membership.role, Role.admin)
        or site is None
    ):
        return _web_redirect(settings, google="error", reason="not_allowed", **site_params)

    try:
        tokens = await google_oauth.exchange_code(settings, code, parsed.code_verifier)
    except IntegrationError as exc:
        log.warning("google code exchange failed: %s", exc)
        return _web_redirect(settings, google="error", reason="exchange_failed", **site_params)
    if not tokens.refresh_token:
        return _web_redirect(settings, google="error", reason="no_refresh_token", **site_params)

    encrypted = encrypt_json(tokens.to_dict(), settings)
    conn = await session.scalar(
        sa.select(IntegrationConnection).where(
            IntegrationConnection.site_id == site.id, IntegrationConnection.provider == Provider.google
        )
    )
    if conn is None:
        conn = IntegrationConnection(
            org_id=site.org_id, site_id=site.id, provider=Provider.google, created_by=user.id
        )
        session.add(conn)
    conn.credentials_encrypted = encrypted
    conn.last_error = None
    conn.status = ConnectionStatus.connected if conn.property_url else ConnectionStatus.pending
    conn.account_label = "Google account"
    await session.commit()
    return _web_redirect(settings, google="connected", **site_params)


# ---- Google: property picker --------------------------------------------------------------------
async def _google_connection(ctx: OrgContext, site_id: uuid.UUID) -> IntegrationConnection:
    await _site(ctx, site_id)
    conn = await get_connection(ctx, site_id, Provider.google)
    if conn is None or not conn.credentials_encrypted:
        raise HTTPException(status.HTTP_409_CONFLICT, "Google is not connected for this site.")
    return conn


def _provider_http_error(exc: IntegrationError) -> HTTPException:
    if isinstance(exc, NeedsReauthError):
        return HTTPException(status.HTTP_409_CONFLICT, str(exc))
    if isinstance(exc, NotConfiguredError):
        return HTTPException(status.HTTP_409_CONFLICT, str(exc))
    if isinstance(exc, ProviderApiError):
        return HTTPException(status.HTTP_502_BAD_GATEWAY, exc.message)
    return HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc))


@router.get("/sites/{site_id}/integrations/google/properties", response_model=PropertiesOut)
async def google_properties(site_id: uuid.UUID, ctx: AdminCtx) -> PropertiesOut:
    conn = await _google_connection(ctx, site_id)
    site = await _site(ctx, site_id)
    try:
        async with GoogleSession(get_sessionmaker(), conn.id) as google:
            found = await list_properties(google)
    except IntegrationError as exc:
        raise _provider_http_error(exc) from None
    usable = [p["site_url"] for p in found if p["permission_level"] in USABLE]
    return PropertiesOut(
        properties=[
            PropertyOut(
                site_url=p["site_url"],
                permission_level=p["permission_level"],
                usable=p["permission_level"] in USABLE,
            )
            for p in found
        ],
        suggested=match_property(site.domain, site.uses_www, usable),
        selected=conn.property_url,
    )


USABLE = frozenset({"siteOwner", "siteFullUser", "siteRestrictedUser"})


@router.put("/sites/{site_id}/integrations/google/property", response_model=ConnectionOut)
async def select_google_property(
    site_id: uuid.UUID, body: PropertyIn, ctx: AdminCtx
) -> IntegrationConnection:
    conn = await _google_connection(ctx, site_id)
    try:
        async with GoogleSession(get_sessionmaker(), conn.id) as google:
            found = await list_properties(google)
    except IntegrationError as exc:
        raise _provider_http_error(exc) from None
    chosen = next((p for p in found if p["site_url"] == body.site_url), None)
    if chosen is None or chosen["permission_level"] not in USABLE:
        raise HTTPException(
            422,
            "That property isn't available to the connected Google account.",
        )
    await ctx.session.refresh(conn)  # the property call may have refreshed the stored token
    changed = conn.property_url != chosen["site_url"]
    conn.property_url = chosen["site_url"]
    conn.status = ConnectionStatus.connected
    conn.last_error = None
    await ctx.session.commit()
    if changed or "last_backfill_job_id" not in conn.meta:
        # First data pull: 16 months, newest days first. Safe to call again: only one sync runs per site.
        await gsc_sync.start_sync(ctx.session, conn, "backfill", ctx.user.id)
        await ctx.session.refresh(conn)
    return conn


class SyncIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["backfill", "incremental"] = "incremental"


@router.post(
    "/sites/{site_id}/integrations/google/sync", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED
)
async def sync_google_now(site_id: uuid.UUID, body: SyncIn, ctx: AdminCtx) -> Job:
    """Fetch new Search Console data now (or re-run the 16-month backfill to fill any gaps)."""
    conn = await _google_connection(ctx, site_id)
    if conn.status is not ConnectionStatus.connected or not conn.property_url:
        raise HTTPException(status.HTTP_409_CONFLICT, "Choose a Search Console property first.")
    job = await gsc_sync.start_sync(ctx.session, conn, body.mode, ctx.user.id)
    if job is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Google is not ready to sync.")
    return job


@router.delete("/sites/{site_id}/integrations/google", status_code=status.HTTP_204_NO_CONTENT)
async def disconnect_google(site_id: uuid.UUID, ctx: AdminCtx, settings: SettingsDep) -> Response:
    """Deletes the stored tokens (after asking Google to revoke them). Already imported data is kept."""
    await _site(ctx, site_id)
    conn = await get_connection(ctx, site_id, Provider.google)
    if conn is not None:
        if conn.credentials_encrypted:
            try:
                tokens = google_oauth.TokenSet.from_dict(decrypt_json(conn.credentials_encrypted, settings))
                await google_oauth.revoke_token(tokens.refresh_token or tokens.access_token)
            except (DecryptionError, EncryptionUnavailableError):
                pass  # nothing we can revoke; the stored copy is deleted below regardless
        await ctx.session.delete(conn)
        await ctx.session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
