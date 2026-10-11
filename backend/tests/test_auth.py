import uuid
from datetime import UTC, datetime, timedelta

import jwt
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from searchos.core import auth as auth_service
from searchos.core.security import hash_refresh_token
from searchos.models import Membership, RefreshToken, Role, User
from tests.conftest import PASSWORD, TEST_JWT_SECRET, auth, register

COOKIE = "searchos_refresh"


async def test_register_creates_user_org_and_owner_membership(
    client: AsyncClient, session: AsyncSession
) -> None:
    data = await register(client, "Founder@Example.com", org_name="Patrixel")
    assert data["user"]["email"] == "founder@example.com"  # lowercased
    assert data["orgs"] == [{"id": data["orgs"][0]["id"], "name": "Patrixel", "role": "owner"}]
    assert data["refresh_token"] is None  # web clients get a cookie, never the token in JSON
    assert data["token_type"] == "bearer"
    assert data["expires_in"] == 15 * 60

    membership = await session.scalar(sa.select(Membership))
    assert membership is not None
    assert membership.role is Role.owner


async def test_register_sets_httponly_samesite_lax_cookie(client: AsyncClient) -> None:
    resp = await client.post(
        "/auth/register",
        json={"email": "a@example.com", "password": PASSWORD, "org_name": "A"},
    )
    cookie = resp.headers["set-cookie"].lower()
    assert f"{COOKIE}=" in cookie
    assert "httponly" in cookie
    assert "samesite=lax" in cookie
    assert "path=/auth" in cookie
    assert "secure" not in cookie.replace("samesite", "")  # Secure only in production


async def test_passwords_and_refresh_tokens_are_never_stored_in_plaintext(
    client: AsyncClient, session: AsyncSession
) -> None:
    await register(client, "a@example.com")
    raw_refresh = client.cookies[COOKIE]
    user = await session.scalar(sa.select(User))
    assert user is not None
    assert user.password_hash.startswith("$argon2id$")
    assert PASSWORD not in user.password_hash
    stored = await session.scalar(sa.select(RefreshToken))
    assert stored is not None
    assert stored.token_hash == hash_refresh_token(raw_refresh)
    assert stored.token_hash != raw_refresh


async def test_register_duplicate_email_conflicts(client: AsyncClient) -> None:
    await register(client, "dup@example.com")
    resp = await client.post(
        "/auth/register", json={"email": "DUP@example.com", "password": PASSWORD, "org_name": "Other"}
    )
    assert resp.status_code == 409


async def test_register_validation(client: AsyncClient) -> None:
    short = await client.post(
        "/auth/register", json={"email": "a@example.com", "password": "short", "org_name": "A"}
    )
    assert short.status_code == 422
    bad_email = await client.post(
        "/auth/register", json={"email": "nope", "password": PASSWORD, "org_name": "A"}
    )
    assert bad_email.status_code == 422


async def test_login_happy_path(client: AsyncClient) -> None:
    await register(client, "user@example.com")
    client.cookies.clear()
    resp = await client.post("/auth/login", json={"email": "USER@example.com", "password": PASSWORD})
    assert resp.status_code == 200
    body = resp.json()
    assert body["access_token"]
    assert COOKIE in resp.headers["set-cookie"]
    me = await client.get("/auth/me", headers=auth(body))
    assert me.status_code == 200
    assert me.json()["user"]["email"] == "user@example.com"


async def test_login_wrong_password_and_unknown_email_look_identical(client: AsyncClient) -> None:
    await register(client, "user@example.com")
    client.cookies.clear()
    wrong = await client.post("/auth/login", json={"email": "user@example.com", "password": "nope-nope-nope"})
    unknown = await client.post("/auth/login", json={"email": "ghost@example.com", "password": PASSWORD})
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()
    assert COOKIE not in wrong.headers.get("set-cookie", "")


async def test_inactive_user_cannot_log_in(client: AsyncClient, session: AsyncSession) -> None:
    await register(client, "user@example.com")
    await session.execute(sa.update(User).values(is_active=False))
    await session.commit()
    resp = await client.post("/auth/login", json={"email": "user@example.com", "password": PASSWORD})
    assert resp.status_code == 401


async def test_protected_route_requires_valid_access_token(client: AsyncClient) -> None:
    assert (await client.get("/auth/me")).status_code == 401
    assert (await client.get("/auth/me", headers={"Authorization": "Bearer garbage"})).status_code == 401

    expired = jwt.encode(
        {
            "sub": str(uuid.uuid4()),
            "typ": "access",
            "iat": datetime.now(UTC) - timedelta(hours=1),
            "exp": datetime.now(UTC) - timedelta(minutes=45),
        },
        TEST_JWT_SECRET,
    )
    resp = await client.get("/auth/me", headers={"Authorization": f"Bearer {expired}"})
    assert resp.status_code == 401


