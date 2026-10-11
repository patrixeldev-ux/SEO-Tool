import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from searchos.models import Membership, Role
from tests.conftest import auth, register


async def join(client: AsyncClient, session: AsyncSession, owner: dict, email: str, role: Role) -> dict:
    """Register `email` (own org) and add them to the owner's org with `role`."""
    tokens = await register(client, email, org_name=f"{email} org")
    session.add(
        Membership(
            org_id=uuid.UUID(owner["orgs"][0]["id"]), user_id=uuid.UUID(tokens["user"]["id"]), role=role
        )
    )
    await session.commit()
    return tokens


@pytest.mark.parametrize(
    ("role", "can_read", "can_create", "can_delete"),
    [
        (Role.client_viewer, True, False, False),
        (Role.member, True, True, False),
        (Role.admin, True, True, True),
        (Role.owner, True, True, True),
    ],
)
async def test_role_matrix_on_sites(
    client: AsyncClient,
    session: AsyncSession,
    role: Role,
    can_read: bool,
    can_create: bool,
    can_delete: bool,
) -> None:
    owner = await register(client, "owner@example.com")
    org_id = owner["orgs"][0]["id"]
    site = (await client.post("/sites", json={"domain": "base.example.com"}, headers=auth(owner))).json()
    user = await join(client, session, owner, f"user-{role.value}@example.com", role)
    headers = auth(user, org_id)

    assert ((await client.get("/sites", headers=headers)).status_code == 200) is can_read
    created = await client.post("/sites", json={"domain": "new.example.com"}, headers=headers)
    assert (created.status_code == 201) is can_create
    if not can_create:
        assert created.status_code == 403
    deleted = await client.delete(f"/sites/{site['id']}", headers=headers)
    assert (deleted.status_code == 204) is can_delete
    if not can_delete:
        assert deleted.status_code == 403


async def test_only_admins_can_toggle_autopilot(client: AsyncClient, session: AsyncSession) -> None:
    owner = await register(client, "owner@example.com")
    org_id = owner["orgs"][0]["id"]
    site = (await client.post("/sites", json={"domain": "base.example.com"}, headers=auth(owner))).json()
    member = await join(client, session, owner, "member@example.com", Role.member)

    renamed = await client.patch(
        f"/sites/{site['id']}", json={"display_name": "Nice"}, headers=auth(member, org_id)
    )
    assert renamed.status_code == 200
    blocked = await client.patch(
        f"/sites/{site['id']}", json={"autopilot_enabled": True}, headers=auth(member, org_id)
    )
    assert blocked.status_code == 403
    allowed = await client.patch(
        f"/sites/{site['id']}", json={"autopilot_enabled": True}, headers=auth(owner)
    )
    assert allowed.json()["autopilot_enabled"] is True


async def test_no_membership_means_forbidden(client: AsyncClient, session: AsyncSession) -> None:
    from sqlalchemy import delete

    tokens = await register(client, "a@example.com")
    await session.execute(delete(Membership))
    await session.commit()
    assert (await client.get("/sites", headers=auth(tokens))).status_code == 403
