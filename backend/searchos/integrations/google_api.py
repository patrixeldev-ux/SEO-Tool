"""Authorised calls to Google APIs for one stored connection.

- Adds the bearer token; refreshes it when it is about to expire, or once on a 401.
- `invalid_grant` on refresh marks the connection `needs_reauth` and raises `NeedsReauthError`.
- Retries 429 and 5xx with exponential backoff (honouring Retry-After), then gives up with `ProviderApiError`.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from searchos.core.config import Settings, get_settings
from searchos.core.crypto import DecryptionError, decrypt_json, encrypt_json
from searchos.integrations import google_oauth
from searchos.integrations.errors import IntegrationError, NeedsReauthError, ProviderApiError
from searchos.models import ConnectionStatus, IntegrationConnection

log = logging.getLogger(__name__)

SITES_URL = "https://www.googleapis.com/webmasters/v3/sites"
MAX_ATTEMPTS = 6
BASE_DELAY = 1.0
MAX_DELAY = 60.0

Sleep = Callable[[float], Awaitable[None]]


def api_error_message(response: httpx.Response) -> str:
    try:
        body = response.json()
        message = body["error"]["message"] if isinstance(body.get("error"), dict) else body.get("error")
    except (ValueError, KeyError, AttributeError, TypeError):
        message = None
    text = str(message or response.text[:200] or f"HTTP {response.status_code}")
    return f"Google answered {response.status_code}: {text}"


def retry_delay(attempt: int, response: httpx.Response | None) -> float:
    """Seconds before retry number `attempt` (1-based): Retry-After if given, else 1, 2, 4, ... capped."""
    if response is not None:
        header = response.headers.get("Retry-After")
        if header and header.isdigit():
            return min(float(header), MAX_DELAY)
    return float(min(BASE_DELAY * 2 ** (attempt - 1), MAX_DELAY))


class GoogleSession:
    """Token-managing HTTP session for one `integration_connections` row (provider = google)."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        connection_id: uuid.UUID,
        *,
        settings: Settings | None = None,
        sleep: Sleep = asyncio.sleep,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self._sessions = sessions
        self._connection_id = connection_id
        self._settings = settings or get_settings()
        self._sleep = sleep
        self._http = http or httpx.AsyncClient(timeout=60.0)
        self._owns_http = http is None
        self._tokens: google_oauth.TokenSet | None = None
        self._lock = asyncio.Lock()

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    async def __aenter__(self) -> GoogleSession:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    # ---- tokens -------------------------------------------------------------------------------
    async def _load_tokens(self) -> google_oauth.TokenSet:
        async with self._sessions() as session:
            conn = await session.get(IntegrationConnection, self._connection_id)
            if conn is None or not conn.credentials_encrypted:
                raise NeedsReauthError("Google is not connected for this site.")
            if conn.status is ConnectionStatus.needs_reauth:
                raise NeedsReauthError("Google needs to be connected again.")
            try:
                data = decrypt_json(conn.credentials_encrypted, self._settings)
            except DecryptionError:
                raise NeedsReauthError(
                    "The stored Google connection can't be read (the encryption key changed). Connect again."
                ) from None
        return google_oauth.TokenSet.from_dict(data)

    async def _refresh(self, stale: google_oauth.TokenSet) -> google_oauth.TokenSet:
        async with self._lock:
            # Another task may have refreshed while we waited for the lock.
            if self._tokens is not None and self._tokens.access_token != stale.access_token:
                return self._tokens
            if not stale.refresh_token:
                await self._mark(ConnectionStatus.needs_reauth, "Google did not give a refresh token.")
                raise NeedsReauthError("Google did not give a refresh token. Please connect Google again.")
            try:
                fresh = await google_oauth.refresh_tokens(
                    self._settings, stale.refresh_token, client=self._http
                )
            except NeedsReauthError as exc:
                await self._mark(ConnectionStatus.needs_reauth, str(exc))
                raise
            self._tokens = fresh
            async with self._sessions() as session:
                await session.execute(
                    sa.update(IntegrationConnection)
                    .where(IntegrationConnection.id == self._connection_id)
                    .values(credentials_encrypted=encrypt_json(fresh.to_dict(), self._settings))
                )
                await session.commit()
            return fresh

    async def _mark(self, status: ConnectionStatus, error: str | None) -> None:
        async with self._sessions() as session:
            await session.execute(
                sa.update(IntegrationConnection)
                .where(IntegrationConnection.id == self._connection_id)
                .values(status=status, last_error=error)
            )
            await session.commit()

    async def _access_token(self) -> google_oauth.TokenSet:
        if self._tokens is None:
            self._tokens = await self._load_tokens()
        if self._tokens.expiring():
            return await self._refresh(self._tokens)
        return self._tokens

    # ---- requests -----------------------------------------------------------------------------
    async def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        """Returns a successful (2xx) response or raises `ProviderApiError` / `NeedsReauthError`."""
        refreshed_on_401 = False
        attempt = 0
        extra_headers: dict[str, str] = kwargs.pop("headers", {})
        while True:
            attempt += 1
            tokens = await self._access_token()
            headers = {**extra_headers, "Authorization": f"Bearer {tokens.access_token}"}
            try:
                response = await self._http.request(method, url, headers=headers, **kwargs)
            except httpx.TransportError as exc:
                if attempt >= MAX_ATTEMPTS:
                    raise ProviderApiError(0, f"Could not reach Google: {type(exc).__name__}") from exc
                await self._sleep(retry_delay(attempt, None))
                continue
            if response.is_success:
                return response
            if response.status_code == 401 and not refreshed_on_401:
                refreshed_on_401 = True
                attempt -= 1
                await self._refresh(tokens)
                continue
            if response.status_code in (429, 500, 502, 503, 504) and attempt < MAX_ATTEMPTS:
                await self._sleep(retry_delay(attempt, response))
                continue
            raise ProviderApiError(response.status_code, api_error_message(response))

    async def get_json(self, url: str, **kwargs: Any) -> Any:
        return (await self.request("GET", url, **kwargs)).json()

    async def post_json(self, url: str, body: dict[str, Any], **kwargs: Any) -> Any:
        return (await self.request("POST", url, json=body, **kwargs)).json()


# ---- Search Console property list ----------------------------------------------------------------
USABLE_LEVELS = frozenset({"siteOwner", "siteFullUser", "siteRestrictedUser"})


async def list_properties(session: GoogleSession) -> list[dict[str, str]]:
    body = await session.get_json(SITES_URL)
    return [
        {"site_url": str(e["siteUrl"]), "permission_level": str(e.get("permissionLevel", ""))}
        for e in body.get("siteEntry", [])
        if "siteUrl" in e
    ]


def match_property(domain: str, uses_www: bool, site_urls: list[str]) -> str | None:
    """The property that best matches a site's domain: the domain property first, then the exact https host
    the owner uses, then the other host spelling, then http."""
    hosts = [f"www.{domain}", domain] if uses_www else [domain, f"www.{domain}"]
    candidates = [f"sc-domain:{domain}"]
    candidates += [f"https://{h}/" for h in hosts]
    candidates += [f"http://{h}/" for h in hosts]
    available = set(site_urls)
    for candidate in candidates:
        if candidate in available:
            return candidate
    return None


__all__ = [
    "GoogleSession",
    "IntegrationError",
    "list_properties",
    "match_property",
    "retry_delay",
]
