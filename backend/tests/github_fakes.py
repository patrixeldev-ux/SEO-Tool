"""A stateful pretend GitHub API (respx) with branches, commits, files and pull requests."""

from __future__ import annotations

import base64
import json
import re
import uuid
from typing import Any
from urllib.parse import unquote

import httpx
import respx

API = "https://api.github.com"
REPO = "acme/site"


class FakeGithub:
    def __init__(self, files: dict[str, str], default_branch: str = "main", push: bool = True) -> None:
        self.default_branch = default_branch
        self.push = push
        self.commits: dict[str, dict[str, str]] = {}
        self.branches: dict[str, str] = {}
        self.prs: dict[int, dict[str, Any]] = {}
        self.writes: list[tuple[str, str, str]] = []  # (method, route, branch) of every change we make
        self.requests: list[tuple[str, str]] = []
        self.fail_put_for: set[str] = set()
        root = self._commit(files)
        self.branches[default_branch] = root
        self.root_sha = root

    def _commit(self, files: dict[str, str]) -> str:
        sha = uuid.uuid4().hex
        self.commits[sha] = dict(files)
        return sha

    def files_at(self, ref: str) -> dict[str, str] | None:
        sha = self.branches.get(ref, ref)
        return self.commits.get(sha)

    def merge(self, number: int) -> None:
        pr = self.prs[number]
        files = self.files_at(pr["head"]) or {}
        self.branches[pr["base"]] = self._commit(files)
        pr["merged"] = True
        pr["state"] = "closed"

    def install(self, mock: respx.MockRouter) -> None:
        mock.route(host="api.github.com").mock(side_effect=self.handle)

    # ---- request router ----
    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        method = request.method
        self.requests.append((method, path))
        assert request.headers["authorization"].startswith("Bearer ")
        base = f"/repos/{REPO}"
        if not path.startswith(base):
            return httpx.Response(404, json={"message": "Not Found"})
        route = path[len(base) :]
        body = json.loads(request.content) if request.content else {}
        if route == "" and method == "GET":
            return httpx.Response(
                200, json={"default_branch": self.default_branch, "permissions": {"push": self.push}}
            )
        m = re.fullmatch(r"/git/ref/heads/(.+)", route)
        if m and method == "GET":
            name = unquote(m.group(1))
            if name not in self.branches:
                return httpx.Response(404, json={"message": "Not Found"})
            return httpx.Response(200, json={"object": {"sha": self.branches[name]}})
        if route == "/git/refs" and method == "POST":
            ref = body["ref"].removeprefix("refs/heads/")
            self.writes.append((method, route, ref))
            if ref in self.branches:
                return httpx.Response(422, json={"message": "Reference already exists"})
            self.branches[ref] = self.branches.get(body["sha"], body["sha"])
            if self.branches[ref] not in self.commits:
                self.branches[ref] = self._commit(self.commits[body["sha"]])
            return httpx.Response(201, json={"ref": body["ref"]})
        m = re.fullmatch(r"/git/refs/heads/(.+)", route)
        if m and method == "DELETE":
            name = unquote(m.group(1))
            self.writes.append((method, route, name))
            self.branches.pop(name, None)
            return httpx.Response(204)
        m = re.fullmatch(r"/contents/(.+)", route)
        if m:
            file_path = unquote(m.group(1))
            if method == "GET":
                files = self.files_at(request.url.params.get("ref", self.default_branch))
                if files is None or file_path not in files:
                    return httpx.Response(404, json={"message": "Not Found"})
                text = files[file_path]
                return httpx.Response(
                    200,
                    json={
                        "type": "file",
                        "encoding": "base64",
                        "size": len(text),
                        "sha": f"blob-{hash(text) & 0xFFFFFF:x}",
                        "content": base64.b64encode(text.encode()).decode(),
                    },
                )
            if method == "PUT":
                branch = body["branch"]
                self.writes.append((method, route, branch))
                if file_path in self.fail_put_for:
                    return httpx.Response(422, json={"message": "nope", "errors": [{"message": "boom"}]})
                files = dict(self.files_at(branch) or {})
                if file_path not in files or body["sha"] != f"blob-{hash(files[file_path]) & 0xFFFFFF:x}":
                    return httpx.Response(409, json={"message": "sha does not match"})
                files[file_path] = base64.b64decode(body["content"]).decode()
                self.branches[branch] = self._commit(files)
                return httpx.Response(200, json={"content": {"path": file_path}})
        if route == "/pulls" and method == "POST":
            number = len(self.prs) + 1
            self.writes.append((method, route, body["head"]))
            self.prs[number] = {
                "number": number,
                "head": body["head"],
                "base": body["base"],
                "title": body["title"],
                "body": body["body"],
                "state": "open",
                "merged": False,
            }
            return httpx.Response(
                201, json={"number": number, "html_url": f"https://github.com/{REPO}/pull/{number}"}
            )
        m = re.fullmatch(r"/pulls/(\d+)", route)
        if m:
            number = int(m.group(1))
            pr = self.prs.get(number)
            if pr is None:
                return httpx.Response(404, json={"message": "Not Found"})
            if method == "PATCH":
                self.writes.append((method, route, pr["head"]))
                pr["state"] = body.get("state", pr["state"])
            return httpx.Response(200, json={**pr, "html_url": f"https://github.com/{REPO}/pull/{number}"})
        return httpx.Response(404, json={"message": f"unhandled {method} {route}"})
