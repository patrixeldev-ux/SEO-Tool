"""Audit API: starting, following and reading an audit, role checks and organisation isolation."""

from __future__ import annotations

import csv
import io
import uuid
from typing import Any

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from searchos.api.audits import _csv_safe
from searchos.audit.catalog import load_catalog
from searchos.core.db import get_engine
from searchos.devtools.fixture_server import FixtureServer
from searchos.models import Crawl, CrawlStatus, Issue, Job, Membership, Page, Role
from searchos.worker.crawl_job import crawl_handler
from searchos.worker.jobs import run_job
from tests.conftest import auth, register


@pytest.fixture
def enqueued(monkeypatch: pytest.MonkeyPatch) -> list[uuid.UUID]:
    """Replace the Celery hand-off: tests run jobs themselves and must never reach a real website."""
    sent: list[uuid.UUID] = []

    async def fake_enqueue(_session: Any, _crawl: Crawl, job: Job) -> bool:
        sent.append(job.id)
        return True

    monkeypatch.setattr("searchos.api.audits.enqueue_crawl", fake_enqueue)
    return sent


async def make_site(client: AsyncClient, tokens: dict[str, Any], domain: str = "shop.example.com") -> str:
    resp = await client.post("/sites", json={"domain": domain}, headers=auth(tokens))
    assert resp.status_code == 201
    return str(resp.json()["id"])


async def run_against_fixture(session: AsyncSession, server: FixtureServer, audit_id: str) -> None:
    """Point the queued crawl at the local fixture server (what the dev helper does) and run its job."""
    crawl = await session.get(Crawl, uuid.UUID(audit_id))
    assert crawl is not None and crawl.job_id is not None
    job = await session.get(Job, crawl.job_id)
    assert job is not None
    crawl.start_url = server.base_url + "/"
    job.payload = {
        **job.payload,
        "allowed_hosts": [server.netloc],
        "start_url_candidates": [],
        "allow_private_hosts": True,
        "rate_per_host": 1000,
        "js_check": False,
    }
    await session.commit()
    await run_job(job.id, crawl_handler, engine=get_engine())
    session.expire_all()


async def finished_audit(
    client: AsyncClient, session: AsyncSession, server: FixtureServer, tokens: dict[str, Any], site_id: str
) -> dict[str, Any]:
    resp = await client.post(f"/sites/{site_id}/audits", json={}, headers=auth(tokens))
    assert resp.status_code == 202, resp.text
    await run_against_fixture(session, server, resp.json()["id"])
    done = await client.get(f"/audits/{resp.json()['id']}", headers=auth(tokens))
    assert done.json()["status"] == "done", done.json()
    body: dict[str, Any] = done.json()
    return body


# ---- starting ---------------------------------------------------------------------------------------
async def test_new_site_has_no_audits(client: AsyncClient) -> None:
    tokens = await register(client, "a@example.com")
    site_id = await make_site(client, tokens)
    resp = await client.get(f"/sites/{site_id}/audits", headers=auth(tokens))
    assert resp.status_code == 200
    assert resp.json() == []


async def test_start_derives_the_urls_from_the_site(
    client: AsyncClient, session: AsyncSession, enqueued: list[uuid.UUID]
) -> None:
    tokens = await register(client, "a@example.com")
    resp = await client.post(
        "/sites", json={"domain": "https://www.Shop.example.com/x"}, headers=auth(tokens)
    )
    site_id = resp.json()["id"]

    started = await client.post(f"/sites/{site_id}/audits", json={}, headers=auth(tokens))
    assert started.status_code == 202
    body = started.json()
    assert body["status"] == "queued" and body["progress"] == 0
    assert body["page_limit"] == 500
    assert body["start_url"] == "https://www.shop.example.com/"
    assert enqueued == [uuid.UUID(body["job_id"])]
    job = await session.get(Job, uuid.UUID(body["job_id"]))
    assert job is not None and job.type == "crawl"
    assert job.payload["start_url_candidates"] == [
        "https://www.shop.example.com/",
        "http://www.shop.example.com/",
    ]
    assert job.payload["allowed_hosts"] == ["shop.example.com", "www.shop.example.com"]
    assert "allow_private_hosts" not in job.payload

    listed = (await client.get(f"/sites/{site_id}/audits", headers=auth(tokens))).json()
    assert [a["id"] for a in listed] == [body["id"]]


