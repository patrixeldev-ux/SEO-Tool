import uuid

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tests.conftest import auth, register


async def create_site(client: AsyncClient, tokens: dict, domain: str = "example.com", **kw: str) -> dict:
    resp = await client.post("/sites", json={"domain": domain, **kw}, headers=auth(tokens))
    assert resp.status_code == 201, resp.text
    return resp.json()


async def test_create_list_get_update_delete(client: AsyncClient) -> None:
    tokens = await register(client, "a@example.com")
    site = await create_site(client, tokens, "https://WWW.Example.com/blog", display_name="Example")
    assert site["domain"] == "example.com"
    assert site["uses_www"] is True
    assert site["display_name"] == "Example"
    assert site["autopilot_enabled"] is False  # default OFF
    assert site["settings"] == {}
    assert site["org_id"] == tokens["orgs"][0]["id"]

    listed = await client.get("/sites", headers=auth(tokens))
    assert [s["id"] for s in listed.json()] == [site["id"]]

    got = await client.get(f"/sites/{site['id']}", headers=auth(tokens))
    assert got.json()["domain"] == "example.com"

    patched = await client.patch(
        f"/sites/{site['id']}",
        json={
            "display_name": "Renamed",
            "settings": {"crawl": {"max_pages": 100}},
            "autopilot_enabled": True,
        },
        headers=auth(tokens),
    )
    assert patched.status_code == 200
    assert patched.json()["display_name"] == "Renamed"
    assert patched.json()["settings"] == {"crawl": {"max_pages": 100}}
    assert patched.json()["autopilot_enabled"] is True

    assert (await client.delete(f"/sites/{site['id']}", headers=auth(tokens))).status_code == 204
    assert (await client.get(f"/sites/{site['id']}", headers=auth(tokens))).status_code == 404


async def test_display_name_defaults_to_domain(client: AsyncClient) -> None:
    tokens = await register(client, "a@example.com")
    site = await create_site(client, tokens, "shop.example.org")
    assert site["display_name"] == "shop.example.org"


async def test_idn_domain_is_stored_as_punycode(client: AsyncClient) -> None:
    tokens = await register(client, "a@example.com")
    site = await create_site(client, tokens, "Bücher.de")
    assert site["domain"] == "xn--bcher-kva.de"


async def test_invalid_domains_rejected(client: AsyncClient) -> None:
    tokens = await register(client, "a@example.com")
    for bad in ("localhost", "http://", "10.0.0.1", "example.com:8080", "not a domain"):
        resp = await client.post("/sites", json={"domain": bad}, headers=auth(tokens))
        assert resp.status_code == 422, bad


async def test_duplicate_domain_in_same_org_conflicts_even_with_www_or_scheme(client: AsyncClient) -> None:
    tokens = await register(client, "a@example.com")
    await create_site(client, tokens, "example.com")
    for variant in ("www.example.com", "https://EXAMPLE.com/", "example.com."):
        resp = await client.post("/sites", json={"domain": variant}, headers=auth(tokens))
        assert resp.status_code == 409, variant


async def test_same_domain_allowed_in_different_orgs(client: AsyncClient) -> None:
    a = await register(client, "a@example.com")
    b = await register(client, "b@example.com")
    await create_site(client, a, "shared.example.com")
    await create_site(client, b, "shared.example.com")


async def test_user_cannot_read_or_modify_another_orgs_site(client: AsyncClient) -> None:
    a = await register(client, "a@example.com", org_name="Org A")
    b = await register(client, "b@example.com", org_name="Org B")
    site = await create_site(client, a, "secret.example.com")
    sid = site["id"]

    # B can't read it...
    assert (await client.get(f"/sites/{sid}", headers=auth(b))).status_code == 404
    # ...can't see it in listings...
    assert (await client.get("/sites", headers=auth(b))).json() == []
    # ...can't change or delete it...
    assert (
        await client.patch(f"/sites/{sid}", json={"display_name": "pwned"}, headers=auth(b))
    ).status_code == 404
    assert (await client.delete(f"/sites/{sid}", headers=auth(b))).status_code == 404
    # ...and 404 is indistinguishable from "does not exist".
    missing = await client.get(f"/sites/{uuid.uuid4()}", headers=auth(b))
    assert missing.status_code == 404
    assert missing.json() == (await client.get(f"/sites/{sid}", headers=auth(b))).json()

    # B cannot reach A's data by naming A's org in the header either.
    forged = await client.get("/sites", headers=auth(b, org_id=a["orgs"][0]["id"]))
    assert forged.status_code == 403
    # A still has it, unchanged.
    still = await client.get(f"/sites/{sid}", headers=auth(a))
    assert still.json()["display_name"] == "secret.example.com"


async def test_body_cannot_smuggle_an_org_id(client: AsyncClient) -> None:
    a = await register(client, "a@example.com")
    b = await register(client, "b@example.com")
    resp = await client.post(
        "/sites",
        json={"domain": "mine.example.com", "org_id": a["orgs"][0]["id"]},
        headers=auth(b),
    )
    assert resp.status_code == 201
    assert resp.json()["org_id"] == b["orgs"][0]["id"]
    assert (await client.get("/sites", headers=auth(a))).json() == []


async def test_sites_require_authentication(client: AsyncClient) -> None:
    assert (await client.get("/sites")).status_code == 401
    assert (await client.post("/sites", json={"domain": "example.com"})).status_code == 401


async def test_x_org_id_switches_active_org(client: AsyncClient) -> None:
    tokens = await register(client, "a@example.com", org_name="First")
    second = await client.post("/orgs", json={"name": "Second"}, headers=auth(tokens))
    assert second.status_code == 201
    second_id = second.json()["id"]
    first_id = tokens["orgs"][0]["id"]

    await client.post("/sites", json={"domain": "one.example.com"}, headers=auth(tokens, first_id))
    await client.post("/sites", json={"domain": "two.example.com"}, headers=auth(tokens, second_id))

    in_first = (await client.get("/sites", headers=auth(tokens, first_id))).json()
    in_second = (await client.get("/sites", headers=auth(tokens, second_id))).json()
    default = (await client.get("/sites", headers=auth(tokens))).json()  # no header = oldest membership
    assert [s["domain"] for s in in_first] == ["one.example.com"]
    assert [s["domain"] for s in in_second] == ["two.example.com"]
    assert default == in_first

    orgs = (await client.get("/orgs", headers=auth(tokens))).json()
    assert [o["name"] for o in orgs] == ["First", "Second"]


async def test_seed_demo_is_idempotent_and_usable(client: AsyncClient, session: AsyncSession) -> None:
    from searchos.seed_demo import DEFAULT_EMAIL, DEFAULT_PASSWORD, seed

    await seed(DEFAULT_EMAIL, DEFAULT_PASSWORD)
    await seed(DEFAULT_EMAIL, DEFAULT_PASSWORD)  # second run must not duplicate anything

    resp = await client.post("/auth/login", json={"email": DEFAULT_EMAIL, "password": DEFAULT_PASSWORD})
    assert resp.status_code == 200
    tokens = resp.json()
    assert [(o["name"], o["role"]) for o in tokens["orgs"]] == [
        ("Demo Agency", "owner"),
        ("Demo Client Co", "admin"),
    ]
    agency = (await client.get("/sites", headers=auth(tokens, tokens["orgs"][0]["id"]))).json()
    client_co = (await client.get("/sites", headers=auth(tokens, tokens["orgs"][1]["id"]))).json()
    assert len(agency) == 2
    assert len(client_co) == 1
