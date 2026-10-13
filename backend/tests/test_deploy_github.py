"""4.5: the GitHub deployer against a stateful fake GitHub API."""

from __future__ import annotations

from typing import Any

import pytest
import respx
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from searchos.deploy import github, service
from searchos.deploy.github import GithubDeployer, candidate_files, clean_path_rules
from searchos.models import Action, Deployment
from searchos.planner.catalog import ActionStatus
from tests.deploy_fakes import PAGE, URL, FakeWebsite, fake_registry, make_action, verify_site  # noqa: F401
from tests.deploy_helpers import owner_with_site
from tests.github_fakes import REPO, FakeGithub

TOKEN = "github_pat_" + "x" * 40


@pytest.fixture(autouse=True)
def no_celery(monkeypatch: pytest.MonkeyPatch) -> None:
    from searchos.deploy import schedule

    monkeypatch.setattr(schedule, "enqueue_verifications", lambda ids, countdown=0: None)


# ---- pure ----------------------------------------------------------------------------------------------------
def test_candidate_files_default_and_rules() -> None:
    assert candidate_files("https://a.example/", "", []) == ["index.html"]
    assert candidate_files("https://a.example/", "public", []) == ["public/index.html"]
    assert candidate_files("https://a.example/about/", "public", []) == [
        "public/about.html",
        "public/about/index.html",
    ]
    rules = [{"url": "/blog/*", "file": "content/{1}.html"}, {"url": "/docs/**", "file": "d/{1}.html"}]
    assert candidate_files("https://a.example/blog/hello", "public", rules) == ["content/hello.html"]
    assert candidate_files("https://a.example/docs/a/b", "", rules) == ["d/a/b.html"]
    # a rule that does not match falls back to the defaults
    assert candidate_files("https://a.example/shop/x", "", rules) == ["shop/x.html", "shop/x/index.html"]


def test_rules_are_validated() -> None:
    assert clean_path_rules([{"url": "/b/*", "file": "/b/{1}.html"}]) == [
        {"url": "/b/*", "file": "b/{1}.html"}
    ]
    for bad in (
        [{"url": "b/*", "file": "x.html"}],
        [{"url": "/b", "file": "../etc/x.html"}],
        [{"url": "/b", "file": "x.js"}],
        [{"url": "/b", "file": ""}],
        "nope",
    ):
        with pytest.raises(github.DeployerError):
            clean_path_rules(bad)


# ---- flow ------------------------------------------------------------------------------------------------------------
async def setup(
    client: AsyncClient,
    session: AsyncSession,
    fake_registry: Any,
    mock: respx.MockRouter,
    monkeypatch: pytest.MonkeyPatch,
    files: dict[str, str] | None = None,
    **connect: Any,
) -> tuple[Any, dict[str, str], FakeGithub, FakeWebsite]:
    headers, site_id, _ = await owner_with_site(client)
    site = await verify_site(session, site_id)
    fake_registry.register(GithubDeployer())
    gh = FakeGithub(files if files is not None else {"page.html": PAGE})
    gh.install(mock)
    web = FakeWebsite()
    monkeypatch.setattr(service, "default_fetcher", lambda: web)
    r = await client.put(
        f"/sites/{site_id}/deploy/connections/github",
        json={"repo": REPO, "token": TOKEN, **connect},
        headers=headers,
    )
    assert r.status_code == 200 and r.json()["ok"], r.text
    assert TOKEN not in r.text
    return site, headers, gh, web


