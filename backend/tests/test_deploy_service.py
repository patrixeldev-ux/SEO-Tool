"""4.2: deployments - snapshots, stale detection, the safety gates, verification, the API."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from searchos.deploy import ops, ownership, registry, schedule, service
from searchos.deploy.verify import RETRY_DELAYS, verify_deployment
from searchos.models import (
    Action,
    BatchStatus,
    BatchTrigger,
    Deployment,
    DeploymentEvent,
    DeploymentStatus,
    Provider,
    Role,
    Site,
)
from searchos.planner.catalog import ActionStatus, RiskClass
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

NOW = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)


_CURRENT: dict[str, FakeWebsite] = {}


@pytest.fixture(autouse=True)
def fake_fetcher(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service, "default_fetcher", lambda: _CURRENT["web"])


async def setup_site(
    client: AsyncClient, session: AsyncSession, fake_registry: Any, *, lag: int = 0
) -> tuple[Site, FakeWebsite, FakeDeployer, dict[str, str], dict[str, Any]]:
    headers, site_id, tokens = await owner_with_site(client)
    site = await verify_site(session, site_id)
    await connect(session, site)
    web = FakeWebsite(lag=lag)
    deployer = FakeDeployer(web)
    fake_registry.register(deployer)
    _CURRENT["web"] = web
    return site, web, deployer, headers, tokens


@pytest.fixture(autouse=True)
def no_celery(monkeypatch: pytest.MonkeyPatch) -> list[uuid.UUID]:
    queued: list[uuid.UUID] = []
    monkeypatch.setattr(schedule, "enqueue_verifications", lambda ids, countdown=0: queued.extend(ids))
    return queued


# ---- pure pieces --------------------------------------------------------------------------------------------
def test_ops_for_each_deployable_type() -> None:
    aid = uuid.uuid4()
    assert ops.ops_for_action("rewrite_title", "title", " New ", aid) == [{"op": "title", "value": "New"}]
    assert ops.ops_for_action("rewrite_meta", "meta_description", "d", aid)[0]["op"] == "meta_description"
    assert (
        ops.ops_for_action("fix_canonical", "canonical", "https://a.example/x", aid)[0]["op"] == "canonical"
    )
    alt = ops.ops_for_action("add_alt", "alt:https://a.example/i.png", "A cup", aid)[0]
    assert alt == {"op": "alt", "src": "https://a.example/i.png", "value": "A cup"}
    ld = ops.ops_for_action("fix_jsonld", "jsonld:Organization", {"@type": "Organization", "name": "X"}, aid)[
        0
    ]
    assert ld["id"] == f"searchos-{aid.hex[:12]}" and "Corporation" in ld["replace"]["types"]
    broken = ops.ops_for_action("fix_jsonld", "jsonld:parse_error", {"@type": "WebPage"}, aid)[0]
    assert broken["replace"] == "invalid"
    add = ops.ops_for_action("add_jsonld", "jsonld:Organization", '{"@type": "Organization"}', aid)[0]
    assert add["replace"] is None
    links = ops.ops_for_action(
        "add_internal_links",
        "internal_links",
        {"links": [{"to": "https://a.example/y", "anchor": "grinders"}] * 5},
        aid,
    )
    assert len(links) == 3 and links[0]["id"] == f"{aid.hex[:8]}-0"


@pytest.mark.parametrize(
    ("action_type", "field", "value"),
    [
        ("rewrite_title", "title", None),
        ("rewrite_title", "title", "  "),
        ("rewrite_title", "title", "x" * 700),
        ("fix_canonical", "canonical", "javascript:alert(1)"),
        ("add_alt", "oops", "A cup"),
        ("add_jsonld", "jsonld:Organization", "not json"),
        ("add_jsonld", "jsonld:Organization", {"name": "no type"}),
        ("add_internal_links", "internal_links", {"links": []}),
    ],
)
def test_bad_values_are_refused(action_type: str, field: str, value: Any) -> None:
    with pytest.raises(ops.NotDeployableError):
        ops.ops_for_action(action_type, field, value, uuid.uuid4())


def test_never_automated_and_manual_are_refused_on_every_route() -> None:
    for via in (False, True):
        for t in (
            "robots_review",
            "noindex_review",
            "robots_disallow_change",
            "delete_page",
            "link_exchange",
        ):
            with pytest.raises(ops.NotDeployableError, match="never made automatically"):
                ops.assert_deployable(t, RiskClass.auto, via_autopilot=via)
        with pytest.raises(ops.NotDeployableError, match="manual"):
            ops.assert_deployable("earn_links", RiskClass.manual, via_autopilot=via)
        with pytest.raises(ops.NotDeployableError, match="no automatic deployer"):
            ops.assert_deployable("collapse_redirect", RiskClass.approve, via_autopilot=via)


def test_autopilot_only_deploys_the_auto_class() -> None:
    ops.assert_deployable("rewrite_title", RiskClass.auto, via_autopilot=True)
    with pytest.raises(ops.NotDeployableError, match="Autopilot"):
        ops.assert_deployable("fix_canonical", RiskClass.approve, via_autopilot=True)
    ops.assert_deployable("fix_canonical", RiskClass.approve, via_autopilot=False)


# ---- the happy path -----------------------------------------------------------------------------------------
async def test_deploy_snapshots_verify_and_events(
    client: AsyncClient, session: AsyncSession, fake_registry: Any, no_celery: list[uuid.UUID]
) -> None:
    site, web, deployer, headers, _ = await setup_site(client, session, fake_registry)
    action = await make_action(session, site)
    resp = await client.post(
        f"/sites/{site.id}/deploy", json={"action_ids": [str(action.id)]}, headers=headers
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    dep = body["deployments"][0]
    assert dep["status"] == "applied" and dep["ops"] == [
        {"op": "title", "value": "A much better title for the coffee page"}
    ]
    assert dep["before_snapshot"]["value"] == "Old title"
    assert body["batch"]["status"] == "applied" and body["batch"]["counts"] == {"applied": 1}
    assert no_celery == [uuid.UUID(dep["id"])]
    await session.refresh(action)
    assert action.status is ActionStatus.deployed

    # the live page really changed, and verification sees it
    row = await session.get(Deployment, uuid.UUID(dep["id"]))
    assert row is not None
    out = await verify_deployment(session, row, fetcher=web, now=NOW)
    assert out.state == "verified"
    await session.refresh(row)
    assert row.status is DeploymentStatus.verified
    assert (
        row.after_snapshot is not None
        and row.after_snapshot["value"] == "A much better title for the coffee page"
    )
    events = (await session.scalars(sa.select(DeploymentEvent.event).order_by(DeploymentEvent.id))).all()
    assert (
        events[:2] == ["deployment_created", "batch_created"] and "applied" in events and "verified" in events
    )
    listing = (await client.get(f"/sites/{site.id}/deploy/deployments", headers=headers)).json()
    assert [d["status"] for d in listing] == ["verified"]
    batches = (await client.get(f"/sites/{site.id}/deploy/batches", headers=headers)).json()
    assert batches[0]["status"] == "verified"


async def test_before_snapshot_is_the_live_page_not_the_old_crawl(
    client: AsyncClient, session: AsyncSession, fake_registry: Any
) -> None:
    site, web, _, headers, _ = await setup_site(client, session, fake_registry)
    # the action says the title was "Old title"; the live page agrees, so it is deployed and the snapshot is live
    action = await make_action(session, site, current="  Old   title ")
    result = await service.deploy_actions(session, site, [action.id], user_id=None, fetcher=web)
    assert result.deployments[0].before_snapshot["value"] == "Old title"  # type: ignore[index]


# ---- staleness -------------------------------------------------------------------------------------------------
async def test_page_edited_since_the_audit_makes_the_action_stale(
    client: AsyncClient, session: AsyncSession, fake_registry: Any
) -> None:
    site, web, deployer, headers, _ = await setup_site(client, session, fake_registry)
    web.original[URL] = PAGE.replace("Old title", "Somebody edited this just now")
    action = await make_action(session, site)  # written for "Old title"
    resp = await client.post(
        f"/sites/{site.id}/deploy", json={"action_ids": [str(action.id)]}, headers=headers
    )
    body = resp.json()
    assert resp.status_code == 201 and body["deployments"] == []
    assert "changed since the audit" in body["skipped"][0]["reason"]
    await session.refresh(action)
    assert action.status is ActionStatus.stale and "stale_reason" in action.evidence
    assert deployer.applied == [] and not web.rules  # nothing was written to the site
    assert body["batch"]["status"] == "failed"


async def test_a_page_that_already_shows_the_new_value_is_not_stale(
    client: AsyncClient, session: AsyncSession, fake_registry: Any
) -> None:
    site, web, deployer, _, _ = await setup_site(client, session, fake_registry)
    web.original[URL] = PAGE.replace("Old title", "A much better title for the coffee page")
    action = await make_action(session, site)
    result = await service.deploy_actions(session, site, [action.id], user_id=None, fetcher=web)
    assert len(result.deployments) == 1


async def test_unreadable_page_is_skipped_not_deployed(
    client: AsyncClient, session: AsyncSession, fake_registry: Any
) -> None:
    site, web, deployer, _, _ = await setup_site(client, session, fake_registry)
    web.fail_status[URL] = 503
    action = await make_action(session, site)
    result = await service.deploy_actions(session, site, [action.id], user_id=None, fetcher=web)
    assert not result.deployments and "HTTP 503" in result.skipped[0].reason
    await session.refresh(action)
    assert action.status is ActionStatus.approved


# ---- gates -------------------------------------------------------------------------------------------------------
async def test_unverified_site_cannot_deploy(
    client: AsyncClient, session: AsyncSession, fake_registry: Any
) -> None:
    headers, site_id, _ = await owner_with_site(client)
    site = await session.get(Site, uuid.UUID(site_id))
    assert site is not None
    await connect(session, site)
    web = FakeWebsite()
    deployer = FakeDeployer(web)
    fake_registry.register(deployer)
    action = await make_action(session, site)
    resp = await client.post(
        f"/sites/{site_id}/deploy", json={"action_ids": [str(action.id)]}, headers=headers
    )
    assert resp.status_code == 409 and "not been verified" in resp.json()["detail"]
    assert deployer.applied == []


async def test_lapsed_proof_blocks_deploy(
    client: AsyncClient, session: AsyncSession, fake_registry: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers, site_id, _ = await owner_with_site(client)
    site = await verify_site(session, site_id, now=NOW - timedelta(days=9))
    await connect(session, site)
    fake_registry.register(FakeDeployer(FakeWebsite()))
    action = await make_action(session, site)
    from tests.deploy_helpers import checkers

    monkeypatch.setattr(ownership, "default_checkers", lambda: checkers())
    resp = await client.post(
        f"/sites/{site_id}/deploy", json={"action_ids": [str(action.id)]}, headers=headers
    )
    assert resp.status_code == 409 and "re-checked every 7 days" in resp.json()["detail"]
    await session.refresh(site)
    assert site.verified_at is None


async def test_no_deployer_connected(client: AsyncClient, session: AsyncSession, fake_registry: Any) -> None:
    headers, site_id, _ = await owner_with_site(client)
    site = await verify_site(session, site_id)
    action = await make_action(session, site)
    resp = await client.post(
        f"/sites/{site_id}/deploy", json={"action_ids": [str(action.id)]}, headers=headers
    )
    assert resp.status_code == 409 and "No deployer is connected" in resp.json()["detail"]


async def test_only_admins_can_deploy(client: AsyncClient, session: AsyncSession, fake_registry: Any) -> None:
    site, _, deployer, _, tokens = await setup_site(client, session, fake_registry)
    action = await make_action(session, site)
    org_id = tokens["orgs"][0]["id"]
    for role in (Role.member, Role.client_viewer):
        h = await add_member(client, session, org_id, f"{role.value}@example.com", role)
        r = await client.post(f"/sites/{site.id}/deploy", json={"action_ids": [str(action.id)]}, headers=h)
        assert r.status_code == 403
        # history is readable by members, not by client viewers' write routes
        assert (await client.get(f"/sites/{site.id}/deploy/deployments", headers=h)).status_code in (200, 403)
    assert deployer.applied == []
    admin = await add_member(client, session, org_id, "admin@example.com", Role.admin)
    ok = await client.post(f"/sites/{site.id}/deploy", json={"action_ids": [str(action.id)]}, headers=admin)
    assert ok.status_code == 201


async def test_other_orgs_cannot_deploy_to_my_site(
    client: AsyncClient, session: AsyncSession, fake_registry: Any
) -> None:
    site, _, _, _, _ = await setup_site(client, session, fake_registry)
    other, _, _ = await owner_with_site(client, "other.example", "other@example.com")
    r = await client.post(f"/sites/{site.id}/deploy", json={"action_ids": [str(uuid.uuid4())]}, headers=other)
    assert r.status_code == 404


async def test_defence_in_depth_refuses_never_automated_actions_with_any_status(
    client: AsyncClient, session: AsyncSession, fake_registry: Any
) -> None:
    site, web, deployer, _, _ = await setup_site(client, session, fake_registry)
    # Rows no API route could produce: approved robots / noindex reviews, a manual task, a redirect (no deployer)
    bad = [
        await make_action(
            session, site, "robots_review", field="robots", current=None, proposed={"kind": "brief"}
        ),
        await make_action(
            session, site, "noindex_review", field="x", current=None, proposed={"kind": "brief"}
        ),
        await make_action(session, site, "earn_links", field="y", current=None, proposed={"kind": "brief"}),
        await make_action(
            session, site, "collapse_redirect", field="redirect", current=None, proposed={"to": "x"}
        ),
    ]
    result = await service.deploy_actions(session, site, [a.id for a in bad], user_id=None, fetcher=web)
    assert not result.deployments and len(result.skipped) == 4
    assert "never made automatically" in result.skipped[0].reason
    assert deployer.applied == [] and not web.rules


async def test_only_approved_actions_and_only_pages_of_the_site(
    client: AsyncClient, session: AsyncSession, fake_registry: Any
) -> None:
    site, web, deployer, _, _ = await setup_site(client, session, fake_registry)
    proposed = await make_action(session, site, status=ActionStatus.proposed)
    foreign = await make_action(session, site, url="https://evil.example/page")
    result = await service.deploy_actions(session, site, [proposed.id, foreign.id], user_id=None, fetcher=web)
    reasons = [s.reason for s in result.skipped]
    assert "not approved" in reasons[0] and "not on this site" in reasons[1]
    # an unknown id is reported, too
    result = await service.deploy_actions(session, site, [uuid.uuid4()], user_id=None, fetcher=web)
    assert result.skipped[0].reason == "Action not found on this site."


async def test_batch_size_and_empty_requests(
    client: AsyncClient, session: AsyncSession, fake_registry: Any
) -> None:
    site, web, _, headers, _ = await setup_site(client, session, fake_registry)
    r = await client.post(f"/sites/{site.id}/deploy", json={"action_ids": []}, headers=headers)
    assert r.status_code == 422
    ids = [str(uuid.uuid4()) for _ in range(service.MAX_BATCH + 1)]
    r = await client.post(f"/sites/{site.id}/deploy", json={"action_ids": ids}, headers=headers)
    assert r.status_code == 422


async def test_two_actions_on_one_page_share_one_batch(
    client: AsyncClient, session: AsyncSession, fake_registry: Any
) -> None:
    site, web, deployer, _, _ = await setup_site(client, session, fake_registry)
    t = await make_action(session, site)
    m = await make_action(
        session, site, "rewrite_meta", current="Old description", proposed="New description here"
    )
    result = await service.deploy_actions(session, site, [t.id, m.id], user_id=None, fetcher=web)
    assert len(result.deployments) == 2 and result.batch is not None
    html = web.render(URL)
    assert (
        "<title>A much better title for the coffee page</title>" in html
        and 'content="New description here"' in html
    )


async def test_failed_apply_keeps_the_action_approved(
    client: AsyncClient, session: AsyncSession, fake_registry: Any
) -> None:
    site, web, deployer, _, _ = await setup_site(client, session, fake_registry)
    deployer.fail_urls.add(URL)
    action = await make_action(session, site)
    result = await service.deploy_actions(session, site, [action.id], user_id=None, fetcher=web)
    assert result.deployments[0].status is DeploymentStatus.failed and result.batch is not None
    assert result.batch.status is BatchStatus.failed
    await session.refresh(action)
    assert action.status is ActionStatus.approved and "told to fail" in (result.deployments[0].error or "")


# ---- verification ------------------------------------------------------------------------------------------------
async def test_verification_waits_for_a_slow_cdn_then_succeeds(
    client: AsyncClient, session: AsyncSession, fake_registry: Any
) -> None:
    site, web, _, _, _ = await setup_site(
        client, session, fake_registry, lag=3
    )  # first fetch is the pre-deploy read
    action = await make_action(session, site)
    result = await service.deploy_actions(session, site, [action.id], user_id=None, fetcher=web, now=NOW)
    dep = result.deployments[0]
    seen: list[str] = []
    t = NOW
    for _ in range(6):
        out = await verify_deployment(session, dep, fetcher=web, now=t)
        seen.append(out.state)
        if out.state != "retry":
            break
        assert out.next_delay in RETRY_DELAYS
        t += timedelta(seconds=out.next_delay or 0)
    assert seen[-1] == "verified" and seen[:-1] and set(seen[:-1]) == {"retry"}
    assert dep.verify_attempts == len(seen)


async def test_verification_fails_after_two_minutes_with_the_observed_value_and_rolls_back(
    client: AsyncClient, session: AsyncSession, fake_registry: Any
) -> None:
    site, web, _, _, _ = await setup_site(client, session, fake_registry, lag=10_000)
    action = await make_action(session, site)
    result = await service.deploy_actions(session, site, [action.id], user_id=None, fetcher=web, now=NOW)
    dep = result.deployments[0]
    t = NOW
    final = None
    for _ in range(20):
        final = await verify_deployment(session, dep, fetcher=web, now=t)
        if final.state != "retry":
            break
        t += timedelta(seconds=final.next_delay or 0)
    assert final is not None and final.state == "failed"
    assert t - NOW <= timedelta(minutes=2)
    # three retries after the first attempt, then the change is taken back automatically
    assert dep.verify_attempts == 4
    assert dep.status is DeploymentStatus.reverted and "Old title" in (dep.error or "")
    assert dep.after_snapshot is not None and dep.after_snapshot["value"] == "Old title"
    await session.refresh(action)
    assert action.status is ActionStatus.approved  # may be deployed again, or the change rolled back


async def test_verification_uses_a_cache_busting_fetch(
    client: AsyncClient, session: AsyncSession, fake_registry: Any
) -> None:
    from searchos.deploy.livefetch import cache_busted

    a, b = cache_busted("https://x.example/p?a=1"), cache_busted("https://x.example/p?a=1")
    assert a != b and a.startswith("https://x.example/p?a=1&searchos_verify=")

    seen: list[bool] = []

    class Spy(FakeWebsite):
        async def get(self, url: str, *, no_cache: bool = False) -> Any:
            seen.append(no_cache)
            return await super().get(url, no_cache=no_cache)

    site, _, deployer, _, _ = await setup_site(client, session, fake_registry)
    spy = Spy()
    deployer.site = spy
    action = await make_action(session, site)
    result = await service.deploy_actions(session, site, [action.id], user_id=None, fetcher=spy)
    await verify_deployment(session, result.deployments[0], fetcher=spy)
    assert seen and all(seen)


def test_batch_status_words() -> None:
    S = DeploymentStatus
    assert service.batch_status([S.verified, S.verified]) is BatchStatus.verified
    assert service.batch_status([S.verified, S.applied]) is BatchStatus.applied
    assert service.batch_status([S.verified, S.failed]) is BatchStatus.partial
    assert service.batch_status([S.failed]) is BatchStatus.failed
    assert service.batch_status([S.reverted, S.reverted]) is BatchStatus.reverted
    assert service.batch_status([S.reverted, S.verified]) is BatchStatus.verified


async def test_batch_trigger_is_recorded(
    client: AsyncClient, session: AsyncSession, fake_registry: Any
) -> None:
    site, web, _, _, _ = await setup_site(client, session, fake_registry)
    action = await make_action(session, site)
    result = await service.deploy_actions(session, site, [action.id], user_id=None, fetcher=web)
    assert result.batch is not None and result.batch.trigger is BatchTrigger.manual
    assert registry.get_deployer(result.batch.method) is not None
    assert Provider.cloudflare.value == result.batch.method.value
    count = await session.scalar(sa.select(sa.func.count()).select_from(Action))
    assert count == 1


def test_schedule_for_github_is_longer_than_default() -> None:
    from searchos.deploy.verify import DEFAULT_SCHEDULE, GITHUB_SCHEDULE, schedule_for
    from searchos.models.deploy import DeployMethod

    assert schedule_for(DeployMethod.github) is GITHUB_SCHEDULE
    assert schedule_for(DeployMethod.cloudflare) is DEFAULT_SCHEDULE
    assert GITHUB_SCHEDULE.window > DEFAULT_SCHEDULE.window
