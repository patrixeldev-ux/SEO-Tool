"""Seed a demo account for local development:  python -m searchos.seed_demo

Creates (idempotently) a demo user who owns "Demo Agency" with two sites and is an admin of a second
workspace "Demo Client Co" with one site, so the org switcher has something to switch between.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from searchos.core import security
from searchos.core.auth import normalize_email
from searchos.core.config import get_settings
from searchos.core.db import dispose_engine, get_sessionmaker
from searchos.core.domain import normalize_domain
from searchos.models import Membership, Org, Role, Site, User

DEFAULT_EMAIL = "demo@example.com"
DEFAULT_PASSWORD = "demo-password-123"

WORKSPACES: list[tuple[str, Role, list[tuple[str, str]]]] = [
    (
        "Demo Agency",
        Role.owner,
        [("acme-widgets.example", "Acme Widgets"), ("northwind-travel.example", "Northwind Travel")],
    ),
    ("Demo Client Co", Role.admin, [("clientco.example", "Client Co")]),
]


async def _get_or_create_org(session: AsyncSession, user: User, name: str, role: Role) -> Org:
    org = await session.scalar(
        sa.select(Org)
        .join(Membership, Membership.org_id == Org.id)
        .where(Membership.user_id == user.id, Org.name == name)
    )
    if org is None:
        org = Org(name=name)
        session.add(org)
        await session.flush()
        session.add(Membership(org_id=org.id, user_id=user.id, role=role))
    return org


async def seed(email: str, password: str) -> None:
    async with get_sessionmaker()() as session:
        user = await session.scalar(sa.select(User).where(User.email == normalize_email(email)))
        if user is None:
            user = User(
                email=normalize_email(email),
                password_hash=security.hash_password(password),
                full_name="Demo User",
            )
            session.add(user)
            await session.flush()
        for org_name, role, sites in WORKSPACES:
            org = await _get_or_create_org(session, user, org_name, role)
            for raw_domain, display_name in sites:
                normalized = normalize_domain(raw_domain)
                exists = await session.scalar(
                    sa.select(Site.id).where(Site.org_id == org.id, Site.domain == normalized.domain)
                )
                if exists is None:
                    session.add(
                        Site(
                            org_id=org.id,
                            domain=normalized.domain,
                            uses_www=normalized.uses_www,
                            display_name=display_name,
                        )
                    )
        await session.commit()


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed a demo account for local development.")
    parser.add_argument("--email", default=DEFAULT_EMAIL)
    parser.add_argument("--password", default=DEFAULT_PASSWORD)
    args = parser.parse_args()

    if get_settings().is_production:
        print("Refusing to seed demo data when ENVIRONMENT=production.", file=sys.stderr)
        return 1

    async def run() -> None:
        try:
            await seed(args.email, args.password)
        finally:
            await dispose_engine()

    asyncio.run(run())
    print("Demo data ready.")
    print(f"  Login:    {normalize_email(args.email)}")
    print(f"  Password: {args.password}")
    print("  Workspaces: Demo Agency (owner, 2 sites), Demo Client Co (admin, 1 site)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
