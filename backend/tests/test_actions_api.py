"""3.6: deciding on actions through the API - approve, edit with validators, reject, bulk, export, hand-off, AI settings."""

from __future__ import annotations

import csv
import io
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from searchos.core.config import get_settings
from searchos.core.db import get_sessionmaker
from searchos.devtools.fixture_server import FixtureServer
from searchos.llm.fake import FakeProvider
from searchos.models import Action, Job, JobStatus, Membership, Role, Site
from searchos.planner.catalog import ActionStatus, RiskClass
from searchos.planner.engine import plan_site
from tests.conftest import auth, register
from tests.test_planner_engine import crawled_site


class World:
    def __init__(self, tokens: dict[str, Any], site_id: uuid.UUID, org_id: uuid.UUID) -> None:
        self.tokens = tokens
        self.site_id = site_id
        self.org_id = org_id
        self.headers = auth(tokens, str(org_id))
        self.base = f"/sites/{site_id}/actions"


async def world(
    client: AsyncClient,
    session: AsyncSession,
    fixture_site: FixtureServer,
    *,
    provider: FakeProvider | None = None,
    ai: bool = True,
    email: str = "owner@example.com",
) -> World:
    """A signed-in owner of the organisation that holds the crawled and planned fixture site."""
    tokens = await register(client, email)
    site_id = await crawled_site(session, fixture_site)
    site = await session.get(Site, site_id)
    assert site is not None
    org_id = site.org_id
    session.add(Membership(org_id=org_id, user_id=uuid.UUID(tokens["user"]["id"]), role=Role.owner))
    await session.commit()
    await plan_site(
        get_sessionmaker(), site_id, trigger="test", provider=(provider or FakeProvider()) if ai else None
    )
    return World(tokens, site_id, org_id)


async def listing(client: AsyncClient, w: World, **params: Any) -> list[dict[str, Any]]:
    resp = await client.get(w.base, params=params, headers=w.headers)
    assert resp.status_code == 200, resp.text
    rows: list[dict[str, Any]] = resp.json()
    return rows


def first(rows: list[dict[str, Any]], type_: str, status: str = "proposed") -> dict[str, Any]:
    return next(r for r in rows if r["type"] == type_ and r["status"] == status)


async def join(
    client: AsyncClient, session: AsyncSession, w: World, email: str, role: Role
) -> dict[str, str]:
    tokens = await register(client, email)
    session.add(Membership(org_id=w.org_id, user_id=uuid.UUID(tokens["user"]["id"]), role=role))
    await session.commit()
    return auth(tokens, str(w.org_id))


# ---- approve / reject / reopen / done -----------------------------------------------------------------------
async def test_approve_then_reopen(
    client: AsyncClient, session: AsyncSession, fixture_site: FixtureServer
) -> None:
    w = await world(client, session, fixture_site)
    title = first(await listing(client, w), "rewrite_title")
    done = await client.post(f"{w.base}/{title['id']}/approve", headers=w.headers)
    assert done.status_code == 200 and done.json()["status"] == "approved"
    again = await client.post(f"{w.base}/{title['id']}/approve", headers=w.headers)
    assert again.status_code == 409 and "approved" in again.json()["detail"]
    back = await client.post(f"{w.base}/{title['id']}/reopen", headers=w.headers)
    assert back.json()["status"] == "proposed"
    row = await session.get(Action, uuid.UUID(title["id"]))
    assert row is not None
    await session.refresh(row)
    assert row.decided_by is None


async def test_cannot_approve_what_has_no_value_or_is_manual(
    client: AsyncClient, session: AsyncSession, fixture_site: FixtureServer
) -> None:
    w = await world(
        client, session, fixture_site, provider=FakeProvider(handler=lambda _t, _d: {"title": "x"})
    )
    rows = await listing(client, w)
    waiting = next(r for r in rows if r["status"] in ("no_suggestion", "needs_ai"))
    resp = await client.post(f"{w.base}/{waiting['id']}/approve", headers=w.headers)
    assert resp.status_code == 409
    manual = first(rows, "earn_links")
    resp = await client.post(f"{w.base}/{manual['id']}/approve", headers=w.headers)
    assert resp.status_code == 409 and "manual task" in resp.json()["detail"]
    done = await client.post(f"{w.base}/{manual['id']}/done", headers=w.headers)
    assert done.json()["status"] == "manual_done"
    other = first(rows, "fix_canonical")
    resp = await client.post(f"{w.base}/{other['id']}/done", headers=w.headers)
    assert resp.status_code == 409 and "Only manual tasks" in resp.json()["detail"]