async def test_refresh_rotates_token_and_returns_new_access_token(client: AsyncClient) -> None:
    await register(client, "user@example.com")
    first_cookie = client.cookies[COOKIE]

    resp = await client.post("/auth/refresh")
    assert resp.status_code == 200
    second_cookie = client.cookies[COOKIE]
    assert second_cookie != first_cookie
    me = await client.get("/auth/me", headers=auth(resp.json()))
    assert me.status_code == 200

    # and the chain continues
    assert (await client.post("/auth/refresh")).status_code == 200


async def test_refresh_token_reuse_revokes_the_whole_family(client: AsyncClient) -> None:
    await register(client, "user@example.com")
    stolen = client.cookies[COOKIE]  # the attacker captured this token

    legit = await client.post("/auth/refresh")  # the real client rotates it
    assert legit.status_code == 200
    newest = client.cookies[COOKIE]

    # The attacker replays the already-rotated token...
    client.cookies.clear()
    replay = await client.post("/auth/refresh", json={"refresh_token": stolen})
    assert replay.status_code == 401

    # ...which kills the legitimate holder's newest token too (whole family revoked).
    after = await client.post("/auth/refresh", json={"refresh_token": newest})
    assert after.status_code == 401


async def test_reuse_does_not_affect_other_sessions(client: AsyncClient) -> None:
    await register(client, "user@example.com")
    laptop = client.cookies[COOKIE]
    client.cookies.clear()
    phone = (
        await client.post("/auth/login", json={"email": "user@example.com", "password": PASSWORD})
    ).cookies[COOKIE]
    # laptop session gets burned by replay
    assert (await client.post("/auth/refresh", json={"refresh_token": laptop})).status_code == 200
    assert (await client.post("/auth/refresh", json={"refresh_token": laptop})).status_code == 401
    # the phone session is a different family and keeps working
    client.cookies.clear()
    assert (await client.post("/auth/refresh", json={"refresh_token": phone})).status_code == 200


async def test_expired_refresh_token_is_rejected(client: AsyncClient, session: AsyncSession) -> None:
    await register(client, "user@example.com")
    await session.execute(sa.update(RefreshToken).values(expires_at=datetime.now(UTC) - timedelta(seconds=1)))
    await session.commit()
    resp = await client.post("/auth/refresh")
    assert resp.status_code == 401
    # the dead cookie is cleared by the server
    assert "max-age=0" in resp.headers["set-cookie"].lower()


async def test_unknown_or_missing_refresh_token_rejected(client: AsyncClient) -> None:
    assert (await client.post("/auth/refresh")).status_code == 401
    assert (await client.post("/auth/refresh", json={"refresh_token": "nope"})).status_code == 401


async def test_logout_revokes_family_and_clears_cookie(client: AsyncClient) -> None:
    await register(client, "user@example.com")
    token = client.cookies[COOKIE]
    resp = await client.post("/auth/logout")
    assert resp.status_code == 204
    assert "max-age=0" in resp.headers["set-cookie"].lower()
    client.cookies.clear()
    assert (await client.post("/auth/refresh", json={"refresh_token": token})).status_code == 401


async def test_logout_without_session_is_harmless(client: AsyncClient) -> None:
    assert (await client.post("/auth/logout")).status_code == 204


async def test_cookie_refresh_from_foreign_origin_is_blocked(client: AsyncClient) -> None:
    await register(client, "user@example.com")
    evil = await client.post("/auth/refresh", headers={"Origin": "https://evil.example"})
    assert evil.status_code == 403
    good = await client.post("/auth/refresh", headers={"Origin": "http://localhost:5173"})
    assert good.status_code == 200


async def test_desktop_client_gets_refresh_token_in_body_and_no_cookie(client: AsyncClient) -> None:
    resp = await client.post(
        "/auth/register",
        json={"email": "d@example.com", "password": PASSWORD, "org_name": "D"},
        headers={"X-Client-Type": "desktop"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["refresh_token"]
    assert "set-cookie" not in resp.headers

    rotated = await client.post(
        "/auth/refresh", json={"refresh_token": body["refresh_token"]}, headers={"X-Client-Type": "desktop"}
    )
    assert rotated.status_code == 200
    assert rotated.json()["refresh_token"] not in (None, body["refresh_token"])


async def test_purge_removes_only_long_expired_tokens(client: AsyncClient, session: AsyncSession) -> None:
    await register(client, "user@example.com")
    user = await session.scalar(sa.select(User))
    assert user is not None
    now = datetime.now(UTC)
    for days_ago in (30, 2):
        session.add(
            RefreshToken(
                user_id=user.id,
                family_id=uuid.uuid4(),
                token_hash=uuid.uuid4().hex + uuid.uuid4().hex,
                expires_at=now - timedelta(days=days_ago),
            )
        )
    await session.commit()
    removed = await auth_service.purge_expired_refresh_tokens(session, older_than_days=7)
    assert removed == 1
    remaining = await session.scalar(sa.select(sa.func.count()).select_from(RefreshToken))
    assert remaining == 2  # the live one from register() + the recently expired one
