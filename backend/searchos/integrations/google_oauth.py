"""Google OAuth 2.0 authorization-code flow with PKCE, over plain httpx.

Scope is read-only Search Console access. `access_type=offline` + `prompt=consent` make Google return a
refresh token every time, so a re-connect always yields a usable one.

The `state` parameter is a Fernet token (authenticated + encrypted, valid for 10 minutes) holding the user,
the site and the PKCE verifier. Encrypting it matters: the verifier must not travel readable in the URL.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import time
import uuid
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx

from searchos.core.config import Settings
from searchos.core.crypto import DecryptionError, decrypt_json, encrypt_json
from searchos.integrations.errors import NeedsReauthError, NotConfiguredError, ProviderApiError

SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105  (a URL, not a secret)
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
STATE_TTL_SECONDS = 600
HTTP_TIMEOUT = 30.0


@dataclass(frozen=True, slots=True)
class TokenSet:
    access_token: str
    refresh_token: str | None
    expires_at: float  # unix seconds
    scope: str = SCOPE

    def to_dict(self) -> dict[str, Any]:
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_at": self.expires_at,
            "scope": self.scope,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TokenSet:
        return cls(
            access_token=str(data.get("access_token") or ""),
            refresh_token=data.get("refresh_token"),
            expires_at=float(data.get("expires_at") or 0),
            scope=str(data.get("scope") or SCOPE),
        )

    def expiring(self, margin: float = 60.0) -> bool:
        return time.time() >= self.expires_at - margin


@dataclass(frozen=True, slots=True)
class OAuthState:
    user_id: uuid.UUID
    site_id: uuid.UUID
    org_id: uuid.UUID
    code_verifier: str


def client_credentials(settings: Settings) -> tuple[str, str]:
    if not settings.google_client_id or settings.google_client_secret is None:
        raise NotConfiguredError(
            "Google isn't set up yet (GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET are missing)."
        )
    return settings.google_client_id, settings.google_client_secret.get_secret_value()


def make_pkce() -> tuple[str, str]:
    """(code_verifier, S256 code_challenge)."""
    verifier = secrets.token_urlsafe(64)  # 86 chars, within RFC 7636's 43-128
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return verifier, base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def build_state(
    user_id: uuid.UUID, site_id: uuid.UUID, org_id: uuid.UUID, verifier: str, settings: Settings
) -> str:
    return encrypt_json(
        {
            "u": str(user_id),
            "s": str(site_id),
            "o": str(org_id),
            "v": verifier,
            "n": secrets.token_hex(8),
        },
        settings,
    )


def read_state(token: str, settings: Settings) -> OAuthState:
    """Raises `DecryptionError` for a forged, tampered, expired or malformed state."""
    data = decrypt_json(token, settings, ttl=STATE_TTL_SECONDS)
    try:
        return OAuthState(
            user_id=uuid.UUID(data["u"]),
            site_id=uuid.UUID(data["s"]),
            org_id=uuid.UUID(data["o"]),
            code_verifier=str(data["v"]),
        )
    except (KeyError, ValueError) as exc:
        raise DecryptionError("malformed state") from exc


def authorization_url(settings: Settings, state: str, code_challenge: str) -> str:
    client_id, _ = client_credentials(settings)
    query = urlencode(
        {
            "client_id": client_id,
            "redirect_uri": settings.google_redirect_uri,
            "response_type": "code",
            "scope": SCOPE,
            "access_type": "offline",
            "prompt": "consent",
            "include_granted_scopes": "false",
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
    )
    return f"{AUTH_URL}?{query}"


def _error_text(response: httpx.Response) -> tuple[str, str]:
    """(error code, description) from a Google OAuth error body."""
    try:
        body = response.json()
    except ValueError:
        return "", response.text[:200]
    if isinstance(body, dict):
        return str(body.get("error", "")), str(body.get("error_description", ""))
    return "", ""


def _tokens_from(body: dict[str, Any], previous_refresh: str | None = None) -> TokenSet:
    return TokenSet(
        access_token=str(body["access_token"]),
        # A refresh response normally has no refresh_token: keep the one we already have.
        refresh_token=body.get("refresh_token") or previous_refresh,
        expires_at=time.time() + float(body.get("expires_in", 3600)),
        scope=str(body.get("scope") or SCOPE),
    )


async def exchange_code(
    settings: Settings, code: str, code_verifier: str, *, client: httpx.AsyncClient | None = None
) -> TokenSet:
    client_id, client_secret = client_credentials(settings)
    data = {
        "client_id": client_id,
        "client_secret": client_secret,
        "code": code,
        "code_verifier": code_verifier,
        "grant_type": "authorization_code",
        "redirect_uri": settings.google_redirect_uri,
    }
    async with _client(client) as http:
        response = await http.post(TOKEN_URL, data=data)
    if response.status_code != 200:
        error, description = _error_text(response)
        raise ProviderApiError(
            response.status_code,
            f"Google refused the sign-in ({error or response.status_code}): {description}",
        )
    return _tokens_from(response.json())


async def refresh_tokens(
    settings: Settings, refresh_token: str, *, client: httpx.AsyncClient | None = None
) -> TokenSet:
    """Raises `NeedsReauthError` on invalid_grant (revoked, expired in Testing mode, password changed)."""
    client_id, client_secret = client_credentials(settings)
    data = {
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }
    async with _client(client) as http:
        response = await http.post(TOKEN_URL, data=data)
    if response.status_code != 200:
        error, description = _error_text(response)
        if error == "invalid_grant":
            raise NeedsReauthError("Google no longer accepts this connection. Please connect Google again.")
        raise ProviderApiError(
            response.status_code,
            f"Could not refresh the Google token ({error or response.status_code}): {description}",
        )
    return _tokens_from(response.json(), previous_refresh=refresh_token)


async def revoke_token(token: str, *, client: httpx.AsyncClient | None = None) -> bool:
    """Best effort: True when Google confirmed the revocation."""
    try:
        async with _client(client) as http:
            response = await http.post(REVOKE_URL, data={"token": token})
    except httpx.HTTPError:
        return False
    return response.status_code == 200


class _client:
    def __init__(self, client: httpx.AsyncClient | None) -> None:
        self._given = client
        self._own: httpx.AsyncClient | None = None

    async def __aenter__(self) -> httpx.AsyncClient:
        if self._given is not None:
            return self._given
        self._own = httpx.AsyncClient(timeout=HTTP_TIMEOUT)
        return self._own

    async def __aexit__(self, *exc: object) -> None:
        if self._own is not None:
            await self._own.aclose()