async def test_reject_needs_a_reason_and_is_not_proposed_again(
    client: AsyncClient, session: AsyncSession, fixture_site: FixtureServer
) -> None:
    w = await world(client, session, fixture_site)
    canon = first(await listing(client, w), "fix_canonical")
    url = f"{w.base}/{canon['id']}/reject"
    assert (await client.post(url, json={}, headers=w.headers)).status_code == 422
    assert (await client.post(url, json={"reason": "   "}, headers=w.headers)).status_code == 409
    resp = await client.post(url, json={"reason": "  We  do this\nlater "}, headers=w.headers)
    body = resp.json()
    assert (
        resp.status_code == 200
        and body["status"] == "rejected"
        and body["reject_reason"] == "We do this later"
    )

    summary = await plan_site(get_sessionmaker(), w.site_id, trigger="test", provider=FakeProvider())
    assert summary["skipped_rejected"] >= 1
    rows = await listing(client, w, type="fix_canonical")
    assert [r["status"] for r in rows if r["id"] == canon["id"]] == ["rejected"]
    assert not [r for r in rows if r["status"] == "proposed" and r["target_url"] == canon["target_url"]]

    reopened = await client.post(f"{w.base}/{canon['id']}/reopen", headers=w.headers)
    assert reopened.json()["status"] == "proposed" and reopened.json()["reject_reason"] is None


async def test_roles_and_tenants(
    client: AsyncClient, session: AsyncSession, fixture_site: FixtureServer
) -> None:
    w = await world(client, session, fixture_site)
    row = first(await listing(client, w), "rewrite_title")
    viewer = await join(client, session, w, "viewer@example.com", Role.client_viewer)
    member = await join(client, session, w, "member@example.com", Role.member)
    stranger = auth(await register(client, "x@example.com", org_name="Other"))
    for op in ("approve", "reopen", "done"):
        assert (await client.post(f"{w.base}/{row['id']}/{op}", headers=viewer)).status_code == 403
    assert (
        await client.post(f"{w.base}/{row['id']}/reject", json={"reason": "no"}, headers=viewer)
    ).status_code == 403
    assert (
        await client.patch(f"{w.base}/{row['id']}", json={"proposed_value": "x"}, headers=viewer)
    ).status_code == 403
    assert (await client.get(w.base, headers=viewer)).status_code == 200
    assert (await client.get(f"{w.base}/summary", headers=viewer)).status_code == 200
    assert (await client.post(f"{w.base}/{row['id']}/approve", headers=member)).status_code == 200
    for method, path in (
        ("get", w.base),
        ("get", f"{w.base}/summary"),
        ("get", f"{w.base}/export.csv"),
        ("get", f"{w.base}/handoff"),
        ("get", f"{w.base}/{row['id']}"),
        ("post", f"{w.base}/{row['id']}/approve"),
    ):
        resp = await getattr(client, method)(path, headers=stranger)
        assert resp.status_code == 404, path
    # an action id under another site of the same org is not found either
    other_site = (await client.post("/sites", json={"domain": "second.example"}, headers=w.headers)).json()[
        "id"
    ]
    assert (
        await client.post(f"/sites/{other_site}/actions/{row['id']}/approve", headers=w.headers)
    ).status_code == 404