async def test_page_limit_is_validated_and_no_other_input_is_accepted(
    client: AsyncClient, enqueued: list[uuid.UUID]
) -> None:
    tokens = await register(client, "a@example.com")
    site_id = await make_site(client, tokens)
    url = f"/sites/{site_id}/audits"
    for bad in ({"page_limit": 0}, {"page_limit": 10_001}, {"page_limit": "lots"}):
        assert (await client.post(url, json=bad, headers=auth(tokens))).status_code == 422
    for sneaky in ({"start_url": "http://169.254.169.254/"}, {"allowed_hosts": ["internal"]}):
        assert (await client.post(url, json=sneaky, headers=auth(tokens))).status_code == 422
    assert enqueued == []
    ok = await client.post(url, json={"page_limit": 25}, headers=auth(tokens))
    assert ok.status_code == 202 and ok.json()["page_limit"] == 25


async def test_second_audit_is_refused_while_one_is_running_and_allowed_after_cancel(
    client: AsyncClient, enqueued: list[uuid.UUID]
) -> None:
    tokens = await register(client, "a@example.com")
    site_id = await make_site(client, tokens)
    url = f"/sites/{site_id}/audits"
    first = await client.post(url, json={}, headers=auth(tokens))
    assert first.status_code == 202
    second = await client.post(url, json={}, headers=auth(tokens))
    assert second.status_code == 409

    cancelled = await client.post(f"/audits/{first.json()['id']}/cancel", headers=auth(tokens))
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
    again = await client.post(f"/audits/{first.json()['id']}/cancel", headers=auth(tokens))
    assert again.status_code == 409
    assert (await client.post(url, json={}, headers=auth(tokens))).status_code == 202
    assert len(enqueued) == 2