@respx.mock(assert_all_called=False)
async def test_deploy_opens_a_pull_request_and_never_touches_the_default_branch(
    client: AsyncClient,
    session: AsyncSession,
    fake_registry: Any,
    monkeypatch: pytest.MonkeyPatch,
    respx_mock: Any,
) -> None:
    site, headers, gh, web = await setup(client, session, fake_registry, respx_mock, monkeypatch)
    title = await make_action(session, site)
    meta = await make_action(
        session, site, "rewrite_meta", field="meta_description", current="Old description", proposed="D" * 130
    )
    r = await client.post(
        f"/sites/{site.id}/deploy", json={"action_ids": [str(title.id), str(meta.id)]}, headers=headers
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert [d["status"] for d in body["deployments"]] == ["pending_merge", "pending_merge"]
    assert body["batch"]["id"] and body["deployments"][0]["pr_url"].endswith("/pull/1")
    branch = f"searchos/{body['batch']['id']}"
    # every write went to the searchos/ branch; the default branch is untouched
    assert gh.writes, "nothing was written"
    for method, route, target in gh.writes:
        assert target.startswith("searchos/") and target != "main", (method, route, target)
    assert gh.files_at("main") == {"page.html": PAGE}
    assert gh.branches["main"] == gh.root_sha
    edited = gh.files_at(branch)["page.html"]  # type: ignore[index]
    assert "<title>A much better title for the coffee page</title>" in edited and "D" * 130 in edited
    assert "Old title" not in edited
    pr = gh.prs[1]
    assert pr["head"] == branch and pr["base"] == "main"
    assert "Nothing goes live until you merge" in pr["body"]
    # two actions on one file = one commit per file, one pull request per batch
    assert sum(1 for m, route, _ in gh.writes if m == "PUT") == 1 and len(gh.prs) == 1
    # the action is deployed-but-not-live; the live page is unchanged
    await session.refresh(title)
    assert title.status is ActionStatus.deployed
    assert web.rules == {} or not any(web.rules.values())


@respx.mock(assert_all_called=False)
async def test_client_refuses_to_write_to_the_default_branch() -> None:
    gh = github.GithubClient("https://api.github.com", TOKEN, REPO)
    gh.default_branch = "main"
    for bad in ("main", "master", "feature/x", "searchos/", "searchos"):
        with pytest.raises(github.DeployerError, match="searchos/ branch"):
            await gh.put_file("a.html", "x", "sha", bad, "m")
        with pytest.raises(github.DeployerError, match="searchos/ branch"):
            await gh.create_branch(bad, "sha")
    # even a branch named like the default one under the prefix rule is fine; a default of "searchos/main" is not
    gh.default_branch = "searchos/main"
    with pytest.raises(github.DeployerError):
        await gh.put_file("a.html", "x", "sha", "searchos/main", "m")


@respx.mock(assert_all_called=False)
async def test_page_file_not_found_or_templated_becomes_needs_manual_with_a_handoff(
    client: AsyncClient,
    session: AsyncSession,
    fake_registry: Any,
    monkeypatch: pytest.MonkeyPatch,
    respx_mock: Any,
) -> None:
    # Next.js-like repository: no .html file for the page at all
    site, headers, gh, _ = await setup(
        client, session, fake_registry, respx_mock, monkeypatch, files={"pages/page.tsx": "export default 1"}
    )
    action = await make_action(session, site)
    r = await client.post(f"/sites/{site.id}/deploy", json={"action_ids": [str(action.id)]}, headers=headers)
    assert r.status_code == 201, r.text
    dep = r.json()["deployments"][0]
    assert dep["status"] == "failed" and "No HTML file for this page" in dep["error"]
    assert not gh.writes and not gh.prs, "no branch, no pull request when nothing can be placed"
    await session.refresh(action)
    assert action.status is ActionStatus.needs_manual
    manual = action.generation["deploy_manual"]
    assert "<title>A much better title for the coffee page</title>" in manual["handoff"]
    assert "developer" in manual["reason"]


@respx.mock(assert_all_called=False)
async def test_a_file_that_is_only_a_template_is_not_edited(
    client: AsyncClient,
    session: AsyncSession,
    fake_registry: Any,
    monkeypatch: pytest.MonkeyPatch,
    respx_mock: Any,
) -> None:
    template = "<html><head><title>{{ page.title }}</title></head><body>{{ content }}</body></html>"
    site, headers, gh, _ = await setup(
        client, session, fake_registry, respx_mock, monkeypatch, files={"page.html": template}
    )
    action = await make_action(session, site)
    r = await client.post(f"/sites/{site.id}/deploy", json={"action_ids": [str(action.id)]}, headers=headers)
    dep = r.json()["deployments"][0]
    assert "built from somewhere else" in dep["error"]
    assert not gh.writes
    await session.refresh(action)
    assert action.status is ActionStatus.needs_manual


@respx.mock(assert_all_called=False)
async def test_two_candidate_files_is_ambiguous(
    client: AsyncClient,
    session: AsyncSession,
    fake_registry: Any,
    monkeypatch: pytest.MonkeyPatch,
    respx_mock: Any,
) -> None:
    site, headers, gh, _ = await setup(
        client,
        session,
        fake_registry,
        respx_mock,
        monkeypatch,
        files={"page.html": PAGE, "page/index.html": PAGE},
    )
    action = await make_action(session, site)
    r = await client.post(f"/sites/{site.id}/deploy", json={"action_ids": [str(action.id)]}, headers=headers)
    assert "More than one file could be this page" in r.json()["deployments"][0]["error"]
    assert not gh.writes


@respx.mock(assert_all_called=False)
async def test_path_rules_locate_the_file(
    client: AsyncClient,
    session: AsyncSession,
    fake_registry: Any,
    monkeypatch: pytest.MonkeyPatch,
    respx_mock: Any,
) -> None:
    site, headers, gh, _ = await setup(
        client,
        session,
        fake_registry,
        respx_mock,
        monkeypatch,
        files={"content/page.html": PAGE},
        path_rules=[{"url": "/*", "file": "content/{1}.html"}],
    )
    action = await make_action(session, site)
    r = await client.post(f"/sites/{site.id}/deploy", json={"action_ids": [str(action.id)]}, headers=headers)
    assert r.json()["deployments"][0]["status"] == "pending_merge"
    assert gh.requests and any("content/page.html" in p for _, p in gh.requests)


@respx.mock(assert_all_called=False)
async def test_rollback_before_merge_closes_the_pull_request_and_deletes_the_branch(
    client: AsyncClient,
    session: AsyncSession,
    fake_registry: Any,
    monkeypatch: pytest.MonkeyPatch,
    respx_mock: Any,
) -> None:
    site, headers, gh, _ = await setup(client, session, fake_registry, respx_mock, monkeypatch)
    action = await make_action(session, site)
    dep = (
        await client.post(f"/sites/{site.id}/deploy", json={"action_ids": [str(action.id)]}, headers=headers)
    ).json()["deployments"][0]
    branch = gh.prs[1]["head"]
    rb = await client.post(
        f"/sites/{site.id}/deploy/rollback", json={"deployment_ids": [dep["id"]]}, headers=headers
    )
    assert rb.status_code == 200 and rb.json()["reverted"][0]["status"] == "reverted", rb.text
    assert gh.prs[1]["state"] == "closed" and not gh.prs[1]["merged"]
    assert branch not in gh.branches
    assert gh.files_at("main") == {"page.html": PAGE}
    await session.refresh(action)
    assert action.status is ActionStatus.rolled_back or action.status is ActionStatus.approved
    again = await client.post(
        f"/sites/{site.id}/deploy/rollback", json={"deployment_ids": [dep["id"]]}, headers=headers
    )
    assert again.json()["reverted"] == [] and len(again.json()["skipped"]) == 1


@respx.mock(assert_all_called=False)
async def test_merge_is_noticed_and_a_merged_change_is_rolled_back_with_a_revert_pull_request(
    client: AsyncClient,
    session: AsyncSession,
    fake_registry: Any,
    monkeypatch: pytest.MonkeyPatch,
    respx_mock: Any,
) -> None:
    from searchos.core.db import get_sessionmaker

    site, headers, gh, web = await setup(client, session, fake_registry, respx_mock, monkeypatch)
    action = await make_action(session, site)
    dep = (
        await client.post(f"/sites/{site.id}/deploy", json={"action_ids": [str(action.id)]}, headers=headers)
    ).json()["deployments"][0]
    # a person merges the pull request
    gh.merge(1)
    ids = await github.poll_pull_requests(get_sessionmaker())
    assert [str(i) for i in ids] == [dep["id"]]
    row = await session.get(Deployment, __import__("uuid").UUID(dep["id"]))
    await session.refresh(row)
    assert row is not None and row.status.value == "applied"
    # nothing more to do while the PR state is unchanged
    assert await github.poll_pull_requests(get_sessionmaker()) == []

    rb = await client.post(
        f"/sites/{site.id}/deploy/rollback", json={"deployment_ids": [dep["id"]]}, headers=headers
    )
    out = rb.json()["reverted"][0]
    assert out["status"] == "reverting" and rb.status_code == 200, rb.text
    revert_branch = gh.prs[2]["head"]
    assert revert_branch.startswith("searchos/revert-") and gh.prs[2]["base"] == "main"
    assert "Old title" in gh.files_at(revert_branch)["page.html"]  # type: ignore[index]
    assert "better title" in gh.files_at("main")["page.html"]  # type: ignore[index]  # main is untouched by the revert
    # the revert is merged -> reverted
    gh.merge(2)
    await github.poll_pull_requests(get_sessionmaker())
    await session.refresh(row)
    assert row.status.value == "reverted"
    assert gh.files_at("main")["page.html"] == PAGE  # type: ignore[index]  # restored byte for byte
    for method, route, target in gh.writes:
        assert target.startswith("searchos/"), (method, route, target)


@respx.mock(assert_all_called=False)
async def test_revert_refuses_when_the_file_was_edited_after_the_merge(
    client: AsyncClient,
    session: AsyncSession,
    fake_registry: Any,
    monkeypatch: pytest.MonkeyPatch,
    respx_mock: Any,
) -> None:
    from searchos.core.db import get_sessionmaker

    site, headers, gh, _ = await setup(client, session, fake_registry, respx_mock, monkeypatch)
    action = await make_action(session, site)
    dep = (
        await client.post(f"/sites/{site.id}/deploy", json={"action_ids": [str(action.id)]}, headers=headers)
    ).json()["deployments"][0]
    gh.merge(1)
    await github.poll_pull_requests(get_sessionmaker())
    gh.commits[gh.branches["main"]]["page.html"] += "\n<!-- someone edited this -->"
    rb = await client.post(
        f"/sites/{site.id}/deploy/rollback", json={"deployment_ids": [dep["id"]]}, headers=headers
    )
    assert rb.json()["reverted"] == [] and "edited since" in rb.json()["skipped"][0]["reason"]
    assert len(gh.prs) == 1


@respx.mock(assert_all_called=False)
async def test_a_pull_request_closed_without_merging_is_noticed(
    client: AsyncClient,
    session: AsyncSession,
    fake_registry: Any,
    monkeypatch: pytest.MonkeyPatch,
    respx_mock: Any,
) -> None:
    from searchos.core.db import get_sessionmaker

    site, headers, gh, _ = await setup(client, session, fake_registry, respx_mock, monkeypatch)
    action = await make_action(session, site)
    dep = (
        await client.post(f"/sites/{site.id}/deploy", json={"action_ids": [str(action.id)]}, headers=headers)
    ).json()["deployments"][0]
    gh.prs[1]["state"] = "closed"
    await github.poll_pull_requests(get_sessionmaker())
    row = await session.get(Deployment, __import__("uuid").UUID(dep["id"]))
    await session.refresh(row)
    assert row is not None and row.status.value == "reverted"
    await session.refresh(action)
    assert action.status is ActionStatus.approved  # can be deployed again


@respx.mock(assert_all_called=False)
async def test_token_without_write_access_is_reported(
    client: AsyncClient, session: AsyncSession, fake_registry: Any, respx_mock: Any
) -> None:
    headers, site_id, _ = await owner_with_site(client)
    fake_registry.register(GithubDeployer())
    FakeGithub({"page.html": PAGE}, push=False).install(respx_mock)
    r = await client.put(
        f"/sites/{site_id}/deploy/connections/github", json={"repo": REPO, "token": TOKEN}, headers=headers
    )
    assert (not r.json()["ok"] and "cannot write" in r.json()["message"]) or "not write" in r.json()[
        "message"
    ]
    assert r.json()["connection"]["status"] == "pending"
    bad = await client.put(
        f"/sites/{site_id}/deploy/connections/github",
        json={"repo": "not a repo", "token": TOKEN},
        headers=headers,
    )
    assert bad.status_code == 422


@respx.mock(assert_all_called=False)
async def test_failed_commit_fails_the_deployment_and_cleans_up(
    client: AsyncClient,
    session: AsyncSession,
    fake_registry: Any,
    monkeypatch: pytest.MonkeyPatch,
    respx_mock: Any,
) -> None:
    site, headers, gh, _ = await setup(client, session, fake_registry, respx_mock, monkeypatch)
    gh.fail_put_for.add("page.html")
    action = await make_action(session, site)
    r = await client.post(f"/sites/{site.id}/deploy", json={"action_ids": [str(action.id)]}, headers=headers)
    dep = r.json()["deployments"][0]
    assert dep["status"] == "failed" and "boom" in dep["error"]
    assert not gh.prs and not [b for b in gh.branches if b.startswith("searchos/")], (
        "the empty branch is deleted"
    )
    await session.refresh(action)
    assert action.status is ActionStatus.approved
    assert isinstance(action, Action)
