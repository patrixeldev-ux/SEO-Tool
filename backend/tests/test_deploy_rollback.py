"""4.8: rollback per deployment and per batch - exact restore, idempotent, audited, allowed without proof."""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from searchos.deploy import rollback, schedule, service
from searchos.models import BatchStatus, DeployBatch, Deployment, DeploymentEvent, DeploymentStatus
from searchos.planner.catalog import ActionStatus
from tests.deploy_fakes import (
    PAGE,
    URL,
    FakeDeployer,
    FakeWebsite,
    connect,
    fake_registry,  # noqa: F401  (pytest fixture)
    make_action,
    verify_site,
)
from tests.deploy_helpers import add_member, owner_with_site

OTHER = "https://shop.example/other"


@pytest.fixture(autouse=True)
def quiet(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(schedule, "enqueue_verifications", lambda ids, countdown=0: None)


async def deployed_batch(
    client: AsyncClient, session: AsyncSession, fake_registry: Any, monkeypatch: pytest.MonkeyPatch
) -> tuple[Any, FakeWebsite, FakeDeployer, dict[str, str], dict[str, Any]]:
    headers, site_id, _ = await owner_with_site(client)
    site = await verify_site(session, site_id)
    await connect(session, site)
    web = FakeWebsite({URL: PAGE, OTHER: PAGE})
    deployer = FakeDeployer(web)
    fake_registry.register(deployer)
    monkeypatch.setattr(service, "default_fetcher", lambda: web)
    monkeypatch.setattr(rollback, "default_fetcher", lambda: web)
    a = await make_action(session, site)
    b = await make_action(session, site, url=OTHER)
    resp = await client.post(
        f"/sites/{site.id}/deploy", json={"action_ids": [str(a.id), str(b.id)]}, headers=headers
    )
    assert resp.status_code == 201, resp.text
    return site, web, deployer, headers, resp.json()


async def test_rolling_back_one_deployment_restores_the_original_page(
    client: AsyncClient, session: AsyncSession, fake_registry: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    site, web, _, headers, body = await deployed_batch(client, session, fake_registry, monkeypatch)
    first, second = body["deployments"]
    assert "A much better title" in web.render(first["url"])
    resp = await client.post(
        f"/sites/{site.id}/deploy/rollback", json={"deployment_ids": [first["id"]]}, headers=headers
    )
    assert resp.status_code == 200, resp.text
    assert web.render(first["url"]) == PAGE  # byte for byte
    assert "A much better title" in web.render(second["url"])  # the other one is untouched
    dep = await session.get(Deployment, uuid.UUID(first["id"]))
    assert dep is not None
    await session.refresh(dep)
    assert dep.status is DeploymentStatus.reverted and dep.reverted_at is not None
    assert (
        dep.after_snapshot is not None
        and dep.after_snapshot["after_rollback"] == "Old title"
        and dep.after_snapshot["restored"] is True
    )
    batch = await session.get(DeployBatch, uuid.UUID(body["batch"]["id"]))
    assert batch is not None
    await session.refresh(batch)
    assert batch.status is not BatchStatus.reverted  # one of two is still live


async def test_rolling_back_a_batch_and_doing_it_twice(
    client: AsyncClient, session: AsyncSession, fake_registry: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    site, web, deployer, headers, body = await deployed_batch(client, session, fake_registry, monkeypatch)
    url = f"/sites/{site.id}/deploy/batches/{body['batch']['id']}/rollback"
    first = await client.post(url, headers=headers)
    assert first.status_code == 200, first.text
    assert len(first.json()["reverted"]) == 2
    assert web.render(URL) == PAGE and web.render(OTHER) == PAGE
    again = await client.post(url, headers=headers)  # idempotent: nothing happens, nothing breaks
    assert again.status_code == 200 and again.json()["reverted"] == [] and len(again.json()["skipped"]) == 2
    assert len(deployer.rolled_back) == 2
    batch = (
        await client.get(f"/sites/{site.id}/deploy/batches/{body['batch']['id']}", headers=headers)
    ).json()
    assert batch["batch"]["status"] == "reverted"
    events = (
        await session.scalars(sa.select(DeploymentEvent.event).where(DeploymentEvent.event == "reverted"))
    ).all()
    assert len(events) == 2
    requested = (
        await session.scalars(sa.select(DeploymentEvent).where(DeploymentEvent.event == "rollback_requested"))
    ).all()
    assert all(e.actor_id is not None for e in requested)


async def test_the_action_returns_to_a_reapprovable_state(
    client: AsyncClient, session: AsyncSession, fake_registry: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from searchos.models import Action

    site, _, _, headers, body = await deployed_batch(client, session, fake_registry, monkeypatch)
    await client.post(f"/sites/{site.id}/deploy/batches/{body['batch']['id']}/rollback", headers=headers)
    actions = (await session.scalars(sa.select(Action))).all()
    for a in actions:
        await session.refresh(a)
    assert {a.status for a in actions} == {ActionStatus.rolled_back}


async def test_rollback_works_after_the_ownership_proof_lapsed(
    client: AsyncClient, session: AsyncSession, fake_registry: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    site, web, _, headers, body = await deployed_batch(client, session, fake_registry, monkeypatch)
    site.verify_checked_at = site.verify_checked_at - timedelta(days=30)  # type: ignore[operator]
    await session.commit()
    resp = await client.post(
        f"/sites/{site.id}/deploy/batches/{body['batch']['id']}/rollback", headers=headers
    )
    assert resp.status_code == 200 and len(resp.json()["reverted"]) == 2
    assert web.render(URL) == PAGE


async def test_failed_rollback_is_reported_and_can_be_retried(
    client: AsyncClient, session: AsyncSession, fake_registry: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    site, web, deployer, headers, body = await deployed_batch(client, session, fake_registry, monkeypatch)
    dep_id = body["deployments"][0]["id"]
    deployer.fail_rollback = True
    bad = await client.post(
        f"/sites/{site.id}/deploy/rollback", json={"deployment_ids": [dep_id]}, headers=headers
    )
    assert bad.json()["reverted"] == [] and "rollback failed" in bad.json()["skipped"][0]["reason"]
    assert "A much better title" in web.render(
        body["deployments"][0]["url"]
    )  # still live, still rollbackable
    deployer.fail_rollback = False
    ok = await client.post(
        f"/sites/{site.id}/deploy/rollback", json={"deployment_ids": [dep_id]}, headers=headers
    )
    assert len(ok.json()["reverted"]) == 1
    failed_events = (
        await session.scalars(sa.select(DeploymentEvent).where(DeploymentEvent.event == "rollback_failed"))
    ).all()
    assert len(failed_events) == 1


async def test_rollback_needs_admin_and_stays_inside_the_organisation(
    client: AsyncClient, session: AsyncSession, fake_registry: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from searchos.models import Role

    site, _, _, headers, body = await deployed_batch(client, session, fake_registry, monkeypatch)
    viewer = await add_member(client, session, str(site.org_id), "viewer@example.com", Role.member)
    url = f"/sites/{site.id}/deploy/batches/{body['batch']['id']}/rollback"
    assert (await client.post(url, headers=viewer)).status_code == 403
    other, _, _ = await owner_with_site(client, "other.example", "other@example.com")
    assert (await client.post(url, headers=other)).status_code == 404
