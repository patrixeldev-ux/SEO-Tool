"""Behaviour that must differ when ENVIRONMENT=production."""

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from tests.conftest import PASSWORD


@pytest_asyncio.fixture
async def prod_client(
    test_env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[AsyncClient]:
    from searchos.core.config import get_settings
    from searchos.main import create_app

    monkeypatch.setenv("ENVIRONMENT", "production")
    get_settings.cache_clear()
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://testserver") as http:
        yield http
    monkeypatch.undo()  # restore ENVIRONMENT=test before the cache is rebuilt
    get_settings.cache_clear()


async def test_refresh_cookie_is_secure_in_production(prod_client: AsyncClient) -> None:
    resp = await prod_client.post(
        "/auth/register", json={"email": "p@example.com", "password": PASSWORD, "org_name": "Prod"}
    )
    assert resp.status_code == 201
    cookie = resp.headers["set-cookie"].lower()
    assert "secure" in cookie.replace("samesite", "")
    assert "httponly" in cookie
    assert "samesite=lax" in cookie


async def test_demo_endpoint_and_api_docs_are_not_exposed_in_production(prod_client: AsyncClient) -> None:
    assert (await prod_client.post("/jobs/demo", json={})).status_code in (404, 405)  # route absent
    assert (await prod_client.get("/docs")).status_code == 404
    assert (await prod_client.get("/openapi.json")).status_code == 404
    assert (await prod_client.get("/health")).status_code == 200
