"""`python -m searchos.create_user`: the way the first admin is created on a production server."""

from __future__ import annotations

import os
import subprocess
import sys

import pytest
from httpx import AsyncClient

from searchos.create_user import CreateUserError, create_user, validate_password
from tests.conftest import BACKEND_DIR, PASSWORD


def run_cli(*args: str, stdin: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        [sys.executable, "-m", "searchos.create_user", *args],
        input=stdin,
        capture_output=True,
        text=True,
        cwd=BACKEND_DIR,
        env=os.environ.copy(),
        timeout=60,
        check=False,
    )


async def test_cli_creates_an_owner_who_can_log_in(client: AsyncClient, test_env: dict[str, str]) -> None:
    done = run_cli(
        "--email", "Admin@Example.com", "--org", "Patrixel", "--password-stdin", stdin=PASSWORD + "\n"
    )
    assert done.returncode == 0, done.stderr
    assert "admin@example.com" in done.stdout
    resp = await client.post("/auth/login", json={"email": "admin@example.com", "password": PASSWORD})
    assert resp.status_code == 200, resp.text
    orgs = resp.json()["orgs"]
    assert [(o["name"], o["role"]) for o in orgs] == [("Patrixel", "owner")]


async def test_cli_refuses_a_duplicate_email(client: AsyncClient, test_env: dict[str, str]) -> None:
    args = ("--email", "dup@example.com", "--org", "A", "--password-stdin")
    assert run_cli(*args, stdin=PASSWORD + "\n").returncode == 0
    again = run_cli(*args, stdin=PASSWORD + "\n")
    assert again.returncode == 1
    assert "already exists" in again.stderr
    assert "Traceback" not in again.stderr


async def test_cli_rejects_a_short_password_and_needs_a_terminal(test_env: dict[str, str]) -> None:
    short = run_cli("--email", "s@example.com", "--org", "S", "--password-stdin", stdin="short\n")
    assert short.returncode == 1 and "at least 12" in short.stderr
    no_tty = run_cli("--email", "s@example.com", "--org", "S")
    assert no_tty.returncode == 1 and "--password-stdin" in no_tty.stderr


async def test_password_is_not_a_command_line_option(test_env: dict[str, str]) -> None:
    done = run_cli("--email", "s@example.com", "--org", "S", "--password", PASSWORD)
    assert done.returncode == 2  # argparse: unrecognised argument


async def test_create_user_validates_input(test_env: dict[str, str]) -> None:
    with pytest.raises(CreateUserError, match="email"):
        await create_user(email="not-an-email", org_name="X", password=PASSWORD)
    with pytest.raises(CreateUserError, match="organisation"):
        await create_user(email="a@example.com", org_name="  ", password=PASSWORD)
    with pytest.raises(CreateUserError):
        validate_password("x" * 11)
    validate_password("x" * 12)
