"""Google OAuth (PKCE + encrypted state), encrypted token storage, refresh, property picker, disconnect.

Every Google call is mocked with respx; nothing here reaches the network.
"""

from __future__ import annotations

import base64
import hashlib
import uuid
from collections.abc import Iterator
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import respx
import sqlalchemy as sa
from httpx import AsyncClient, Response
from sqlalchemy.ext.asyncio import AsyncSession

from searchos.core import crypto
from searchos.core.config import get_settings
from searchos.core.db import get_sessionmaker
from searchos.integrations import google_oauth
from searchos.integrations.errors import NeedsReauthError, ProviderApiError
from searchos.integrations.google_api import GoogleSession, list_properties, match_property, retry_delay
from searchos.models import ConnectionStatus, IntegrationConnection, Membership, Provider, Role
from tests.conftest import auth, register

CLIENT_ID = "test-client-id.apps.googleusercontent.com"
CLIENT_SECRET = "test-client-secret"
REDIRECT = "http://localhost:8000/integrations/google/callback"


@pytest.fixture
def google_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("GOOGLE_CLIENT_ID", CLIENT_ID)
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", CLIENT_SECRET)
    monkeypatch.setenv("WEB_BASE_URL", "http://web.test")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def make_site(client: AsyncClient, tokens: dict[str, Any], domain: str = "example.com") -> str:
    resp = await client.post("/sites", json={"domain": domain}, headers=auth(tokens))
    assert resp.status_code == 201
    return str(resp.json()["id"])


async def start_connect(
    client: AsyncClient, tokens: dict[str, Any], site_id: str
) -> tuple[str, dict[str, list[str]]]:
    resp = await client.post(f"/sites/{site_id}/integrations/google/connect", headers=auth(tokens))
    assert resp.status_code == 200, resp.text
    query = parse_qs(urlparse(resp.json()["authorization_url"]).query)
    return query["state"][0], query


def token_response(access: str = "ya29.access-1", refresh: str | None = "1//refresh-1") -> dict[str, Any]:
    body: dict[str, Any] = {
        "access_token": access,
        "expires_in": 3599,
        "scope": google_oauth.SCOPE,
        "token_type": "Bearer",
    }
    if refresh:
        body["refresh_token"] = refresh
    return body


async def connect_site(client: AsyncClient, tokens: dict[str, Any], site_id: str, **token_kw: Any) -> None:
    state, _ = await start_connect(client, tokens, site_id)
    with respx.mock(assert_all_called=False) as router:
        router.post(google_oauth.TOKEN_URL).respond(200, json=token_response(**token_kw))
        resp = await client.get("/integrations/google/callback", params={"code": "auth-code", "state": state})
    assert resp.status_code == 303
    assert "google=connected" in resp.headers["location"]


async def connection(session: AsyncSession, site_id: str) -> IntegrationConnection:
    session.expire_all()
    conn = await session.scalar(
        sa.select(IntegrationConnection).where(IntegrationConnection.site_id == uuid.UUID(site_id))
    )
    assert conn is not None
    return conn


# ---- status / configuration -------------------------------------------------------------------------
async def test_status_says_google_not_configured_without_env(client: AsyncClient) -> None:
    body = (await client.get("/integrations/status")).json()
    assert body["google"]["configured"] is False
    assert "isn't set up" in body["google"]["message"]
    assert body["psi"]["key_configured"] is False
    assert "secret" not in str(body).lower().replace("client_secret", "")


async def test_status_configured_with_env(client: AsyncClient, google_env: None) -> None:
    body = (await client.get("/integrations/status")).json()
    assert body["google"]["configured"] is True
    assert body["redirect_uri"] == REDIRECT


async def test_connect_refused_when_google_not_configured(client: AsyncClient) -> None:
    tokens = await register(client, "o@example.com")
    site_id = await make_site(client, tokens)
    resp = await client.post(f"/sites/{site_id}/integrations/google/connect", headers=auth(tokens))
    assert resp.status_code == 409
    assert "isn't set up" in resp.json()["detail"]


