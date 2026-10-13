"""4.4 (mocked HTTP): request building, error messages, address checks, connection flow, unsupported links."""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest
import respx
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from searchos.deploy import service, wordpress
from searchos.deploy.base import DeployerError
from searchos.deploy.wordpress import WordPressDeployer, check_site_url, removal_body, request_body
from searchos.models import Site
from tests.deploy_fakes import FakeWebsite, fake_registry, make_action, verify_site  # noqa: F401
from tests.deploy_helpers import owner_with_site

BASE = "https://shop.example"
ROUTE = f"{BASE}/wp-json/searchos/v1"
STATUS = {"plugin_version": "0.1.0", "wp_version": "6.7", "seo_plugin": "yoast", "user": "ed"}


@pytest.fixture(autouse=True)
def public_hosts(monkeypatch: pytest.MonkeyPatch) -> None:
    async def yes(host: str) -> bool:
        return True

    monkeypatch.setattr(wordpress, "_host_is_public", yes)


def site_named(domain: str = "shop.example") -> Site:
    return Site(domain=domain)


# ---- pure ----------------------------------------------------------------------------------------------------
def test_request_body_collects_fields_and_later_ops_win() -> None:
    body = request_body(
        "https://shop.example/p",
        [
            {"op": "title", "value": "One"},
            {"op": "title", "value": "Two"},
            {"op": "meta_description", "value": "D"},
            {"op": "canonical", "value": "https://shop.example/p"},
            {"op": "alt", "src": "https://shop.example/a.png", "value": "A"},
            {"op": "alt", "src": "https://shop.example/a.png", "value": "B"},
            {"op": "jsonld", "id": "searchos-1", "value": {"@type": "Organization"}, "replace": None},
        ],
    )
    assert body["set"] == {"title": "Two", "meta_description": "D", "canonical": "https://shop.example/p"}
    assert body["alt"]["put"] == [{"src": "https://shop.example/a.png", "value": "B"}]
    assert body["jsonld"]["put"][0]["id"] == "searchos-1"
    assert "clear" not in body


def test_removal_clears_then_restores_what_other_live_deployments_want() -> None:
    removed = [
        {"op": "title", "value": "Newer"},
        {"op": "jsonld", "id": "searchos-2", "value": {}, "replace": None},
    ]
    remaining = [
        {"op": "title", "value": "Older"},
        {"op": "meta_description", "value": "untouched"},
        {"op": "jsonld", "id": "searchos-1", "value": {"@type": "Organization"}, "replace": None},
    ]
    body = removal_body("https://shop.example/p", removed, remaining)
    assert body["clear"] == ["title"]
    assert body["set"] == {"title": "Older"}  # the older deployment's title comes back, not the original
    assert body["jsonld"]["remove"] == ["searchos-2"] and body["jsonld"]["put"] == []
    assert "meta_description" not in body["set"]


def test_address_must_be_https_and_on_the_site() -> None:
    s = site_named()
    assert check_site_url(s, "https://shop.example/blog/", allow_private=False) == "https://shop.example/blog"
    assert check_site_url(s, "https://www.shop.example", allow_private=False) == "https://www.shop.example"
    for bad, msg in [
        ("http://shop.example", "unencrypted"),
        ("https://evil.example", "must be on shop.example"),
        ("https://shop.example.evil.example", "must be on shop.example"),
        ("https://user:pw@shop.example", "Do not put"),
        ("ftp://shop.example", "starting with https"),
        ("not a url", "starting with https"),
    ]:
        with pytest.raises(DeployerError, match=msg):
            check_site_url(s, bad, allow_private=False)
    assert check_site_url(s, "http://shop.example:8080", allow_private=True) == "http://shop.example:8080"


# ---- the HTTP client and the connection flow ----------------------------------------------------------------------
async def connect_wp(
    client: AsyncClient, headers: dict[str, str], site_id: str, **over: Any
) -> httpx.Response:
    body = {"site_url": BASE, "username": "ed", "application_password": "abcd efgh ijkl mnop", **over}
    return await client.put(f"/sites/{site_id}/deploy/connections/wordpress", json=body, headers=headers)


@respx.mock(assert_all_called=False)
async def test_connect_ok_and_secrets_are_not_returned(
    client: AsyncClient, session: AsyncSession, respx_mock: Any
) -> None:
    headers, site_id, _ = await owner_with_site(client)
    route = respx_mock.get(f"{ROUTE}/status").mock(return_value=httpx.Response(200, json=STATUS))
    r = await connect_wp(client, headers, site_id)
    assert r.status_code == 200, r.text
    out = r.json()
    assert (
        out["ok"] and "Yoast SEO is active" in out["message"] and out["connection"]["status"] == "connected"
    )
    assert "abcd" not in r.text and "application_password" not in r.text
    sent = route.calls.last.request
    assert sent.headers["authorization"].startswith("Basic ")
    listing = await client.get(f"/sites/{site_id}/deploy/connections", headers=headers)
    assert "abcd" not in listing.text and listing.json()["deploy_method"] == "wordpress"
    assert listing.json()["connections"][0]["meta"]["username"] == "ed"


