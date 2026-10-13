"""Create an account (user + organisation + owner membership) without the web sign-up form.

    python -m searchos.create_user --email you@example.com --org "My Agency"

The password is asked for on the terminal (twice, hidden). For scripts and CI, pass --password-stdin and
write the password on standard input. The password is never accepted as a command-line argument (it would
show up in process lists and shell history). Production has no demo seed: this is how the first admin is made.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import sys
from collections.abc import Sequence

import sqlalchemy as sa

from searchos.core import security
from searchos.core.auth import normalize_email
from searchos.core.db import dispose_engine, get_sessionmaker
from searchos.models import Membership, Org, Role, User

MIN_PASSWORD_LENGTH = 12


class CreateUserError(Exception):
    """A problem the operator can fix; shown without a traceback."""


def validate_password(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise CreateUserError(f"The password must be at least {MIN_PASSWORD_LENGTH} characters long.")


async def create_user(*, email: str, org_name: str, password: str, full_name: str | None = None) -> User:
    email = normalize_email(email)
    if "@" not in email or "." not in email.rsplit("@", 1)[-1]:
        raise CreateUserError(f"'{email}' does not look like an email address.")
    org_name = org_name.strip()
    if not org_name:
        raise CreateUserError("The organisation name must not be empty.")
    validate_password(password)
    async with get_sessionmaker()() as session:
        if await session.scalar(sa.select(User.id).where(User.email == email)) is not None:
            raise CreateUserError(f"An account for {email} already exists.")
        user = User(email=email, password_hash=security.hash_password(password), full_name=full_name)
        org = Org(name=org_name)
        session.add_all([user, org])
        await session.flush()
        session.add(Membership(org_id=org.id, user_id=user.id, role=Role.owner))
        await session.commit()
        return user


def _read_password(from_stdin: bool) -> str:
    if from_stdin:
        password = sys.stdin.readline().rstrip("\r\n")
        if not password:
            raise CreateUserError("No password was provided on standard input.")
        return password
    if not sys.stdin.isatty():
        raise CreateUserError("No terminal to ask for a password. Use --password-stdin or run with a TTY.")
    first = getpass.getpass("Password (min 12 characters): ")
    if getpass.getpass("Repeat password: ") != first:
        raise CreateUserError("The two passwords do not match.")
    return first


async def _run(email: str, org: str, password: str, name: str | None) -> User:
    try:
        return await create_user(email=email, org_name=org, password=password, full_name=name)
    finally:
        await dispose_engine()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m searchos.create_user", description="Create an owner account."
    )
    parser.add_argument("--email", required=True)
    parser.add_argument("--org", required=True, help="Name of the organisation (workspace) to create")
    parser.add_argument("--name", default=None, help="Full name (optional)")
    parser.add_argument("--password-stdin", action="store_true", help="Read the password from standard input")
    args = parser.parse_args(argv)
    try:
        password = _read_password(args.password_stdin)
        validate_password(password)
        user = asyncio.run(_run(args.email, args.org, password, args.name))
    except CreateUserError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(f"Created {user.email} as owner of '{args.org.strip()}'. You can sign in now.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