# ---- connect: URL, PKCE, state ----------------------------------------------------------------------
async def test_authorization_url_uses_pkce_offline_access_and_a_bound_state(
    client: AsyncClient, google_env: None
) -> None:
    tokens = await register(client, "o@example.com")
    site_id = await make_site(client, tokens)
    state, q = await start_connect(client, tokens, site_id)
    assert q["client_id"] == [CLIENT_ID]
    assert q["redirect_uri"] == [REDIRECT]
    assert q["response_type"] == ["code"]
    assert q["scope"] == ["https://www.googleapis.com/auth/webmasters.readonly"]
    assert q["access_type"] == ["offline"]
    assert q["prompt"] == ["consent"]
    assert q["code_challenge_method"] == ["S256"]
    parsed = google_oauth.read_state(state, get_settings())
    assert str(parsed.user_id) == tokens["user"]["id"]
    assert str(parsed.site_id) == site_id
    digest = hashlib.sha256(parsed.code_verifier.encode()).digest()
    assert q["code_challenge"] == [base64.urlsafe_b64encode(digest).rstrip(b"=").decode()]
    assert parsed.code_verifier not in state  # the verifier is encrypted inside the state


async def test_only_admins_can_connect_and_other_orgs_get_404(
    client: AsyncClient, session: AsyncSession, google_env: None
) -> None:
    owner = await register(client, "o@example.com")
    site_id = await make_site(client, owner)
    member = await register(client, "m@example.com", org_name="M org")
    session.add(
        Membership(
            org_id=uuid.UUID(owner["orgs"][0]["id"]),
            user_id=uuid.UUID(member["user"]["id"]),
            role=Role.member,
        )
    )
    await session.commit()
    resp = await client.post(
        f"/sites/{site_id}/integrations/google/connect", headers=auth(member, owner["orgs"][0]["id"])
    )
    assert resp.status_code == 403
    stranger = await register(client, "s@example.com", org_name="S org")
    resp = await client.post(f"/sites/{site_id}/integrations/google/connect", headers=auth(stranger))
    assert resp.status_code == 404


# ---- callback ---------------------------------------------------------------------------------------
async def test_callback_stores_encrypted_tokens_and_sends_the_pkce_verifier(
    client: AsyncClient, session: AsyncSession, google_env: None
) -> None:
    tokens = await register(client, "o@example.com")
    site_id = await make_site(client, tokens)
    state, _ = await start_connect(client, tokens, site_id)
    verifier = google_oauth.read_state(state, get_settings()).code_verifier
    with respx.mock(assert_all_called=False) as router:
        route = router.post(google_oauth.TOKEN_URL).respond(200, json=token_response())
        resp = await client.get("/integrations/google/callback", params={"code": "the-code", "state": state})
    assert resp.status_code == 303
    assert resp.headers["location"].startswith("http://web.test/settings?")
    assert "google=connected" in resp.headers["location"] and site_id in resp.headers["location"]
    sent = parse_qs(route.calls.last.request.content.decode())
    assert sent["code"] == ["the-code"] and sent["code_verifier"] == [verifier]
    assert sent["grant_type"] == ["authorization_code"] and sent["client_secret"] == [CLIENT_SECRET]

    conn = await connection(session, site_id)
    assert conn.status is ConnectionStatus.pending and conn.provider is Provider.google
    assert conn.credentials_encrypted and "ya29.access-1" not in conn.credentials_encrypted
    assert "1//refresh-1" not in conn.credentials_encrypted
    stored = crypto.decrypt_json(conn.credentials_encrypted)
    assert stored["refresh_token"] == "1//refresh-1"
    # No endpoint ever returns credentials.
    listing = (await client.get(f"/sites/{site_id}/integrations", headers=auth(tokens))).json()
    assert listing["google"]["status"] == "pending"
    assert "credentials" not in str(listing) and "ya29" not in str(listing)