# ---- bulk -------------------------------------------------------------------------------------------------
async def test_bulk_approve_reports_each_action_on_its_own(
    client: AsyncClient, session: AsyncSession, fixture_site: FixtureServer
) -> None:
    w = await world(client, session, fixture_site)
    rows = await listing(client, w)
    auto = [r["id"] for r in rows if r["risk_class"] == "auto" and r["status"] == "proposed"][:4]
    manual = first(rows, "earn_links")["id"]
    ghost = str(uuid.uuid4())
    resp = await client.post(
        f"{w.base}/bulk",
        json={"ids": [*auto, manual, ghost, auto[0]], "op": "approve"},
        headers=w.headers,
    )
    body = resp.json()
    assert resp.status_code == 200
    assert [u["id"] for u in body["updated"]] == auto and {u["status"] for u in body["updated"]} == {
        "approved"
    }
    errors = {f["id"]: f["error"] for f in body["failed"]}
    assert (
        set(errors) == {manual, ghost}
        and "manual task" in errors[manual]
        and errors[ghost] == "Action not found"
    )
    summary = (await client.get(f"{w.base}/summary", headers=w.headers)).json()
    assert summary["by_status"]["approved"] == len(auto)


async def test_bulk_reject_shares_one_reason_and_requires_it(
    client: AsyncClient, session: AsyncSession, fixture_site: FixtureServer
) -> None:
    w = await world(client, session, fixture_site)
    ids = [r["id"] for r in (await listing(client, w)) if r["type"] == "add_alt"][:3]
    assert ids
    missing = await client.post(f"{w.base}/bulk", json={"ids": ids, "op": "reject"}, headers=w.headers)
    assert len(missing.json()["failed"]) == len(ids) and not missing.json()["updated"]
    ok = await client.post(
        f"{w.base}/bulk", json={"ids": ids, "op": "reject", "reason": "Decorative images"}, headers=w.headers
    )
    assert [u["reject_reason"] for u in ok.json()["updated"]] == ["Decorative images"] * len(ids)
    assert (
        await client.post(f"{w.base}/bulk", json={"ids": [], "op": "approve"}, headers=w.headers)
    ).status_code == 422
    assert (
        await client.post(f"{w.base}/bulk", json={"ids": ids, "op": "delete"}, headers=w.headers)
    ).status_code == 422
    reopened = await client.post(f"{w.base}/bulk", json={"ids": ids, "op": "reopen"}, headers=w.headers)
    assert len(reopened.json()["updated"]) == len(ids)


# ---- editing -----------------------------------------------------------------------------------------------
async def test_an_edit_goes_through_the_validators_and_keeps_the_ai_original(
    client: AsyncClient, session: AsyncSession, fixture_site: FixtureServer
) -> None:
    w = await world(client, session, fixture_site)
    title = first(await listing(client, w), "rewrite_title")
    ai_value = title["proposed_value"]
    url = f"{w.base}/{title['id']}"

    bad = await client.patch(url, json={"proposed_value": "Too short"}, headers=w.headers)
    detail = bad.json()["detail"]
    assert bad.status_code == 422 and detail["problems"] and "at least 30" in detail["problems"][0]
    assert (await client.get(url, headers=w.headers)).json()["proposed_value"] == ai_value

    mine = "Hand-written title that says it plainly"
    ok = await client.patch(url, json={"proposed_value": f"  {mine}  "}, headers=w.headers)
    body = ok.json()
    assert ok.status_code == 200 and body["proposed_value"] == mine and body["edited_by_user"] is True
    assert body["original_value"] == ai_value and body["status"] == "proposed"

    await client.post(f"{url}/approve", headers=w.headers)
    again = await client.patch(url, json={"proposed_value": mine + " today"}, headers=w.headers)
    assert again.json()["status"] == "proposed", "a changed value needs a fresh approval"
    assert again.json()["original_value"] == ai_value, "the AI original is not overwritten by a second edit"

    restored = await client.post(f"{url}/restore", headers=w.headers)
    assert restored.json()["proposed_value"] == ai_value and restored.json()["edited_by_user"] is False
    assert (await client.post(f"{url}/restore", headers=w.headers)).status_code == 409


