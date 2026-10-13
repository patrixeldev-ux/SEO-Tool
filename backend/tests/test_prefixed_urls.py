"""The production kit serves the API under /api (Caddy strips the prefix). Every absolute URL the API hands out
or redirects to is built from settings, never from the request, so it stays correct behind the proxy."""

from __future__ import annotations

from collections.abc import Iterator
from urllib.parse import parse_qs, urlparse

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from searchos.core.config import get_settings
from searchos.deploy.cloudflare import CloudflareDeployer
from tests.conftest import PASSWORD, auth, register
from tests.deploy_fakes import fake_registry, verify_site  # noqa: F401
from tests.deploy_helpers import owner_with_site

HOST = "https://152-67-1-2.sslip.io"


@pytest.fixture
def prefixed_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("REFRESH_COOKIE_PATH", "/api/auth")
    monkeypatch.setenv("COOKIE_SECURE", "true")
    monkeypatch.setenv("CORS_ORIGINS", HOST)
    monkeypatch.setenv("WEB_BASE_URL", HOST)
    monkeypatch.setenv("PUBLIC_API_URL", f"{HOST}/api")
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "cid.apps.googleusercontent.com")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "secret")
    monkeypatch.setenv("GOOGLE_REDIRECT_URI", f"{HOST}/api/integrations/google/callback")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_settings_read_the_prefixed_values(prefixed_env: None) -> None:
    s = get_settings()
    assert s.refresh_cookie_path == "/api/auth"
    assert s.refresh_cookie_secure is True
    assert s.cors_origins == [HOST]
    assert s.public_api_url == f"{HOST}/api"


async def test_refresh_cookie_path_follows_the_prefix(client: AsyncClient, prefixed_env: None) -> None:
    resp = await client.post(
        "/auth/register", json={"email": "p@example.com", "password": PASSWORD, "org_name": "P"}
    )
    assert resp.status_code == 201
    cookie = resp.headers["set-cookie"]
    assert "Path=/api/auth" in cookie
    assert "Secure" in cookie and "HttpOnly" in cookie


async def test_default_cookie_path_is_unprefixed(client: AsyncClient) -> None:
    resp = await client.post(
        "/auth/register", json={"email": "d@example.com", "password": PASSWORD, "org_name": "D"}
    )
    assert "Path=/auth" in resp.headers["set-cookie"]


async def test_google_redirect_uri_and_status_use_the_public_prefix(
    client: AsyncClient, prefixed_env: None
) -> None:
    status = (await client.get("/integrations/status")).json()
    assert status["redirect_uri"] == f"{HOST}/api/integrations/google/callback"


async def test_google_authorization_url_carries_the_prefixed_redirect(
    client: AsyncClient, prefixed_env: None
) -> None:
    tokens = await register(client, "o@example.com")
    site = await client.post(
        "/sites", json={"domain": "shop.example", "display_name": "Shop"}, headers=auth(tokens)
    )
    assert site.status_code == 201, site.text
    resp = await client.post(f"/sites/{site.json()['id']}/integrations/google/connect", headers=auth(tokens))
    assert resp.status_code == 200, resp.text
    q = parse_qs(urlparse(resp.json()["authorization_url"]).query)
    assert q["redirect_uri"] == [f"{HOST}/api/integrations/google/callback"]


async def test_callback_redirects_back_to_the_web_origin_not_the_api_prefix(
    client: AsyncClient, prefixed_env: None
) -> None:
    resp = await client.get("/integrations/google/callback", params={"error": "access_denied", "state": "x"})
    # An invalid state goes to the web app's settings page on the public origin.
    assert resp.status_code == 303
    assert resp.headers["location"].startswith(f"{HOST}/settings?")


async def test_cloudflare_rules_url_includes_the_api_prefix(
    client: AsyncClient, session: AsyncSession, fake_registry: object, prefixed_env: None
) -> None:
    headers, site_id, _ = await owner_with_site(client)
    await verify_site(session, site_id)
    fake_registry.register(CloudflareDeployer())  # type: ignore[attr-defined]
    r = await client.put(f"/sites/{site_id}/deploy/connections/cloudflare", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["rules_url"] == f"{HOST}/api/edge/rules/{body['site_token']}"


async def test_cors_allows_only_the_public_origin(prefixed_env: None, client: AsyncClient) -> None:
    # prefixed_env first: the app reads CORS_ORIGINS when it is created.
    ok = await client.options(
        "/auth/login", headers={"Origin": HOST, "Access-Control-Request-Method": "POST"}
    )
    assert ok.headers.get("access-control-allow-origin") == HOST
    assert ok.headers.get("access-control-allow-credentials") == "true"
    bad = await client.options(
        "/auth/login", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"}
    )
    assert "access-control-allow-origin" not in bad.headers


async def test_registration_can_be_switched_off(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALLOW_REGISTRATION", "false")
    get_settings.cache_clear()
    try:
        resp = await client.post(
            "/auth/register", json={"email": "x@example.com", "password": PASSWORD, "org_name": "X"}
        )
        assert resp.status_code == 403
    finally:
        get_settings.cache_clear()
