from httpx import AsyncClient


async def test_health_returns_200(client: AsyncClient) -> None:
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


async def test_ready_checks_postgres_and_redis(client: AsyncClient) -> None:
    resp = await client.get("/health/ready")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ready"}


async def test_version(client: AsyncClient) -> None:
    resp = await client.get("/version")
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "searchos"
    assert body["version"]
    assert body["environment"] == "test"


async def test_cors_allows_configured_origin_only(client: AsyncClient) -> None:
    ok = await client.options(
        "/auth/login",
        headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST"},
    )
    assert ok.headers.get("access-control-allow-origin") == "http://localhost:5173"
    assert ok.headers.get("access-control-allow-credentials") == "true"

    bad = await client.options(
        "/auth/login",
        headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"},
    )
    assert "access-control-allow-origin" not in bad.headers