async def test_a_title_cannot_duplicate_another_proposal_or_page(
    client: AsyncClient, session: AsyncSession, fixture_site: FixtureServer
) -> None:
    w = await world(client, session, fixture_site)
    titles = [
        r for r in await listing(client, w) if r["type"] == "rewrite_title" and r["status"] == "proposed"
    ]
    assert len(titles) >= 2
    clash = await client.patch(
        f"{w.base}/{titles[1]['id']}", json={"proposed_value": titles[0]["proposed_value"]}, headers=w.headers
    )
    assert clash.status_code == 422 and any("already" in p for p in clash.json()["detail"]["problems"])


async def test_an_action_waiting_for_ai_can_be_written_by_hand(
    client: AsyncClient, session: AsyncSession, fixture_site: FixtureServer
) -> None:
    w = await world(client, session, fixture_site, ai=False)
    rows = await listing(client, w)
    # no AI at all in this world: everything that needs words waits
    waiting = [r for r in rows if r["status"] == "needs_ai"]
    assert waiting
    meta = next(r for r in waiting if r["type"] == "rewrite_meta")
    text = "A short and honest description of this page that is written by hand and is long enough to pass."
    text = text + " It tells the visitor what to expect."
    assert 120 <= len(text) <= 155, len(text)
    resp = await client.patch(f"{w.base}/{meta['id']}", json={"proposed_value": text}, headers=w.headers)
    assert (
        resp.status_code == 200
        and resp.json()["status"] == "proposed"
        and resp.json()["original_value"] is None
    )


async def test_jsonld_edits_are_validated_against_the_required_properties(
    client: AsyncClient, session: AsyncSession, fixture_site: FixtureServer
) -> None:
    w = await world(client, session, fixture_site)
    rows = await listing(client, w)
    block = next(
        (r for r in rows if r["type"] in ("add_jsonld", "fix_jsonld") and r["status"] == "proposed"), None
    )
    assert block is not None
    url = f"{w.base}/{block['id']}"
    junk = await client.patch(url, json={"proposed_value": "{not json"}, headers=w.headers)
    assert junk.status_code == 422 and junk.json()["detail"]["problems"]
    unsupported = await client.patch(
        url, json={"proposed_value": {"@context": "https://schema.org", "@type": "Recipe"}}, headers=w.headers
    )
    assert unsupported.status_code == 422


async def test_tasks_without_a_value_are_ticked_not_edited(
    client: AsyncClient, session: AsyncSession, fixture_site: FixtureServer
) -> None:
    w = await world(client, session, fixture_site)
    task = first(await listing(client, w), "earn_links")
    url = f"{w.base}/{task['id']}"
    edit = await client.patch(url, json={"proposed_value": {"kind": "checklist"}}, headers=w.headers)
    assert edit.status_code == 422 and "tick off" in edit.json()["detail"]["problems"][0]
    ids = [i["id"] for i in task["proposed_value"]["items"]]
    ok = await client.patch(url, json={"checklist_done": [ids[0], ids[2], ids[0]]}, headers=w.headers)
    assert ok.json()["checklist_done"] == [ids[0], ids[2]]
    bad = await client.patch(url, json={"checklist_done": ["nope"]}, headers=w.headers)
    assert bad.status_code == 409 and "nope" in bad.json()["detail"]
    assert (await client.patch(url, json={}, headers=w.headers)).status_code == 422
    # a re-plan keeps the ticks
    await plan_site(get_sessionmaker(), w.site_id, trigger="test", provider=FakeProvider())
    assert (await client.get(url, headers=w.headers)).json()["checklist_done"] == [ids[0], ids[2]]
    await client.post(f"{url}/done", headers=w.headers)
    assert (await client.get(url, headers=w.headers)).json()["status"] == "manual_done"


async def test_an_edited_action_survives_a_replan(
    client: AsyncClient, session: AsyncSession, fixture_site: FixtureServer
) -> None:
    w = await world(client, session, fixture_site)
    title = first(await listing(client, w), "rewrite_title")
    mine = "Hand-written title that says it plainly"
    await client.patch(f"{w.base}/{title['id']}", json={"proposed_value": mine}, headers=w.headers)
    await plan_site(get_sessionmaker(), w.site_id, trigger="test", provider=FakeProvider())
    after = (await client.get(f"{w.base}/{title['id']}", headers=w.headers)).json()
    assert after["proposed_value"] == mine and after["edited_by_user"]