async def test_callback_rejects_tampered_or_missing_state(
    client: AsyncClient, session: AsyncSession, google_env: None
) -> None:
    tokens = await register(client, "o@example.com")
    site_id = await make_site(client, tokens)
    state, _ = await start_connect(client, tokens, site_id)
    with respx.mock(assert_all_called=False) as router:
        route = router.post(google_oauth.TOKEN_URL).respond(200, json=token_response())
        bad = await client.get(
            "/integrations/google/callback", params={"code": "c", "state": state[:-4] + "AAAA"}
        )
        none = await client.get("/integrations/google/callback", params={"code": "c"})
        assert not route.called
    assert "reason=state_invalid" in bad.headers["location"]
    assert "reason=missing_state" in none.headers["location"]
    assert (await session.scalar(sa.select(sa.func.count()).select_from(IntegrationConnection))) == 0


async def test_callback_rejects_expired_state(
    client: AsyncClient, session: AsyncSession, google_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    tokens = await register(client, "o@example.com")
    site_id = await make_site(client, tokens)
    state, _ = await start_connect(client, tokens, site_id)
    monkeypatch.setattr(google_oauth, "STATE_TTL_SECONDS", -5)
    resp = await client.get("/integrations/google/callback", params={"code": "c", "state": state})
    assert "reason=state_invalid" in resp.headers["location"]


async def test_callback_user_denied_consent(client: AsyncClient, google_env: None) -> None:
    tokens = await register(client, "o@example.com")
    site_id = await make_site(client, tokens)
    state, _ = await start_connect(client, tokens, site_id)
    resp = await client.get(
        "/integrations/google/callback", params={"error": "access_denied", "state": state}
    )
    assert "google=error" in resp.headers["location"] and "reason=denied" in resp.headers["location"]


async def test_callback_fails_if_user_lost_admin_role_meanwhile(
    client: AsyncClient, session: AsyncSession, google_env: None
) -> None:
    tokens = await register(client, "o@example.com")
    site_id = await make_site(client, tokens)
    state, _ = await start_connect(client, tokens, site_id)
    await session.execute(sa.update(Membership).values(role=Role.member))
    await session.commit()
    with respx.mock(assert_all_called=False) as router:
        route = router.post(google_oauth.TOKEN_URL).respond(200, json=token_response())
        resp = await client.get("/integrations/google/callback", params={"code": "c", "state": state})
        assert not route.called
    assert "reason=not_allowed" in resp.headers["location"]


async def test_callback_without_refresh_token_is_an_error(
    client: AsyncClient, session: AsyncSession, google_env: None
) -> None:
    tokens = await register(client, "o@example.com")
    site_id = await make_site(client, tokens)
    state, _ = await start_connect(client, tokens, site_id)
    with respx.mock(assert_all_called=False) as router:
        router.post(google_oauth.TOKEN_URL).respond(200, json=token_response(refresh=None))
        resp = await client.get("/integrations/google/callback", params={"code": "c", "state": state})
    assert "reason=no_refresh_token" in resp.headers["location"]
    assert (await session.scalar(sa.select(sa.func.count()).select_from(IntegrationConnection))) == 0


# ---- property picker --------------------------------------------------------------------------------
SITES_BODY = {
    "siteEntry": [
        {"siteUrl": "https://other.com/", "permissionLevel": "siteOwner"},
        {"siteUrl": "https://www.example.com/", "permissionLevel": "siteFullUser"},
        {"siteUrl": "sc-domain:example.com", "permissionLevel": "siteOwner"},
        {"siteUrl": "https://example.com/", "permissionLevel": "siteUnverifiedUser"},
    ]
}


async def test_property_picker_suggests_domain_property_and_saves_selection(
    client: AsyncClient, session: AsyncSession, google_env: None
) -> None:
    tokens = await register(client, "o@example.com")
    site_id = await make_site(client, tokens)
    await connect_site(client, tokens, site_id)
    with respx.mock(assert_all_called=False) as router:
        router.get("https://www.googleapis.com/webmasters/v3/sites").respond(200, json=SITES_BODY)
        listing = (
            await client.get(f"/sites/{site_id}/integrations/google/properties", headers=auth(tokens))
        ).json()
        assert listing["suggested"] == "sc-domain:example.com"
        assert {p["site_url"] for p in listing["properties"] if not p["usable"]} == {"https://example.com/"}
        ok = await client.put(
            f"/sites/{site_id}/integrations/google/property",
            json={"site_url": "sc-domain:example.com"},
            headers=auth(tokens),
        )
        assert ok.status_code == 200, ok.text
        assert ok.json()["status"] == "connected" and ok.json()["property_url"] == "sc-domain:example.com"
        unusable = await client.put(
            f"/sites/{site_id}/integrations/google/property",
            json={"site_url": "https://example.com/"},
            headers=auth(tokens),
        )
        assert unusable.status_code == 422
        unknown = await client.put(
            f"/sites/{site_id}/integrations/google/property",
            json={"site_url": "https://nope.example/"},
            headers=auth(tokens),
        )
        assert unknown.status_code == 422
    assert (await connection(session, site_id)).property_url == "sc-domain:example.com"


@pytest.mark.parametrize(
    ("domain", "uses_www", "available", "expected"),
    [
        ("example.com", False, ["sc-domain:example.com", "https://example.com/"], "sc-domain:example.com"),
        ("example.com", False, ["https://www.example.com/", "https://example.com/"], "https://example.com/"),
        (
            "example.com",
            True,
            ["https://www.example.com/", "https://example.com/"],
            "https://www.example.com/",
        ),
        ("example.com", False, ["http://example.com/"], "http://example.com/"),
        ("example.com", False, ["https://example.org/", "sc-domain:other.com"], None),
        ("example.com", False, [], None),
    ],
)
def test_match_property(domain: str, uses_www: bool, available: list[str], expected: str | None) -> None:
    assert match_property(domain, uses_www, available) == expected


# ---- token refresh, reauth, backoff -----------------------------------------------------------------
async def stored_session(client: AsyncClient, tokens: dict[str, Any], site_id: str, **kw: Any) -> uuid.UUID:
    await connect_site(client, tokens, site_id, **kw)
    sessions = get_sessionmaker()
    async with sessions() as s:
        conn = await s.scalar(
            sa.select(IntegrationConnection).where(IntegrationConnection.site_id == uuid.UUID(site_id))
        )
        assert conn is not None
        return conn.id


async def test_expired_access_token_is_refreshed_and_persisted(
    client: AsyncClient, session: AsyncSession, google_env: None
) -> None:
    tokens = await register(client, "o@example.com")
    site_id = await make_site(client, tokens)
    conn_id = await stored_session(client, tokens, site_id)
    conn = await connection(session, site_id)
    expired = {**crypto.decrypt_json(conn.credentials_encrypted or ""), "expires_at": 1.0}
    conn.credentials_encrypted = crypto.encrypt_json(expired)
    await session.commit()

    with respx.mock(assert_all_called=False) as router:
        refresh = router.post(google_oauth.TOKEN_URL).respond(
            200, json={"access_token": "ya29.new", "expires_in": 3600}
        )
        api = router.get("https://www.googleapis.com/webmasters/v3/sites").respond(200, json=SITES_BODY)
        async with GoogleSession(get_sessionmaker(), conn_id) as google:
            props = await list_properties(google)
    assert len(props) == 4
    assert parse_qs(refresh.calls.last.request.content.decode())["refresh_token"] == ["1//refresh-1"]
    assert api.calls.last.request.headers["Authorization"] == "Bearer ya29.new"
    stored = crypto.decrypt_json((await connection(session, site_id)).credentials_encrypted or "")
    assert stored["access_token"] == "ya29.new" and stored["refresh_token"] == "1//refresh-1"


async def test_a_401_triggers_one_refresh_and_a_retry(client: AsyncClient, google_env: None) -> None:
    tokens = await register(client, "o@example.com")
    site_id = await make_site(client, tokens)
    conn_id = await stored_session(client, tokens, site_id)
    with respx.mock(assert_all_called=False) as router:
        refresh = router.post(google_oauth.TOKEN_URL).respond(
            200, json={"access_token": "ya29.new", "expires_in": 3600}
        )
        router.get("https://www.googleapis.com/webmasters/v3/sites").mock(
            side_effect=[
                Response(401, json={"error": {"message": "expired"}}),
                Response(200, json=SITES_BODY),
            ]
        )
        async with GoogleSession(get_sessionmaker(), conn_id) as google:
            assert len(await list_properties(google)) == 4
    assert refresh.call_count == 1


async def test_invalid_grant_marks_the_connection_needs_reauth(
    client: AsyncClient, session: AsyncSession, google_env: None
) -> None:
    tokens = await register(client, "o@example.com")
    site_id = await make_site(client, tokens)
    conn_id = await stored_session(client, tokens, site_id)
    with respx.mock(assert_all_called=False) as router:
        router.get("https://www.googleapis.com/webmasters/v3/sites").respond(
            401, json={"error": {"message": "x"}}
        )
        router.post(google_oauth.TOKEN_URL).respond(
            400, json={"error": "invalid_grant", "error_description": "Token has been expired or revoked."}
        )
        async with GoogleSession(get_sessionmaker(), conn_id) as google:
            with pytest.raises(NeedsReauthError):
                await list_properties(google)
            with pytest.raises(NeedsReauthError):  # and it does not keep hammering Google afterwards
                await list_properties(google)
    conn = await connection(session, site_id)
    assert conn.status is ConnectionStatus.needs_reauth and conn.last_error
    listing = (await client.get(f"/sites/{site_id}/integrations", headers=auth(tokens))).json()
    assert listing["google"]["status"] == "needs_reauth"
    # The picker endpoint reports it as a clean 409 for the UI.
    resp = await client.get(f"/sites/{site_id}/integrations/google/properties", headers=auth(tokens))
    assert resp.status_code == 409


async def test_429_and_5xx_are_retried_with_exponential_backoff(
    client: AsyncClient, google_env: None
) -> None:
    tokens = await register(client, "o@example.com")
    site_id = await make_site(client, tokens)
    conn_id = await stored_session(client, tokens, site_id)
    delays: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        delays.append(seconds)

    with respx.mock(assert_all_called=False) as router:
        router.get("https://www.googleapis.com/webmasters/v3/sites").mock(
            side_effect=[Response(429), Response(503), Response(500), Response(200, json=SITES_BODY)]
        )
        async with GoogleSession(get_sessionmaker(), conn_id, sleep=fake_sleep) as google:
            assert len(await list_properties(google)) == 4
    assert delays == [1.0, 2.0, 4.0]


async def test_backoff_gives_up_and_non_retryable_errors_fail_at_once(
    client: AsyncClient, google_env: None
) -> None:
    tokens = await register(client, "o@example.com")
    site_id = await make_site(client, tokens)
    conn_id = await stored_session(client, tokens, site_id)
    delays: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        delays.append(seconds)

    with respx.mock(assert_all_called=False) as router:
        route = router.get("https://www.googleapis.com/webmasters/v3/sites").respond(
            403, json={"error": {"message": "Search Console API has not been used in project 1"}}
        )
        async with GoogleSession(get_sessionmaker(), conn_id, sleep=fake_sleep) as google:
            with pytest.raises(ProviderApiError, match="has not been used") as info:
                await list_properties(google)
        assert info.value.status == 403 and route.call_count == 1 and delays == []
        router.get("https://www.googleapis.com/webmasters/v3/sites").respond(503)
        async with GoogleSession(get_sessionmaker(), conn_id, sleep=fake_sleep) as google:
            with pytest.raises(ProviderApiError):
                await list_properties(google)
    assert len(delays) == 5  # six attempts, five waits


def test_retry_delay_honours_retry_after_and_caps() -> None:
    assert retry_delay(1, None) == 1.0 and retry_delay(3, None) == 4.0 and retry_delay(20, None) == 60.0
    assert retry_delay(1, httpx.Response(429, headers={"Retry-After": "7"})) == 7.0
    assert retry_delay(1, httpx.Response(429, headers={"Retry-After": "9999"})) == 60.0


# ---- disconnect -------------------------------------------------------------------------------------
async def test_disconnect_revokes_and_deletes_the_stored_tokens(
    client: AsyncClient, session: AsyncSession, google_env: None
) -> None:
    tokens = await register(client, "o@example.com")
    site_id = await make_site(client, tokens)
    await connect_site(client, tokens, site_id)
    with respx.mock(assert_all_called=False) as router:
        revoke = router.post(google_oauth.REVOKE_URL).respond(200)
        resp = await client.delete(f"/sites/{site_id}/integrations/google", headers=auth(tokens))
    assert resp.status_code == 204
    assert parse_qs(revoke.calls.last.request.content.decode())["token"] == ["1//refresh-1"]
    assert (await session.scalar(sa.select(sa.func.count()).select_from(IntegrationConnection))) == 0
    again = await client.delete(f"/sites/{site_id}/integrations/google", headers=auth(tokens))
    assert again.status_code == 204  # idempotent


async def test_disconnect_still_deletes_when_revoke_fails(
    client: AsyncClient, session: AsyncSession, google_env: None
) -> None:
    tokens = await register(client, "o@example.com")
    site_id = await make_site(client, tokens)
    await connect_site(client, tokens, site_id)
    with respx.mock(assert_all_called=False) as router:
        router.post(google_oauth.REVOKE_URL).mock(side_effect=httpx.ConnectError("offline"))
        resp = await client.delete(f"/sites/{site_id}/integrations/google", headers=auth(tokens))
    assert resp.status_code == 204
    assert (await session.scalar(sa.select(sa.func.count()).select_from(IntegrationConnection))) == 0


# ---- encryption key handling ------------------------------------------------------------------------
def test_production_without_key_refuses_to_encrypt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("JWT_SECRET", "a-real-production-secret-that-is-long-enough-123")
    monkeypatch.delenv("TOKEN_ENCRYPTION_KEY", raising=False)
    get_settings.cache_clear()
    try:
        assert crypto.encryption_available() is False
        with pytest.raises(crypto.EncryptionUnavailableError):
            crypto.encrypt_json({"a": 1})
    finally:
        get_settings.cache_clear()


def test_explicit_key_is_used_and_garbage_key_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    from cryptography.fernet import Fernet

    key = Fernet.generate_key().decode()
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", key)
    get_settings.cache_clear()
    try:
        token = crypto.encrypt_json({"a": 1})
        assert Fernet(key.encode()).decrypt(token.encode())
        monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "not-a-key")
        get_settings.cache_clear()
        assert crypto.encryption_available() is False
    finally:
        get_settings.cache_clear()


def test_wrong_key_cannot_decrypt(monkeypatch: pytest.MonkeyPatch) -> None:
    token = crypto.encrypt_json({"a": 1})
    monkeypatch.setenv("JWT_SECRET", "another-secret-another-secret-another-secret-1")
    get_settings.cache_clear()
    try:
        with pytest.raises(crypto.DecryptionError):
            crypto.decrypt_json(token)
    finally:
        get_settings.cache_clear()
