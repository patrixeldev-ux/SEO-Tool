"""4.7: autopilot - limits, ordering, the auto class only, notifications, the daily task."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from searchos.deploy import autopilot, schedule, service
from searchos.models import BatchTrigger, Deployment, DeploymentStatus, Notification, Site
from searchos.planner.catalog import ActionStatus
from tests.deploy_fakes import (
    PAGE,
    FakeDeployer,
    FakeWebsite,
    connect,
    fake_registry,  # noqa: F401  (pytest fixture)
    make_action,
    verify_site,
)
from tests.deploy_helpers import owner_with_site

NOW = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)


def url(i: int) -> str:
    return f"https://shop.example/p{i}"


@pytest.fixture(autouse=True)
def quiet(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(schedule, "enqueue_verifications", lambda ids, countdown=0: None)


async def setup(
    client: AsyncClient, session: AsyncSession, fake_registry: Any, pages: int = 5, *, on: bool = True
) -> tuple[Site, FakeWebsite, FakeDeployer, dict[str, str]]:
    headers, site_id, _ = await owner_with_site(client)
    site = await verify_site(session, site_id)
    await connect(session, site)
    web = FakeWebsite({url(i): PAGE.replace(".com/page", f"/p{i}") for i in range(pages)})
    deployer = FakeDeployer(web)
    fake_registry.register(deployer)
    site.autopilot_enabled = on
    await session.commit()
    return site, web, deployer, headers


async def run(session: AsyncSession, site: Site, web: FakeWebsite, now: datetime = NOW) -> Any:
    return await autopilot.run_autopilot_for_site(session, site, now=now, fetcher=web)


async def test_deploys_best_auto_actions_first_and_stops_at_the_weekly_cap(
    client: AsyncClient, session: AsyncSession, fake_registry: Any
) -> None:
    site, web, _, _ = await setup(client, session, fake_registry)
    site.settings = {autopilot.CAP_KEY: 2}
    low = await make_action(session, site, url=url(0), priority=1.0, status=ActionStatus.proposed)
    mid = await make_action(session, site, url=url(1), priority=5.0, status=ActionStatus.proposed)
    top = await make_action(session, site, url=url(2), priority=9.0, status=ActionStatus.approved)
    res = await run(session, site, web)
    assert res.deployed == 2
    for a in (low, mid, top):
        await session.refresh(a)
    assert top.status is ActionStatus.deployed and mid.status is ActionStatus.deployed
    assert low.status is ActionStatus.proposed
    assert mid.evidence["approved_by"] == "autopilot" and mid.decided_by is None
    batch = (await session.scalars(sa.select(service.DeployBatch))).one()
    assert batch.trigger is BatchTrigger.autopilot
    # the cap is used up for the rest of the week
    again = await run(session, site, web, NOW + timedelta(days=1))
    assert again.deployed == 0 and again.reason == "weekly limit reached"
    # ...and free again after 7 days
    site.verified_at = site.verify_checked_at = NOW + timedelta(days=8)
    await make_action(session, site, url=url(4), status=ActionStatus.proposed)
    later = await run(session, site, web, NOW + timedelta(days=8))
    assert later.deployed == 2  # the week has passed: the cap allows 2 more


async def test_default_cap_is_twenty() -> None:
    assert autopilot.weekly_cap(Site(settings={})) == 20
    assert autopilot.weekly_cap(Site(settings={autopilot.CAP_KEY: 5})) == 5
    assert autopilot.weekly_cap(Site(settings={autopilot.CAP_KEY: "x"})) == 20
    assert autopilot.weekly_cap(Site(settings={autopilot.CAP_KEY: 10_000})) == autopilot.MAX_WEEKLY_CAP


async def test_one_change_per_url_per_28_days(
    client: AsyncClient, session: AsyncSession, fake_registry: Any
) -> None:
    site, web, _, _ = await setup(client, session, fake_registry)
    t = await make_action(session, site, "rewrite_title", url=url(0), status=ActionStatus.approved)
    m = await make_action(
        session,
        site,
        "rewrite_meta",
        url=url(0),
        current="Old description",
        proposed="New description for coffee",
        status=ActionStatus.approved,
    )
    first = await run(session, site, web)
    assert first.deployed == 1  # two actions on one URL: only one per run
    site.verified_at = site.verify_checked_at = NOW + timedelta(days=10)
    second = await run(session, site, web, NOW + timedelta(days=10))
    assert second.deployed == 0  # the URL is cooling down
    site.verified_at = site.verify_checked_at = NOW + timedelta(days=29)
    third = await run(session, site, web, NOW + timedelta(days=29))
    assert third.deployed == 1
    for a in (t, m):
        await session.refresh(a)
    assert {t.status, m.status} == {ActionStatus.deployed}


async def test_only_the_auto_class_and_never_the_never_automated(
    client: AsyncClient, session: AsyncSession, fake_registry: Any
) -> None:
    site, web, _, _ = await setup(client, session, fake_registry)
    canon = await make_action(
        session,
        site,
        "fix_canonical",
        url=url(0),
        current="x",
        proposed="https://shop.example/p0",
        status=ActionStatus.approved,
    )
    noindex = await make_action(
        session, site, "robots_review", url=url(1), proposed="index", status=ActionStatus.approved
    )
    res = await run(session, site, web)
    assert res.deployed == 0 and res.reason == "nothing to deploy"
    for a in (canon, noindex):
        await session.refresh(a)
    assert canon.status is ActionStatus.approved and noindex.status is ActionStatus.approved


async def test_off_unverified_or_unconnected_sites_do_nothing(
    client: AsyncClient, session: AsyncSession, fake_registry: Any
) -> None:
    site, web, _, _ = await setup(client, session, fake_registry, on=False)
    a = await make_action(session, site, url=url(0), status=ActionStatus.proposed)
    assert (await run(session, site, web)).reason == "autopilot is off"
    site.autopilot_enabled = True
    site.verified_at = None
    assert (await run(session, site, web)).reason == "site ownership is not verified"
    await session.refresh(a)
    assert a.status is ActionStatus.proposed
    site.verified_at = NOW
    site.deploy_method = "wordpress"  # chosen but not connected
    res = await run(session, site, web)
    await session.refresh(a)
    assert res.deployed == 0 and a.status is ActionStatus.proposed


async def test_a_skipped_action_returns_to_proposed(
    client: AsyncClient, session: AsyncSession, fake_registry: Any
) -> None:
    site, web, _, _ = await setup(client, session, fake_registry)
    a = await make_action(
        session, site, url=url(0), current="Not what the page says", status=ActionStatus.proposed
    )
    res = await run(session, site, web)
    assert res.deployed == 0 and res.batch_id is None
    await session.refresh(a)
    assert (
        a.status is ActionStatus.stale
    )  # the page changed since the audit; nothing was approved by a person
    assert (await session.scalars(sa.select(Notification))).all() == []


async def test_a_batch_writes_one_notification(
    client: AsyncClient, session: AsyncSession, fake_registry: Any
) -> None:
    site, web, _, headers = await setup(client, session, fake_registry)
    for i in range(3):
        await make_action(session, site, url=url(i), status=ActionStatus.proposed, priority=i + 1)
    res = await run(session, site, web)
    assert res.deployed == 3
    listing = (await client.get("/notifications", headers=headers)).json()
    assert listing["unread"] == 1
    n = listing["items"][0]
    assert n["title"].startswith("Autopilot made 3 changes") and n["batch_id"] == str(res.batch_id)
    assert (await client.post(f"/notifications/{n['id']}/read", headers=headers)).json()["read"] is True
    assert (await client.get("/notifications", headers=headers)).json()["unread"] == 0


async def test_notifications_are_private_to_the_organisation(
    client: AsyncClient, session: AsyncSession, fake_registry: Any
) -> None:
    site, web, _, _ = await setup(client, session, fake_registry)
    await make_action(session, site, url=url(0), status=ActionStatus.proposed)
    await run(session, site, web)
    other, _, _ = await owner_with_site(client, "other.example", "other@example.com")
    assert (await client.get("/notifications", headers=other)).json() == {"unread": 0, "items": []}
    n = (await session.scalars(sa.select(Notification))).one()
    assert (await client.post(f"/notifications/{n.id}/read", headers=other)).status_code == 404


async def test_autopilot_settings_endpoint(
    client: AsyncClient, session: AsyncSession, fake_registry: Any
) -> None:
    site, _, _, headers = await setup(client, session, fake_registry, on=False)
    got = (await client.get(f"/sites/{site.id}/deploy/autopilot", headers=headers)).json()
    assert got["enabled"] is False and got["weekly_cap"] == 20 and got["ready"] is True
    put = await client.put(
        f"/sites/{site.id}/deploy/autopilot", json={"enabled": True, "weekly_cap": 7}, headers=headers
    )
    assert put.json()["enabled"] is True and put.json()["weekly_cap"] == 7
    bad = await client.put(f"/sites/{site.id}/deploy/autopilot", json={"weekly_cap": 9999}, headers=headers)
    assert bad.status_code == 422


async def test_daily_task_covers_every_site_and_survives_a_failing_one(
    client: AsyncClient,
    session: AsyncSession,
    fake_registry: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from searchos.core.db import get_sessionmaker

    sessionmaker_ = get_sessionmaker()
    site, web, _, _ = await setup(client, session, fake_registry)
    await make_action(session, site, url=url(0), status=ActionStatus.proposed)
    monkeypatch.setattr(service, "default_fetcher", lambda: web)
    out = await autopilot.run_autopilot_all(sessionmaker_)
    assert out["sites"] == 1 and out["deployed"] == 1

    async def boom(*_: Any, **__: Any) -> Any:
        raise RuntimeError("x")

    monkeypatch.setattr(autopilot, "run_autopilot_for_site", boom)
    out = await autopilot.run_autopilot_all(sessionmaker_)
    assert out["deployed"] == 0 and out["results"][0]["reason"] == "error"
    dep = (await session.scalars(sa.select(Deployment))).all()
    assert [d.status for d in dep] == [DeploymentStatus.applied]
    assert uuid.UUID(out["results"][0]["site_id"]) == site.id
