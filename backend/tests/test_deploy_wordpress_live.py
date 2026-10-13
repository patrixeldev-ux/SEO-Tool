"""4.4 integration: a real WordPress with the SearchOS Connector plugin active.

Runs only when WP_TEST_URL (+ WP_TEST_USER, WP_TEST_APP_PASSWORD) are set - the CI job `wordpress` starts the
official WordPress + MariaDB images, installs the plugin and creates the application password with wp-cli;
`wp-plugin/tests/local-wp.sh` does the same on a developer machine with PHP's built-in server and SQLite.
Also needs DEPLOY_ALLOW_PRIVATE_HOSTS=true (the test site is on localhost).

deploy -> verify (raw HTML, no JavaScript) -> rollback -> the original page is back, tag for tag.
"""

from __future__ import annotations

import os
import re
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from searchos.deploy import htmlview
from searchos.deploy.verify import verify_by_id
from tests.deploy_fakes import make_action, verify_site
from tests.deploy_helpers import owner_with_site

WP_URL = os.environ.get("WP_TEST_URL", "").rstrip("/")
pytestmark = [
    pytest.mark.skipif(not WP_URL, reason="WP_TEST_URL is not set (real WordPress needed)"),
    pytest.mark.skipif(
        os.environ.get("DEPLOY_ALLOW_PRIVATE_HOSTS", "").lower() != "true",
        reason="DEPLOY_ALLOW_PRIVATE_HOSTS=true is needed to reach a test site on localhost",
    ),
]

# With the Yoast test double active the page already has an Organization inside a @graph block; the action
# replaces just that node and the rest of the graph must survive.
FAKE_SEO = os.environ.get("WP_TEST_FAKE_SEO", "") == "yoast"
OLD_ORG = {"@type": "Organization", "name": "Old org"}
POST_PATH = os.environ.get("WP_TEST_POST_PATH", "/coffee-grinders/")


def get_raw(url: str) -> str:
    r = httpx.get(url, timeout=30, follow_redirects=True)
    assert r.status_code == 200, (url, r.status_code)
    return r.text


def counts(html: str) -> dict[str, int]:
    return {
        "description": len(re.findall(r'<meta\s+name=["\']description["\']', html, re.I)),
        "canonical": len(re.findall(r'<link\s+rel=["\']canonical["\']', html, re.I)),
        "title": len(re.findall(r"<title\b", html, re.I)),
    }


