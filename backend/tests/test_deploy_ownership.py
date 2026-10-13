"""4.1: proof of ownership - DNS TXT or a file, re-checked after 7 days, required before any deploy."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from searchos.core.db import get_sessionmaker
from searchos.deploy import ownership
from searchos.models import Role, Site
from tests.conftest import auth
from tests.deploy_helpers import FakeFetcher, FakeResolver, add_member, checkers, owner_with_site


def make_site() -> Site:
    return Site(domain="shop.example", display_name="Shop", uses_www=False, org_id=None)  # type: ignore[arg-type]


async def test_dns_record_proves_ownership() -> None:
    site = make_site()
    token = ownership.ensure_token(site)
    ok = await ownership.run_check(
        site, checkers(FakeResolver({"shop.example": ["v=spf1 -all", f'"searchos-verify={token}"']}))
    )
    assert ok.ok and ok.method == "dns" and site.verified_at is not None


async def test_dns_record_with_a_wrong_token_is_rejected() -> None:
    site = make_site()
    ownership.ensure_token(site)
    res = await ownership.run_check(
        site, checkers(FakeResolver({"shop.example": ["searchos-verify=somebody-elses"]}))
    )
    assert not res.ok and site.verified_at is None and "Neither proof" in (site.verify_error or "")


async def test_file_proves_ownership_and_only_with_the_exact_token() -> None:
    site = make_site()
    token = ownership.ensure_token(site)
    url = f"https://shop.example/searchos-verify-{token}.txt"
    good = await ownership.check_ownership(
        "shop.example", False, token, checkers(fetcher=FakeFetcher({url: (200, token + "\n")}))
    )
    assert good.ok and good.method == "file"
    for page in ((200, "not the token"), (404, token), (200, token + "x")):
        bad = await ownership.check_ownership(
            "shop.example", False, token, checkers(fetcher=FakeFetcher({url: page}))
        )
        assert not bad.ok


async def test_file_check_also_tries_the_other_host_variant() -> None:
    token = "tok"
    url = f"https://www.shop.example/searchos-verify-{token}.txt"
    res = await ownership.check_ownership(
        "shop.example", False, token, checkers(fetcher=FakeFetcher({url: (200, token)}))
    )
    assert res.ok


async def test_tokens_are_random_and_stable() -> None:
    a, b = make_site(), make_site()
    assert ownership.ensure_token(a) != ownership.ensure_token(b)
    assert ownership.ensure_token(a) == a.verify_token
    assert len(ownership.ensure_token(a)) >= 24


async def test_recheck_after_seven_days_and_loss_of_proof_blocks() -> None:
    site = make_site()
    token = ownership.ensure_token(site)
    start = datetime(2026, 10, 1, tzinfo=UTC)
    ok = checkers(FakeResolver({"shop.example": [f"searchos-verify={token}"]}))
    gone = checkers()
    await ownership.run_check(site, ok, now=start)
    # fresh: no re-check happens (a checker that finds nothing is not even asked)
    await ownership.require_verified(site, gone, now=start + timedelta(days=6, hours=23))
    assert site.verified_at is not None
    # stale and the proof is gone: blocked, and the site is unverified again
    with pytest.raises(ownership.NotVerifiedError, match="re-checked every 7 days"):
        await ownership.require_verified(site, gone, now=start + timedelta(days=8))
    assert site.verified_at is None
    # stale but the proof is still there: allowed, and the clock restarts
    await ownership.run_check(site, ok, now=start)
    await ownership.require_verified(site, ok, now=start + timedelta(days=8))
    assert site.verify_checked_at == start + timedelta(days=8)


async def test_never_verified_site_is_refused() -> None:
    with pytest.raises(ownership.NotVerifiedError, match="not been verified"):
        await ownership.require_verified(make_site(), checkers())


async def test_api_flow(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    headers, site_id, _ = await owner_with_site(client)
    first = (await client.get(f"/sites/{site_id}/deploy/ownership", headers=headers)).json()
    assert first["verified"] is False and first["token"]
    assert first["dns_value"] == f"searchos-verify={first['token']}"
    assert first["file_path"] == f"/searchos-verify-{first['token']}.txt"
    again = (await client.get(f"/sites/{site_id}/deploy/ownership", headers=headers)).json()
    assert again["token"] == first["token"]

    monkeypatch.setattr(ownership, "default_checkers", lambda: checkers())
    miss = (await client.post(f"/sites/{site_id}/deploy/ownership/check", headers=headers)).json()
    assert miss["verified"] is False and miss["error"]

    resolver = FakeResolver({"shop.example": [first["dns_value"]]})
    monkeypatch.setattr(ownership, "default_checkers", lambda: checkers(resolver))
    done = (
        await client.post(f"/sites/{site_id}/deploy/ownership/check", json={"method": "dns"}, headers=headers)
    ).json()
    assert done["verified"] is True and done["method"] == "dns" and done["error"] is None

    # only the chosen method is tried
    file_only = (
        await client.post(
            f"/sites/{site_id}/deploy/ownership/check", json={"method": "file"}, headers=headers
        )
    ).json()
    assert file_only["verified"] is False


async def test_ownership_endpoints_need_admin(client: AsyncClient, session: AsyncSession) -> None:
    _headers, site_id, tokens = await owner_with_site(client)
    org_id = tokens["orgs"][0]["id"]
    for role in (Role.member, Role.client_viewer):
        h = await add_member(client, session, org_id, f"{role.value}@example.com", role)
        assert (await client.get(f"/sites/{site_id}/deploy/ownership", headers=h)).status_code == 403
        assert (await client.post(f"/sites/{site_id}/deploy/ownership/check", headers=h)).status_code == 403
    admin = await add_member(client, session, org_id, "admin@example.com", Role.admin)
    assert (await client.get(f"/sites/{site_id}/deploy/ownership", headers=admin)).status_code == 200
    other = await client.post(
        "/auth/register",
        json={"email": "x@example.com", "password": "correct horse battery staple", "org_name": "X"},
    )
    assert (
        await client.get(f"/sites/{site_id}/deploy/ownership", headers=auth(other.json()))
    ).status_code == 404


async def test_tokens_survive_in_the_database(client: AsyncClient) -> None:
    headers, site_id, _ = await owner_with_site(client)
    token = (await client.get(f"/sites/{site_id}/deploy/ownership", headers=headers)).json()["token"]
    async with get_sessionmaker()() as s:
        row = await s.get(Site, uuid.UUID(site_id))
        assert row is not None and row.verify_token == token
