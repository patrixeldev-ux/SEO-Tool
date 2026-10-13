"""4.3 (API side): the signed rules endpoint, the Cloudflare connection, kill switch, rollback, shared vectors."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from searchos.deploy import connections, service
from searchos.deploy.cloudflare import CloudflareDeployer
from searchos.deploy.livefetch import LiveResponse
from searchos.deploy.signing import (
    MAX_AGE_SECONDS,
    SIGNATURE_HEADER,
    build_rules,
    normalise_path,
    sign_body,
    verify_rules,
)
from searchos.devtools import fake_site
from tests.deploy_fakes import PAGE, URL, FakeWebsite, fake_registry, make_action, verify_site  # noqa: F401
from tests.deploy_helpers import owner_with_site

VECTORS = json.loads(
    (Path(__file__).parents[2] / "edge" / "test" / "vectors.json").read_text(encoding="utf-8")
)


# ---- the pure pieces, against the vectors the Worker tests also use ---------------------------------------------
@pytest.mark.parametrize(
    "case", VECTORS["normalise"], ids=[c["input"] or "<empty>" for c in VECTORS["normalise"]]
)
def test_path_normalisation_vectors(case: dict[str, str]) -> None:
    assert normalise_path(case["input"]) == case["path"]


def test_signature_vector() -> None:
    v = VECTORS["signature"]
    assert sign_body(v["body"], v["secret"]) == v["sig"]


def test_verify_rules_rejects_unsigned_stale_and_future() -> None:
    body = build_rules(host="a.example", version=1, issued_at=1_000_000, enabled=True, pages={})
    sig = sign_body(body, "s")
    assert verify_rules(body, sig, "s", now=1_000_100) is not None
    assert verify_rules(body, "", "s", now=1_000_100) is None
    assert verify_rules(body, sig, "other", now=1_000_100) is None
    assert verify_rules(body + " ", sig, "s", now=1_000_100) is None
    assert verify_rules(body, sig, "s", now=1_000_000 + MAX_AGE_SECONDS) is not None
    assert verify_rules(body, sig, "s", now=1_000_000 + MAX_AGE_SECONDS + 1) is None
    assert verify_rules(body, sig, "s", now=1_000_000 - 301) is None


def test_disabled_rules_carry_no_pages() -> None:
    body = build_rules(
        host="a.example", version=1, issued_at=5, enabled=False, pages={"/x": [{"op": "title"}]}
    )
    assert json.loads(body)["pages"] == {}


# ---- the API ------------------------------------------------------------------------------------------------------
class EdgeFetcher:
    """The fake website behind a pretend Worker; `header` is what the home page shows in `x-searchos-edge`."""

    def __init__(self, header: str | None) -> None:
        self.header = header

    async def get(self, url: str, *, no_cache: bool = False) -> LiveResponse:
        headers = {"x-searchos-edge": self.header} if self.header else {}
        return LiveResponse(url=url, status=200, text="<html></html>", headers=headers)


async def setup(
    client: AsyncClient, session: AsyncSession, fake_registry: Any
) -> tuple[Any, dict[str, str], Any]:
    headers, site_id, _ = await owner_with_site(client)
    site = await verify_site(session, site_id)
    fake_registry.register(CloudflareDeployer())
    r = await client.put(f"/sites/{site_id}/deploy/connections/cloudflare", headers=headers)
    assert r.status_code == 200, r.text
    return site, headers, r.json()


async def test_connect_test_and_choose(
    client: AsyncClient, session: AsyncSession, fake_registry: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    site, headers, setup_values = await setup(client, session, fake_registry)
    assert setup_values["connection"]["status"] == "pending"
    assert setup_values["rules_url"].endswith(f"/edge/rules/{setup_values['site_token']}")
    assert setup_values["route"] == "shop.example/*"
    assert len(setup_values["signing_secret"]) == 64
    # calling again keeps the values; rotate=true changes them
    again = (await client.put(f"/sites/{site.id}/deploy/connections/cloudflare", headers=headers)).json()
    assert (
        again["site_token"] == setup_values["site_token"]
        and again["signing_secret"] == setup_values["signing_secret"]
    )
    # the secret never appears in the connection list
    listing = await client.get(f"/sites/{site.id}/deploy/connections", headers=headers)
    assert setup_values["signing_secret"] not in listing.text and listing.json()["deploy_method"] is None

    # Worker not installed -> test fails, stays pending, cannot be chosen
    monkeypatch.setattr(connections, "default_fetcher", lambda: EdgeFetcher(None))
    bad = (await client.post(f"/sites/{site.id}/deploy/connections/cloudflare/test", headers=headers)).json()
    assert (
        not bad["ok"] and "Worker was not seen" in bad["message"] and bad["connection"]["status"] == "pending"
    )
    chosen = await client.put(
        f"/sites/{site.id}/deploy/method", json={"method": "cloudflare"}, headers=headers
    )
    assert chosen.status_code == 409

    # Worker answering -> connected, and it becomes the method (the only one)
    monkeypatch.setattr(connections, "default_fetcher", lambda: EdgeFetcher("ready"))
    good = (await client.post(f"/sites/{site.id}/deploy/connections/cloudflare/test", headers=headers)).json()
    assert good["ok"] and good["connection"]["status"] == "connected" and good["connection"]["chosen"]

    rotated = (
        await client.put(f"/sites/{site.id}/deploy/connections/cloudflare?rotate=true", headers=headers)
    ).json()
    assert rotated["signing_secret"] != setup_values["signing_secret"]
    assert (
        rotated["connection"]["status"] == "pending"
    )  # must be re-tested after the Worker got the new secret


async def test_rules_endpoint_is_signed_and_follows_deployments(
    client: AsyncClient, session: AsyncSession, fake_registry: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    site, headers, values = await setup(client, session, fake_registry)
    monkeypatch.setattr(connections, "default_fetcher", lambda: EdgeFetcher("ready"))
    await client.post(f"/sites/{site.id}/deploy/connections/cloudflare/test", headers=headers)
    web = FakeWebsite()
    monkeypatch.setattr(service, "default_fetcher", lambda: web)
    url = f"/edge/rules/{values['site_token']}"

    empty = await client.get(url)
    assert empty.status_code == 200 and empty.headers["cache-control"] == "no-store"
    data = verify_rules(empty.text, empty.headers[SIGNATURE_HEADER], values["signing_secret"])
    assert (
        data is not None
        and data["pages"] == {}
        and data["enabled"] is True
        and data["host"] == "shop.example"
    )

    action = await make_action(session, site)
    dep = (
        await client.post(f"/sites/{site.id}/deploy", json={"action_ids": [str(action.id)]}, headers=headers)
    ).json()
    assert dep["deployments"][0]["status"] == "applied", dep

    live = await client.get(url)
    data = verify_rules(live.text, live.headers[SIGNATURE_HEADER], values["signing_secret"])
    assert data is not None
    assert data["pages"]["/page"] == [{"op": "title", "value": "A much better title for the coffee page"}]
    # a Worker holding another secret would reject it
    assert verify_rules(live.text, live.headers[SIGNATURE_HEADER], "x" * 64) is None

    # kill switch: nothing is served, the deployments stay on record
    off = await client.post(
        f"/sites/{site.id}/deploy/connections/cloudflare/kill-switch", json={"off": True}, headers=headers
    )
    assert off.status_code == 200 and off.json()["meta"]["kill_switch"] is True
    killed = verify_rules(
        (r := await client.get(url)).text, r.headers[SIGNATURE_HEADER], values["signing_secret"]
    )
    assert killed is not None and killed["enabled"] is False and killed["pages"] == {}
    await client.post(
        f"/sites/{site.id}/deploy/connections/cloudflare/kill-switch", json={"off": False}, headers=headers
    )

    # rollback removes the page from the rules
    rb = await client.post(
        f"/sites/{site.id}/deploy/rollback",
        json={"deployment_ids": [dep["deployments"][0]["id"]]},
        headers=headers,
    )
    assert rb.status_code == 200 and rb.json()["reverted"][0]["status"] == "reverted"
    after = verify_rules(
        (r := await client.get(url)).text, r.headers[SIGNATURE_HEADER], values["signing_secret"]
    )
    assert after is not None and after["pages"] == {}
    # idempotent
    again = await client.post(
        f"/sites/{site.id}/deploy/rollback",
        json={"deployment_ids": [dep["deployments"][0]["id"]]},
        headers=headers,
    )
    assert again.status_code == 200 and again.json()["reverted"] == [] and len(again.json()["skipped"]) == 1


async def test_unknown_tokens_get_404_and_nothing_else(client: AsyncClient) -> None:
    for token in ("short", "x" * 30, "x" * 200):
        assert (await client.get(f"/edge/rules/{token}")).status_code == 404


async def test_pending_connections_are_not_deployed_to(
    client: AsyncClient, session: AsyncSession, fake_registry: Any
) -> None:
    site, headers, _ = await setup(client, session, fake_registry)
    action = await make_action(session, site)
    r = await client.post(f"/sites/{site.id}/deploy", json={"action_ids": [str(action.id)]}, headers=headers)
    assert r.status_code == 409 and "No deployer is connected" in r.json()["detail"]


async def test_disconnect_refused_while_changes_are_live(
    client: AsyncClient, session: AsyncSession, fake_registry: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    site, headers, _ = await setup(client, session, fake_registry)
    monkeypatch.setattr(connections, "default_fetcher", lambda: EdgeFetcher("ready"))
    await client.post(f"/sites/{site.id}/deploy/connections/cloudflare/test", headers=headers)
    monkeypatch.setattr(service, "default_fetcher", lambda: FakeWebsite())
    action = await make_action(session, site)
    dep = (
        await client.post(f"/sites/{site.id}/deploy", json={"action_ids": [str(action.id)]}, headers=headers)
    ).json()
    assert (
        await client.delete(f"/sites/{site.id}/deploy/connections/cloudflare", headers=headers)
    ).status_code == 409
    await client.post(
        f"/sites/{site.id}/deploy/rollback",
        json={"deployment_ids": [dep["deployments"][0]["id"]]},
        headers=headers,
    )
    assert (
        await client.delete(f"/sites/{site.id}/deploy/connections/cloudflare", headers=headers)
    ).status_code == 204


# ---- the pretend Worker used by the dev smoke run ----------------------------------------------------------------------
def test_fake_site_acts_like_the_worker() -> None:
    secret = "k" * 64
    body = build_rules(
        host="shop.test",
        version=1,
        issued_at=2_000_000_000,
        enabled=True,
        pages={"/grinders": [{"op": "title", "value": "Best grinders"}]},
    )
    sig = sign_body(body, secret)
    import time

    real_time = time.time
    time.time = lambda: 2_000_000_100.0  # type: ignore[assignment]
    try:
        status, html, state = fake_site.render("/grinders/", fake_site.PAGES, lambda: (body, sig), secret)
        assert (status, state) == (200, "on") and "<title>Best grinders</title>" in html
        status, html, state = fake_site.render("/", fake_site.PAGES, lambda: (body, sig), secret)
        assert state == "ready" and "Harbor Coffee" in html
        status, html, state = fake_site.render("/grinders", fake_site.PAGES, lambda: (body, "bad"), secret)
        assert state == "pass" and "<title>Grinders</title>" in html
    finally:
        time.time = real_time  # type: ignore[assignment]