# ---- summary -----------------------------------------------------------------------------------------------
async def test_summary_counts_match_the_list(
    client: AsyncClient, session: AsyncSession, fixture_site: FixtureServer
) -> None:
    w = await world(client, session, fixture_site)
    rows = await listing(client, w)
    s = (await client.get(f"{w.base}/summary", headers=w.headers)).json()
    assert s["total"] == len(rows) == sum(s["by_status"].values())
    assert set(s["by_risk"]) == {"auto", "approve", "manual"} and sum(s["by_risk"].values()) == len(rows)
    assert s["by_type"]["earn_links"] == 1 and s["needs_ai"] == 0
    assert s["gain_prior"] > 0 and s["gain_measured"] == 0, (
        "no Search Console data in this world: only priors"
    )
    assert s["gain_prior"] == pytest.approx(sum(r["expected_gain"] for r in rows), abs=0.2)
    assert s["autopilot_enabled"] is False and s["plan_running"] is False and s["last_plan"] is None
    assert s["ai"]["configured"] is False

    await client.post(f"{w.base}/{first(rows, 'fix_canonical')['id']}/approve", headers=w.headers)
    s2 = (await client.get(f"{w.base}/summary", headers=w.headers)).json()
    assert s2["by_status"]["approved"] == 1 and s2["gain_approved"] > 0


async def test_summary_reports_needs_ai_and_the_last_plan(
    client: AsyncClient, session: AsyncSession, fixture_site: FixtureServer
) -> None:
    w = await world(client, session, fixture_site)
    ids = [r["id"] for r in await listing(client, w) if r["type"] == "rewrite_title"]
    await session.execute(
        sa.update(Action)
        .where(Action.id.in_([uuid.UUID(i) for i in ids]))
        .values(status=ActionStatus.needs_ai, proposed_value=None)
    )
    site = await session.get(Site, w.site_id)
    assert site is not None
    session.add(
        Job(
            org_id=site.org_id,
            site_id=w.site_id,
            type="plan",
            status=JobStatus.done,
            payload={"site_id": str(w.site_id), "trigger": "audit"},
            result={"created": 3},
            finished_at=datetime.now(UTC),
        )
    )
    await session.commit()
    s = (await client.get(f"{w.base}/summary", headers=w.headers)).json()
    assert s["needs_ai"] == len(ids) > 0
    assert s["last_plan"]["trigger"] == "audit" and s["last_plan"]["result"] == {"created": 3}


# ---- export and hand-off ---------------------------------------------------------------------------------------
async def test_csv_export_is_complete_and_safe_for_spreadsheets(
    client: AsyncClient, session: AsyncSession, fixture_site: FixtureServer
) -> None:
    w = await world(client, session, fixture_site)
    title = first(await listing(client, w), "rewrite_title")
    row = await session.get(Action, uuid.UUID(title["id"]))
    assert row is not None
    row.proposed_value = '=HYPERLINK("http://evil.example","click")'
    row.field = "+cmd"
    await session.commit()
    resp = await client.get(f"{w.base}/export.csv", headers=w.headers)
    assert resp.status_code == 200 and resp.headers["content-type"].startswith("text/csv")
    assert (
        "attachment" in resp.headers["content-disposition"] and ".csv" in resp.headers["content-disposition"]
    )
    parsed = list(csv.DictReader(io.StringIO(resp.text)))
    rows = await listing(client, w)
    assert len(parsed) == len(rows)
    assert list(parsed[0]) == [
        "priority", "status", "risk_class", "type", "page", "field", "current_value", "proposed_value", "why",
        "expected_extra_clicks_28d", "gain_basis", "confidence", "effort_1_to_5", "edited_by_person", "reject_reason",
    ]  # fmt: skip
    mine = next(p for p in parsed if p["field"].endswith("+cmd"))
    assert mine["proposed_value"].startswith("'=HYPERLINK") and mine["field"] == "'+cmd"
    assert [float(p["priority"]) for p in parsed] == sorted(
        (float(p["priority"]) for p in parsed), reverse=True
    )
    assert all(not p[c].startswith(("=", "+", "@")) for p in parsed for c in p)
    only = await client.get(f"{w.base}/export.csv", params={"status": "approved"}, headers=w.headers)
    assert len(list(csv.DictReader(io.StringIO(only.text)))) == 0


