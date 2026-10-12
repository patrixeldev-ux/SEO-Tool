"""3.2: planning a real (fixture) site end to end: actions per class, re-planning, rejection memory, triggers."""

from __future__ import annotations

import uuid
from collections import Counter
from datetime import UTC, date, datetime, timedelta

import respx
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from searchos.core.db import get_engine, get_sessionmaker
from searchos.devtools.fixture_server import FixtureServer
from searchos.models import Action, Issue, Job, JobStatus, Org, Site
from searchos.planner.catalog import ACTION_TYPES, ActionStatus, RiskClass
from searchos.planner.engine import plan_site
from searchos.worker.crawl_job import crawl_handler
from searchos.worker.jobs import run_job
from tests.conftest import auth, register
from tests.gsc_helpers import QUERY_URL, fake_search_console, make_connected_site, total_row
from tests.helpers import make_crawl
from tests.test_gsc_ingest import run_sync, steady

TODAY = date(2026, 10, 12)


async def crawled_site(session: AsyncSession, server: FixtureServer, **payload: object) -> uuid.UUID:
    _org, site, _crawl, job = await make_crawl(
        session, server, extra_payload={"js_check": False, "plan": False, **payload}
    )
    site_id = site.id
    await run_job(job.id, crawl_handler, engine=get_engine())
    return site_id


async def actions_of(session: AsyncSession, site_id: uuid.UUID, *statuses: ActionStatus) -> list[Action]:
    session.expire_all()
    query = sa.select(Action).where(Action.site_id == site_id)
    if statuses:
        query = query.where(Action.status.in_(statuses))
    return list((await session.scalars(query.order_by(Action.type, Action.field))).all())


async def test_planning_the_fixture_site_creates_actions_of_every_class(
    session: AsyncSession, fixture_site: FixtureServer
) -> None:
    site_id = await crawled_site(session, fixture_site)
    summary = await plan_site(get_sessionmaker(), site_id, trigger="test")
    assert summary["findings"] == 40 and summary["candidates"] == summary["created"] > 20
    assert summary["updated"] == 0 and summary["obsolete"] == 0

    rows = await actions_of(session, site_id)
    types = Counter(a.type for a in rows)
    assert {
        "rewrite_title",
        "rewrite_meta",
        "add_alt",
        "add_jsonld",
        "fix_jsonld",
        "fix_canonical",
        "collapse_redirect",
        "fix_link",
        "refresh_content",
        "dev_fix",
        "noindex_review",
        "robots_review",
    } <= set(types)
    assert {a.risk_class for a in rows} == {RiskClass.auto, RiskClass.approve, RiskClass.manual}
    for a in rows:
        assert a.risk_class is ACTION_TYPES[a.type].risk_class and a.effort == ACTION_TYPES[a.type].effort
        assert a.evidence["why"] and a.evidence["gain_basis"] in ("prior", "gsc")
        assert a.priority >= 0 and a.created_by == "system"
        assert a.target_url is None or a.target_url.startswith(fixture_site.base_url)
    # value-type actions wait for the AI, everything else has its value already
    assert {a.status for a in rows if a.type in ("rewrite_title", "rewrite_meta", "add_alt")} == {
        ActionStatus.needs_ai
    }
    assert all(a.proposed_value is None for a in rows if a.status is ActionStatus.needs_ai)
    assert all(
        a.proposed_value is not None
        for a in rows
        if a.type in ("fix_canonical", "collapse_redirect", "fix_link", "refresh_content", "dev_fix")
    )
    # nothing for the info-only checks
    assert not any("LLMS_TXT" in a.sources or "AI_TRAINING_BOT_BLOCKED" in a.sources for a in rows)


async def test_replanning_updates_instead_of_duplicating(
    session: AsyncSession, fixture_site: FixtureServer
) -> None:
    site_id = await crawled_site(session, fixture_site)
    first = await plan_site(get_sessionmaker(), site_id, trigger="test")
    before = {a.id: (a.type, a.target_url, a.field) for a in await actions_of(session, site_id)}
    second = await plan_site(get_sessionmaker(), site_id, trigger="test")
    after = {a.id: (a.type, a.target_url, a.field) for a in await actions_of(session, site_id)}
    assert before == after
    assert second["created"] == 0 and second["updated"] == first["created"] and second["obsolete"] == 0


async def test_an_action_whose_finding_disappears_becomes_obsolete(
    session: AsyncSession, fixture_site: FixtureServer
) -> None:
    site_id = await crawled_site(session, fixture_site)
    await plan_site(get_sessionmaker(), site_id, trigger="test")
    await session.execute(sa.delete(Issue).where(Issue.check_id == "IMG_ALT_MISSING"))
    await session.commit()
    summary = await plan_site(get_sessionmaker(), site_id, trigger="test")
    assert summary["obsolete"] >= 1
    still_open = await actions_of(session, site_id, ActionStatus.needs_ai)
    assert all(a.type != "add_alt" for a in still_open)
    gone = await actions_of(session, site_id, ActionStatus.obsolete)
    assert gone and all(a.type == "add_alt" for a in gone)
    # closed rows stay closed on the next plan
    assert (await plan_site(get_sessionmaker(), site_id, trigger="test"))["obsolete"] == 0


async def test_user_edits_and_approvals_survive_a_replan(
    session: AsyncSession, fixture_site: FixtureServer
) -> None:
    site_id = await crawled_site(session, fixture_site)
    await plan_site(get_sessionmaker(), site_id, trigger="test")
    canon = next(a for a in await actions_of(session, site_id) if a.type == "fix_canonical")
    canon.status = ActionStatus.approved
    canon.edited_by_user = True
    canon.proposed_value = "https://edited.example/x"
    canon_id = canon.id
    await session.commit()
    await plan_site(get_sessionmaker(), site_id, trigger="test")
    session.expire_all()
    canon = await session.get(Action, canon_id)  # type: ignore[assignment]
    assert canon.status is ActionStatus.approved and canon.proposed_value == "https://edited.example/x"