async def test_wordpress_deploy_verify_rollback(client: AsyncClient, session: AsyncSession) -> None:
    host = urlsplit(WP_URL).hostname or ""
    headers, site_id, _ = await owner_with_site(client, domain=host)
    site = await verify_site(session, site_id)
    page_url = f"{WP_URL}{POST_PATH}"
    original = get_raw(page_url)
    tree_before = htmlview.parse(original)
    url_for_model = page_url
    image = re.search(r'<img[^>]+src=["\']([^"\']+/uploads/[^"\']+)["\']', original)
    assert image, "the test post needs an image from the media library"
    image_src = urljoin(page_url, image.group(1))

    connect = await client.put(
        f"/sites/{site_id}/deploy/connections/wordpress",
        json={
            "site_url": WP_URL,
            "username": os.environ["WP_TEST_USER"],
            "application_password": os.environ["WP_TEST_APP_PASSWORD"],
        },
        headers=headers,
    )
    assert connect.status_code == 200, connect.text
    assert connect.json()["ok"], connect.json()
    assert connect.json()["connection"]["status"] == "connected"
    assert os.environ["WP_TEST_APP_PASSWORD"] not in connect.text

    new_title = "Best burr coffee grinders for home brewing 2026"
    new_desc = "We tested burr coffee grinders for home brewing and ranked the best ones for espresso and filter coffee."
    new_canon = page_url
    org = {
        "@context": "https://schema.org",
        "@type": "Organization",
        "name": "Harbor Coffee",
        "url": WP_URL + "/",
    }
    actions: list[Any] = [
        await make_action(
            session,
            site,
            "rewrite_title",
            url=url_for_model,
            field="title",
            current=htmlview.read_title(tree_before),
            proposed=new_title,
        ),
        await make_action(
            session,
            site,
            "rewrite_meta",
            url=url_for_model,
            field="meta_description",
            current=htmlview.read_meta_description(tree_before),
            proposed=new_desc,
        ),
        await make_action(
            session,
            site,
            "fix_canonical",
            url=url_for_model,
            field="canonical",
            current=htmlview.read_canonical(tree_before, page_url),
            proposed=new_canon,
        ),
        await make_action(
            session,
            site,
            "add_alt",
            url=url_for_model,
            field=f"alt:{image_src}",
            current=htmlview.read_alt(tree_before, page_url, image_src),
            proposed="A burr grinder on a counter",
        ),
        (
            await make_action(
                session,
                site,
                "fix_jsonld",
                url=url_for_model,
                field="jsonld:Organization",
                current=OLD_ORG,
                proposed=org,
            )
            if FAKE_SEO
            else await make_action(
                session,
                site,
                "add_jsonld",
                url=url_for_model,
                field="jsonld:Organization",
                current=None,
                proposed=org,
            )
        ),
    ]
    resp = await client.post(
        f"/sites/{site_id}/deploy", json={"action_ids": [str(a.id) for a in actions]}, headers=headers
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert not body["skipped"], body["skipped"]
    assert [d["status"] for d in body["deployments"]] == ["applied"] * 5, body["deployments"]
    batch_id = body["batch"]["id"]

    # the page, as a crawler without JavaScript sees it
    deployed = get_raw(page_url)
    tree = htmlview.parse(deployed)
    assert htmlview.read_title(tree) == new_title
    assert htmlview.read_meta_description(tree) == new_desc
    assert htmlview.read_alt(tree, page_url, image_src) == "A burr grinder on a counter"
    assert htmlview.entity_present(htmlview.read_jsonld(tree).entities, org)
    # no tag was duplicated by the connector
    if FAKE_SEO:
        entities = htmlview.read_jsonld(tree).entities
        assert not htmlview.entity_present(entities, OLD_ORG)
        assert any(e.get("@type") == "WebPage" for e in entities), "the rest of the graph must survive"
    assert (
        counts(deployed)["description"] == 1
        and counts(deployed)["canonical"] == 1
        and counts(deployed)["title"] == 1
    )

    # verification (the same code the worker runs)
    for dep in body["deployments"]:
        out = await verify_by_id(session, __import__("uuid").UUID(dep["id"]))
        assert out.state == "verified", (dep["action_type"], out)
    listing = (await client.get(f"/sites/{site_id}/deploy/deployments", headers=headers)).json()
    assert {d["status"] for d in listing} == {"verified"}
    assert all(d["after_snapshot"] and d["before_snapshot"] is not None for d in listing)

    # rollback the whole batch
    rb = await client.post(f"/sites/{site_id}/deploy/batches/{batch_id}/rollback", headers=headers)
    assert rb.status_code == 200, rb.text
    assert len(rb.json()["reverted"]) == 5 and not rb.json()["skipped"]

    restored = get_raw(page_url)
    t2 = htmlview.parse(restored)
    assert htmlview.read_title(t2) == htmlview.read_title(tree_before)
    assert htmlview.read_meta_description(t2) == htmlview.read_meta_description(tree_before)
    assert htmlview.read_canonical(t2, page_url) == htmlview.read_canonical(tree_before, page_url)
    assert htmlview.read_alt(t2, page_url, image_src) == htmlview.read_alt(tree_before, page_url, image_src)
    assert not htmlview.entity_present(htmlview.read_jsonld(t2).entities, org)
    if FAKE_SEO:
        assert htmlview.entity_present(htmlview.read_jsonld(t2).entities, OLD_ORG)
    assert counts(restored) == counts(original)

    # idempotent
    again = await client.post(f"/sites/{site_id}/deploy/batches/{batch_id}/rollback", headers=headers)
    assert again.json()["reverted"] == []
    batches = (await client.get(f"/sites/{site_id}/deploy/batches", headers=headers)).json()
    assert batches[0]["status"] == "reverted"