async def test_handoff_lists_approved_changes_with_ready_to_paste_markup(
    client: AsyncClient, session: AsyncSession, fixture_site: FixtureServer
) -> None:
    w = await world(client, session, fixture_site)
    empty = (await client.get(f"{w.base}/handoff", headers=w.headers)).json()
    assert "Nothing is approved yet" not in empty["text"] or empty["count"] == 0
    rows = await listing(client, w)
    ids = [first(rows, t)["id"] for t in ("rewrite_title", "fix_canonical")]
    await client.post(f"{w.base}/bulk", json={"ids": ids, "op": "approve"}, headers=w.headers)
    out = (await client.get(f"{w.base}/handoff", headers=w.headers)).json()
    text = out["text"]
    assert "<title>" in text and 'rel="canonical"' in text and "PAGE CHANGES" in text
    assert out["includes_unapproved"] is False and out["count"] >= 2
    assert "Technical tasks".upper() in text, "developer tasks need no approval"
    more = (
        await client.get(f"{w.base}/handoff", params={"include_proposed": True}, headers=w.headers)
    ).json()
    assert more["count"] > out["count"] and more["includes_unapproved"] is True
    assert "earn links" not in text.lower(), "business tasks are not for the developer"


# ---- AI settings -----------------------------------------------------------------------------------------------
async def test_ai_settings_show_key_presence_never_the_key(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    tokens = await register(client, "a@example.com")
    h = auth(tokens)
    off = (await client.get("/ai/status", headers=h)).json()
    assert off["configured"] is False and off["anthropic_key_present"] is False
    assert off["monthly_token_budget"] == 2_000_000 and off["budget_is_custom"] is False
    assert off["tokens_used_this_month"] == 0 and off["usage_by_purpose"] == []

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-super-secret-value")
    get_settings.cache_clear()
    try:
        on = await client.get("/ai/status", headers=h)
        assert on.json()["configured"] is True and on.json()["provider"] == "anthropic"
        assert on.json()["model"] == "claude-sonnet-5-5" and on.json()["anthropic_key_present"] is True
        assert "super-secret" not in on.text
    finally:
        get_settings.cache_clear()
    assert (await client.get("/ai/status")).status_code == 401


async def test_only_admins_set_the_budget(client: AsyncClient, session: AsyncSession) -> None:
    tokens = await register(client, "owner@example.com")
    h = auth(tokens)
    resp = await client.put("/ai/budget", json={"monthly_token_budget": 50_000}, headers=h)
    assert (
        resp.status_code == 200
        and resp.json()["monthly_token_budget"] == 50_000
        and resp.json()["budget_is_custom"]
    )
    assert (await client.put("/ai/budget", json={"monthly_token_budget": -1}, headers=h)).status_code == 422
    reset = await client.put("/ai/budget", json={"monthly_token_budget": None}, headers=h)
    assert reset.json()["monthly_token_budget"] == 2_000_000 and not reset.json()["budget_is_custom"]

    other = await register(client, "m@example.com")
    session.add(
        Membership(
            org_id=uuid.UUID(tokens["orgs"][0]["id"]),
            user_id=uuid.UUID(other["user"]["id"]),
            role=Role.member,
        )
    )
    await session.commit()
    member_in_org = auth(other, tokens["orgs"][0]["id"])
    assert (await client.get("/ai/status", headers=member_in_org)).status_code == 200
    assert (
        await client.put("/ai/budget", json={"monthly_token_budget": 1}, headers=member_in_org)
    ).status_code == 403


def test_risk_class_enum_is_what_the_board_filters_on() -> None:
    assert {r.value for r in RiskClass} == {"auto", "approve", "manual"}