@respx.mock(assert_all_called=False)
async def test_wrong_password_plugin_missing_and_fallback_route(client: AsyncClient, respx_mock: Any) -> None:
    headers, site_id, _ = await owner_with_site(client)
    respx_mock.get(f"{ROUTE}/status").mock(
        return_value=httpx.Response(401, json={"code": "incorrect_password", "message": "x"})
    )
    r = await connect_wp(client, headers, site_id)
    assert not r.json()["ok"] and "did not accept the username" in r.json()["message"]
    assert r.json()["connection"]["status"] == "pending"

    respx_mock.get(f"{ROUTE}/status").mock(return_value=httpx.Response(404, json={"code": "rest_no_route"}))
    respx_mock.get(f"{BASE}/", params={"rest_route": "/searchos/v1/status"}).mock(
        return_value=httpx.Response(404, json={"code": "rest_no_route"})
    )
    r = await connect_wp(client, headers, site_id)
    assert "not installed or not active" in r.json()["message"]

    # plain permalinks: /wp-json is not routed, ?rest_route= works
    respx_mock.get(f"{ROUTE}/status").mock(return_value=httpx.Response(404, json={"code": "rest_no_route"}))
    respx_mock.get(f"{BASE}/", params={"rest_route": "/searchos/v1/status"}).mock(
        return_value=httpx.Response(200, json=STATUS)
    )
    r = await connect_wp(client, headers, site_id)
    assert r.json()["ok"]


async def test_bad_addresses_are_refused_with_422(client: AsyncClient) -> None:
    headers, site_id, _ = await owner_with_site(client)
    for url in ("http://shop.example", "https://other.example"):
        r = await connect_wp(client, headers, site_id, site_url=url)
        assert r.status_code == 422, (url, r.text)


@respx.mock(assert_all_called=False)
async def test_deploy_sends_one_request_per_page_and_internal_links_are_declined(
    client: AsyncClient,
    session: AsyncSession,
    fake_registry: Any,
    monkeypatch: pytest.MonkeyPatch,
    respx_mock: Any,
) -> None:
    headers, site_id, _ = await owner_with_site(client)
    site = await verify_site(session, site_id)
    fake_registry.register(WordPressDeployer())
    respx_mock.get(f"{ROUTE}/status").mock(return_value=httpx.Response(200, json=STATUS))
    assert (await connect_wp(client, headers, site_id)).json()["ok"]
    web = FakeWebsite()
    monkeypatch.setattr(service, "default_fetcher", lambda: web)
    put = respx_mock.put(f"{ROUTE}/overrides").mock(
        return_value=httpx.Response(
            200, json={"target": {"type": "post", "post_id": 7}, "before": {}, "after": {}}
        )
    )
    title = await make_action(session, site, "rewrite_title")
    meta = await make_action(
        session, site, "rewrite_meta", field="meta_description", current="Old description", proposed="D" * 130
    )
    links = await make_action(
        session,
        site,
        "add_internal_links",
        field="internal_links",
        current=None,
        proposed={"links": [{"to": "https://shop.example/y", "anchor": "coffee grinders"}]},
    )
    r = await client.post(
        f"/sites/{site_id}/deploy",
        json={"action_ids": [str(a.id) for a in (title, meta, links)]},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert [d["status"] for d in body["deployments"]] == ["applied", "applied"]
    assert len(body["skipped"]) == 1 and "cannot insert internal links" in body["skipped"][0]["reason"]
    assert put.call_count == 1  # both changes to the page went in one request
    sent = put.calls.last.request.content.decode()
    assert '"title":"A much better title for the coffee page"' in sent
    assert body["deployments"][0]["pr_url"] is None


@respx.mock(assert_all_called=False)
async def test_a_failing_wordpress_marks_the_deployment_failed_and_the_action_stays_approved(
    client: AsyncClient,
    session: AsyncSession,
    fake_registry: Any,
    monkeypatch: pytest.MonkeyPatch,
    respx_mock: Any,
) -> None:
    headers, site_id, _ = await owner_with_site(client)
    site = await verify_site(session, site_id)
    fake_registry.register(WordPressDeployer())
    respx_mock.get(f"{ROUTE}/status").mock(return_value=httpx.Response(200, json=STATUS))
    await connect_wp(client, headers, site_id)
    monkeypatch.setattr(service, "default_fetcher", lambda: FakeWebsite())
    respx_mock.put(f"{ROUTE}/overrides").mock(
        return_value=httpx.Response(
            404, json={"code": "searchos_unknown_url", "message": "That address is not a page."}
        )
    )
    action = await make_action(session, site)
    r = await client.post(f"/sites/{site_id}/deploy", json={"action_ids": [str(action.id)]}, headers=headers)
    dep = r.json()["deployments"][0]
    assert dep["status"] == "failed" and "not a page" in dep["error"]
    await session.refresh(action)
    assert action.status.value == "approved"
    assert uuid.UUID(dep["id"])