async def test_a_broker_failure_marks_the_audit_failed(
    client: AsyncClient, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(_job_id: str) -> None:
        raise ConnectionError("redis is down")

    monkeypatch.setattr("searchos.worker.tasks.crawl_task.delay", broken)
    tokens = await register(client, "a@example.com")
    site_id = await make_site(client, tokens)
    resp = await client.post(f"/sites/{site_id}/audits", json={}, headers=auth(tokens))
    assert resp.status_code == 503
    crawl = (await session.scalars(sa.select(Crawl))).one()
    assert crawl.status is CrawlStatus.failed and "queue" in (crawl.error or "")


async def test_an_abandoned_running_audit_does_not_block_new_ones(
    client: AsyncClient, session: AsyncSession, enqueued: list[uuid.UUID]
) -> None:
    tokens = await register(client, "a@example.com")
    site_id = await make_site(client, tokens)
    first = (await client.post(f"/sites/{site_id}/audits", json={}, headers=auth(tokens))).json()
    await session.execute(
        sa.update(Crawl)
        .where(Crawl.id == uuid.UUID(first["id"]))
        .values(status=CrawlStatus.running, created_at=sa.text("now() - interval '5 hours'"))
    )
    await session.commit()
    assert (await client.post(f"/sites/{site_id}/audits", json={}, headers=auth(tokens))).status_code == 202
    old = (await client.get(f"/audits/{first['id']}", headers=auth(tokens))).json()
    assert old["status"] == "failed" and "interrupted" in old["error"]


# ---- reading a finished audit -----------------------------------------------------------------------
async def test_finished_audit_score_issues_pages_and_export(
    client: AsyncClient, session: AsyncSession, fixture_site: FixtureServer, enqueued: list[uuid.UUID]
) -> None:
    tokens = await register(client, "a@example.com")
    site_id = await make_site(client, tokens)
    audit = await finished_audit(client, session, fixture_site, tokens, site_id)

    assert audit["progress"] == 100
    assert audit["score"] is not None and 0 < audit["score"] < 100
    assert set(audit["subscores"]) == {
        "indexability",
        "on_page",
        "structured_data",
        "performance",
        "ai_readiness",
    }
    assert audit["pages_crawled"] == 53
    assert audit["issue_counts"]["total"] >= 30
    assert "checks" not in audit["issue_counts"]

    issues = (await client.get(f"/audits/{audit['id']}/issues", headers=auth(tokens))).json()
    groups = {g["check_id"]: g for g in issues["groups"]}
    assert sum(g["count"] for g in groups.values()) == audit["issue_counts"]["total"]
    missing_title = groups["TITLE_MISSING"]
    assert missing_title["severity"] == load_catalog()["TITLE_MISSING"].severity
    assert missing_title["title"] and missing_title["why_it_matters"] and missing_title["how_to_fix"]
    assert missing_title["count"] == len(missing_title["items"]) >= 1
    assert all(i["url"].startswith(fixture_site.base_url) for i in missing_title["items"])
    # site-wide issues have no URL
    assert groups["HTTPS_MISSING"]["items"][0]["url"] is None
    # most severe first
    order = ["critical", "high", "medium", "low", "info"]
    ranks = [order.index(g["severity"]) for g in issues["groups"]]
    assert ranks == sorted(ranks)
    assert issues["passed"] and all(p["check_id"] not in groups for p in issues["passed"])

    pages = (await client.get(f"/audits/{audit['id']}/pages?limit=200", headers=auth(tokens))).json()
    assert pages["total"] == 53 and len(pages["items"]) == 53
    only_bad = (
        await client.get(f"/audits/{audit['id']}/pages?with_issues=true&limit=200", headers=auth(tokens))
    ).json()
    assert 0 < only_bad["total"] < 53 and all(p["issue_count"] > 0 for p in only_bad["items"])
    searched = (await client.get(f"/audits/{audit['id']}/pages?q=level-5", headers=auth(tokens))).json()
    assert [p["url"] for p in searched["items"]] == [fixture_site.base_url + "/deep/level-5.html"]
    like = (await client.get(f"/audits/{audit['id']}/pages?q=%25", headers=auth(tokens))).json()
    assert like["total"] == 0  # '%' is searched literally

    deep = searched["items"][0]
    assert deep["depth"] == 5 and deep["issue_count"] >= 1
    detail = (await client.get(f"/audits/{audit['id']}/pages/{deep['id']}", headers=auth(tokens))).json()
    assert detail["url"] == deep["url"] and detail["depth"] == 5
    assert [i["check_id"] for i in detail["issues"]] == ["DEEP_PAGE"]
    assert detail["issues"][0]["how_to_fix"]
    assert detail["inlinks"] and detail["inlinks"][0]["url"].endswith("/deep/level-4.html")
    assert detail["outlinks_total"] >= len(detail["outlinks"]) >= 1

    exported = await client.get(f"/audits/{audit['id']}/export.csv", headers=auth(tokens))
    assert exported.status_code == 200
    assert exported.headers["content-type"].startswith("text/csv")
    assert "attachment" in exported.headers["content-disposition"]
    rows = list(csv.reader(io.StringIO(exported.text.lstrip("﻿"))))
    assert rows[0] == ["severity", "check_id", "category", "issue", "url", "details", "how_to_fix"]
    assert len(rows) - 1 == audit["issue_counts"]["total"]
    assert any(r[1] == "HTTPS_MISSING" and r[4] == "(whole site)" for r in rows[1:])


def test_csv_cells_cannot_start_a_formula() -> None:
    assert _csv_safe('=HYPERLINK("http://evil")') == '\'=HYPERLINK("http://evil")'
    assert _csv_safe("+1") == "'+1" and _csv_safe("-1") == "'-1" and _csv_safe("@x") == "'@x"
    assert _csv_safe("https://a.example/") == "https://a.example/"
    assert _csv_safe(None) == "" and _csv_safe("") == ""


async def test_changes_since_the_previous_audit(
    client: AsyncClient, session: AsyncSession, fixture_site: FixtureServer, enqueued: list[uuid.UUID]
) -> None:
    tokens = await register(client, "a@example.com")
    site_id = await make_site(client, tokens)
    first = await finished_audit(client, session, fixture_site, tokens, site_id)
    nothing = (await client.get(f"/audits/{first['id']}/changes", headers=auth(tokens))).json()
    assert nothing["previous_audit_id"] is None and nothing["new"] == [] and nothing["fixed"] == []

    # Make the first audit differ from what the second one will find: one issue it never had (so it counts
    # as new later) and one extra issue on a clean page (so it counts as fixed later).
    first_id = uuid.UUID(first["id"])
    removed = (
        await session.scalars(
            sa.select(Issue).where(Issue.crawl_id == first_id, Issue.check_id == "DEEP_PAGE")
        )
    ).one()
    await session.delete(removed)
    clean = await session.scalar(
        sa.select(Page).where(
            Page.crawl_id == first_id, Page.normalised_url == fixture_site.base_url + "/about.html"
        )
    )
    assert clean is not None
    session.add(
        Issue(
            crawl_id=first_id, check_id="H1_MISSING", page_id=clean.id, severity="medium", category="on_page"
        )
    )
    await session.commit()

    second = await finished_audit(client, session, fixture_site, tokens, site_id)
    changes = (await client.get(f"/audits/{second['id']}/changes", headers=auth(tokens))).json()
    assert changes["previous_audit_id"] == first["id"]
    assert [(c["check_id"], c["url"]) for c in changes["new"]] == [
        ("DEEP_PAGE", fixture_site.base_url + "/deep/level-5.html")
    ]
    assert [(c["check_id"], c["url"]) for c in changes["fixed"]] == [
        ("H1_MISSING", fixture_site.base_url + "/about.html")
    ]
    assert changes["persisting_count"] == second["issue_counts"]["total"] - 1
    assert changes["score"] == second["score"]
    assert changes["score_delta"] == round(second["score"] - first["score"], 1)
    assert changes["new"][0]["title"] and changes["new"][0]["severity"]


# ---- who may do what --------------------------------------------------------------------------------
async def test_viewer_can_read_but_not_start_or_cancel(
    client: AsyncClient, session: AsyncSession, enqueued: list[uuid.UUID]
) -> None:
    owner = await register(client, "owner@example.com")
    site_id = await make_site(client, owner)
    started = (await client.post(f"/sites/{site_id}/audits", json={}, headers=auth(owner))).json()

    viewer = await register(client, "viewer@example.com", org_name="Viewer org")
    session.add(
        Membership(
            org_id=uuid.UUID(owner["orgs"][0]["id"]),
            user_id=uuid.UUID(viewer["user"]["id"]),
            role=Role.client_viewer,
        )
    )
    await session.commit()
    headers = auth(viewer, owner["orgs"][0]["id"])
    assert (await client.get(f"/sites/{site_id}/audits", headers=headers)).status_code == 200
    assert (await client.get(f"/audits/{started['id']}", headers=headers)).status_code == 200
    assert (await client.post(f"/sites/{site_id}/audits", json={}, headers=headers)).status_code == 403
    assert (await client.post(f"/audits/{started['id']}/cancel", headers=headers)).status_code == 403


async def test_another_organisation_cannot_see_or_touch_an_audit(
    client: AsyncClient, session: AsyncSession, fixture_site: FixtureServer, enqueued: list[uuid.UUID]
) -> None:
    owner = await register(client, "owner@example.com")
    site_id = await make_site(client, owner)
    audit = await finished_audit(client, session, fixture_site, owner, site_id)
    page_id = (await client.get(f"/audits/{audit['id']}/pages", headers=auth(owner))).json()["items"][0]["id"]

    other = await register(client, "other@example.com", org_name="Other org")
    h = auth(other)
    for path in (
        f"/audits/{audit['id']}",
        f"/audits/{audit['id']}/issues",
        f"/audits/{audit['id']}/pages",
        f"/audits/{audit['id']}/pages/{page_id}",
        f"/audits/{audit['id']}/export.csv",
        f"/audits/{audit['id']}/changes",
        f"/sites/{site_id}/audits",
    ):
        assert (await client.get(path, headers=h)).status_code == 404, path
    assert (await client.post(f"/audits/{audit['id']}/cancel", headers=h)).status_code == 404
    assert (await client.post(f"/sites/{site_id}/audits", json={}, headers=h)).status_code == 404

    # a page id from another audit of the same org does not resolve either
    second_site = await make_site(client, other, "other.example.com")
    mine = await client.post(f"/sites/{second_site}/audits", json={}, headers=h)
    assert (await client.get(f"/audits/{mine.json()['id']}/pages/{page_id}", headers=h)).status_code == 404

    unauth = await client.get(f"/audits/{audit['id']}")
    assert unauth.status_code == 401
