"""3.1: the actions table, the action-type catalog (risk classes, never-automated) and the read API."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from searchos.models import Action, Org, Site
from searchos.planner.catalog import (
    ACTION_TYPES,
    NEVER_AUTOMATED,
    OPEN_STATUSES,
    ActionStatus,
    RiskClass,
    effort_for,
    may_auto_deploy,
    risk_class_for,
)
from searchos.planner.lifecycle import restored_status, transition_error
from tests.conftest import auth, register


# ---- catalog -------------------------------------------------------------------------------------
def test_every_type_has_a_label_effort_and_risk() -> None:
    for name, info in ACTION_TYPES.items():
        assert info.type == name and info.label
        assert 1 <= info.effort <= 5
        assert isinstance(info.risk_class, RiskClass)


def test_risk_classes_follow_the_decisions_table() -> None:
    auto = {"rewrite_title", "rewrite_meta", "add_alt", "add_jsonld", "fix_jsonld", "add_internal_links"}
    approve = {"fix_canonical", "collapse_redirect", "fix_link", "refresh_content", "consolidate_pages"}
    assert {t for t, i in ACTION_TYPES.items() if i.risk_class is RiskClass.auto} == auto
    assert {t for t, i in ACTION_TYPES.items() if i.risk_class is RiskClass.approve} == approve
    manual = set(ACTION_TYPES) - auto - approve
    assert {"ssr_task", "robots_review", "noindex_review", "author_eeat", "earn_links"} <= manual
    assert all(risk_class_for(t) is RiskClass.manual for t in manual)


def test_never_automated_types_cannot_auto_deploy() -> None:
    for name in NEVER_AUTOMATED:
        assert name not in {t for t, i in ACTION_TYPES.items() if i.risk_class is RiskClass.auto}
        assert may_auto_deploy(name) is False if name in ACTION_TYPES else True
    for name in ("robots_review", "noindex_review"):
        assert risk_class_for(name) is RiskClass.manual and not may_auto_deploy(name)
    # nothing that is not `auto` may deploy by itself either
    assert not any(may_auto_deploy(t) for t, i in ACTION_TYPES.items() if i.risk_class is not RiskClass.auto)
    assert may_auto_deploy("rewrite_title")


def test_there_is_no_action_type_for_deleting_pages_buying_links_or_touching_noindex_or_disallow() -> None:
    for banned in ("delete_page", "link_exchange", "robots_disallow_change", "noindex_change", "buy_links"):
        assert banned not in ACTION_TYPES


def test_effort_table_values() -> None:
    assert effort_for("rewrite_title") == 1
    assert effort_for("consolidate_pages") == 5


# ---- lifecycle -----------------------------------------------------------------------------------
def test_transitions() -> None:
    P = ActionStatus
    assert transition_error("rewrite_title", P.proposed, P.approved, has_value=True) is None
    assert "no proposed value" in (
        transition_error("rewrite_title", P.proposed, P.approved, has_value=False) or ""
    )
    # a brief has no value to approve, it is the task itself
    assert transition_error("refresh_content", P.proposed, P.approved, has_value=False) is None
    assert "manual task" in (transition_error("earn_links", P.proposed, P.approved, has_value=True) or "")
    assert transition_error("earn_links", P.proposed, P.manual_done, has_value=True) is None
    assert transition_error("rewrite_title", P.proposed, P.manual_done, has_value=True) is not None
    assert transition_error("rewrite_title", P.needs_ai, P.approved, has_value=False) is not None
    assert transition_error("rewrite_title", P.deployed, P.rejected, has_value=True) is not None
    assert transition_error("rewrite_title", P.rejected, P.proposed, has_value=True) is None
    assert restored_status("rewrite_title", has_value=False) is P.needs_ai
    assert restored_status("rewrite_title", has_value=True) is P.proposed
    assert restored_status("refresh_content", has_value=False) is P.proposed


# ---- table ---------------------------------------------------------------------------------------
async def _site(session: AsyncSession) -> Site:
    org = Org(name=f"Org {uuid.uuid4().hex[:6]}")
    session.add(org)
    await session.flush()
    site = Site(org_id=org.id, domain=f"{uuid.uuid4().hex[:8]}.example", display_name="x")
    session.add(site)
    await session.flush()
    return site


def _action(site: Site, **kw: Any) -> Action:
    values: dict[str, Any] = {
        "org_id": site.org_id,
        "site_id": site.id,
        "type": "rewrite_title",
        "risk_class": RiskClass.auto,
        "target_url": "https://x.example/a",
        "field": "title",
        "effort": 1,
    }
    values.update(kw)
    return Action(**values)


async def test_defaults_and_jsonb_round_trip(session: AsyncSession) -> None:
    site = await _site(session)
    session.add(
        _action(site, current_value="Old", proposed_value={"links": [{"to": "/b"}]}, evidence={"why": "x"})
    )
    await session.commit()
    row = (await session.scalars(sa.select(Action))).one()
    assert row.status is ActionStatus.proposed and row.created_by == "system" and row.priority == 0
    assert row.proposed_value == {"links": [{"to": "/b"}]} and row.edited_by_user is False
    assert row.sources == [] and row.generation == {}


@pytest.mark.parametrize("status", list(OPEN_STATUSES))
async def test_only_one_open_action_per_key(session: AsyncSession, status: ActionStatus) -> None:
    site = await _site(session)
    session.add(_action(site, status=status))
    await session.commit()
    session.add(_action(site, status=ActionStatus.proposed))
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()


async def test_closed_actions_do_not_block_a_new_open_one(session: AsyncSession) -> None:
    site = await _site(session)
    for status in (
        ActionStatus.rejected,
        ActionStatus.obsolete,
        ActionStatus.rolled_back,
        ActionStatus.manual_done,
    ):
        session.add(_action(site, status=status))
    session.add(_action(site))
    await session.commit()
    assert await session.scalar(sa.select(sa.func.count()).select_from(Action)) == 5


async def test_null_target_is_part_of_the_key(session: AsyncSession) -> None:
    site = await _site(session)
    session.add(_action(site, type="earn_links", risk_class=RiskClass.manual, target_url=None, field="links"))
    await session.commit()
    session.add(_action(site, type="earn_links", risk_class=RiskClass.manual, target_url=None, field="links"))
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()


async def test_check_constraints(session: AsyncSession) -> None:
    site = await _site(session)
    session.add(_action(site, effort=9))
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()
    site = await _site(session)
    session.add(_action(site, created_by="robot"))
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()


# ---- API -----------------------------------------------------------------------------------------
async def _api_site(client: AsyncClient, tokens: dict[str, Any], domain: str = "plan.example") -> str:
    resp = await client.post("/sites", json={"domain": domain}, headers=auth(tokens))
    assert resp.status_code == 201
    return str(resp.json()["id"])


async def test_list_is_empty_sorted_filtered_and_scoped(client: AsyncClient, session: AsyncSession) -> None:
    tokens = await register(client, "a@example.com")
    site_id = await _api_site(client, tokens)
    assert (await client.get(f"/sites/{site_id}/actions", headers=auth(tokens))).json() == []
    site = await session.get(Site, uuid.UUID(site_id))
    assert site is not None
    session.add_all(
        [
            _action(site, field="title", priority=1.0, current_value="a"),
            _action(site, type="rewrite_meta", field="meta_description", priority=5.0),
            _action(
                site, type="fix_canonical", risk_class=RiskClass.approve, field="canonical", priority=3.0
            ),
            _action(
                site,
                type="earn_links",
                risk_class=RiskClass.manual,
                target_url=None,
                field="links",
                priority=9.0,
            ),
            _action(site, field="title", target_url="https://x.example/old", status=ActionStatus.obsolete),
        ]
    )
    await session.commit()
    url = f"/sites/{site_id}/actions"
    rows = (await client.get(url, headers=auth(tokens))).json()
    assert [r["type"] for r in rows] == ["earn_links", "rewrite_meta", "fix_canonical", "rewrite_title"]
    assert rows[0]["type_label"] == "Earn links and mentions" and rows[3]["current_value"] == "a"
    assert len((await client.get(url, params={"status": "obsolete"}, headers=auth(tokens))).json()) == 1
    by_risk = (await client.get(url, params={"risk_class": "approve"}, headers=auth(tokens))).json()
    assert [r["type"] for r in by_risk] == ["fix_canonical"]
    by_type = (
        await client.get(
            url, params=[("type", "rewrite_title"), ("type", "rewrite_meta")], headers=auth(tokens)
        )
    ).json()
    assert len(by_type) == 2
    manual = (await client.get(url, params={"manual": "true"}, headers=auth(tokens))).json()
    assert [r["type"] for r in manual] == ["earn_links"]
    assert len((await client.get(url, params={"manual": "false"}, headers=auth(tokens))).json()) == 3
    assert (await client.get(url, params={"type": "nope"}, headers=auth(tokens))).status_code == 422
    assert len((await client.get(url, params={"limit": 2}, headers=auth(tokens))).json()) == 2

    other = await register(client, "b@example.com", org_name="Other")
    assert (await client.get(url, headers=auth(other))).status_code == 404
    assert (await client.get(url)).status_code == 401