async def test_rejected_actions_are_not_reproposed_for_90_days_unless_priority_rises_by_half(
    session: AsyncSession, fixture_site: FixtureServer
) -> None:
    site_id = await crawled_site(session, fixture_site)
    await plan_site(get_sessionmaker(), site_id, trigger="test")
    rows = await actions_of(session, site_id)
    canon = next(a for a in rows if a.type == "fix_canonical")
    key = (canon.type, canon.target_url, canon.field)
    now = datetime.now(UTC)

    async def reject(priority_then: float, days_ago: int) -> None:
        canon.status = ActionStatus.rejected
        canon.decided_at = now - timedelta(days=days_ago)
        canon.priority_at_decision = priority_then
        canon.reject_reason = "not now"
        await session.commit()

    def same_key(a: Action) -> bool:
        return (a.type, a.target_url, a.field) == key

    # rejected yesterday at the same priority: stays rejected, nothing new
    await reject(canon.priority, 1)
    summary = await plan_site(get_sessionmaker(), site_id, trigger="test")
    assert summary["skipped_rejected"] >= 1
    assert [a.status for a in await actions_of(session, site_id) if same_key(a)] == [ActionStatus.rejected]

    # priority was low then, now it is 1.5x or more: comes back as a new open action
    await reject(canon.priority / 2, 1)
    summary = await plan_site(get_sessionmaker(), site_id, trigger="test")
    states = Counter(a.status for a in await actions_of(session, site_id) if same_key(a))
    assert states == Counter({ActionStatus.rejected: 1, ActionStatus.proposed: 1})

    # an old rejection (> 90 days) no longer counts
    for a in await actions_of(session, site_id):
        if same_key(a) and a.status is ActionStatus.proposed:
            await session.delete(a)
    await session.commit()
    await reject(canon.priority, 91)
    await plan_site(get_sessionmaker(), site_id, trigger="test")
    states = Counter(a.status for a in await actions_of(session, site_id) if same_key(a))
    assert states[ActionStatus.proposed] == 1


async def test_nothing_to_plan_from_without_an_audit_or_search_data(session: AsyncSession) -> None:
    org = Org(name="Empty")
    session.add(org)
    await session.flush()
    site = Site(org_id=org.id, domain="empty.example", display_name="empty")
    session.add(site)
    await session.commit()
    summary = await plan_site(get_sessionmaker(), site.id, trigger="test")
    assert "Nothing to plan" in summary["skipped"]
    assert await actions_of(session, site.id) == []
    assert (await plan_site(get_sessionmaker(), uuid.uuid4(), trigger="x"))[
        "skipped"
    ] == "site no longer exists"


# ---- triggers ------------------------------------------------------------------------------------
async def test_a_finished_audit_queues_a_plan_once(
    session: AsyncSession, fixture_site: FixtureServer
) -> None:
    _, site, _crawl, job = await make_crawl(
        session, fixture_site, page_limit=10, extra_payload={"js_check": False}
    )
    await run_job(job.id, crawl_handler, engine=get_engine())
    plans = (await session.scalars(sa.select(Job).where(Job.site_id == site.id, Job.type == "plan"))).all()
    assert len(plans) == 1 and plans[0].status is JobStatus.queued
    assert plans[0].payload == {"site_id": str(site.id), "trigger": "audit"}


async def test_finishing_a_search_console_sync_queues_a_plan(session: AsyncSession) -> None:
    _, site, conn = await make_connected_site(session)
    with respx.mock(assert_all_called=False) as router:
        router.post(QUERY_URL).mock(side_effect=fake_search_console(steady, lambda d: total_row(d, 6, 130)))
        job = await run_sync(
            session, conn, mode="backfill", today=TODAY.isoformat(), start="2026-09-01", end="2026-09-05"
        )
    assert job.status is JobStatus.done, job.error
    plans = (await session.scalars(sa.select(Job).where(Job.site_id == site.id, Job.type == "plan"))).all()
    assert [p.payload["trigger"] for p in plans] == ["opportunities"]


async def test_replan_endpoint_queues_one_job_and_checks_roles(client: AsyncClient) -> None:
    tokens = await register(client, "a@example.com")
    site_id = (await client.post("/sites", json={"domain": "plan.example"}, headers=auth(tokens))).json()[
        "id"
    ]
    url = f"/sites/{site_id}/actions/replan"
    first = await client.post(url, headers=auth(tokens))
    assert first.status_code == 202
    body = first.json()
    assert body["type"] == "plan" and body["status"] == "queued" and body["payload"]["trigger"] == "manual"
    second = await client.post(url, headers=auth(tokens))
    assert second.json()["id"] == body["id"]  # the queued plan is reused
    other = await register(client, "b@example.com", org_name="Other")
    assert (await client.post(url, headers=auth(other))).status_code == 404
    assert (await client.post(url)).status_code == 401


async def test_the_crawl_keeps_main_text_for_the_planner(
    session: AsyncSession, fixture_site: FixtureServer
) -> None:
    site_id = await crawled_site(session, fixture_site)
    del site_id
    from searchos.models import Page

    texts = (await session.scalars(sa.select(Page.main_text).where(Page.status_code == 200))).all()
    assert texts and sum(1 for t in texts if t and len(t) > 200) > 20
    assert all(t is None or len(t) <= 30_000 for t in texts)
