import { vi } from "vitest";

import { ApiClient } from "@/lib/api/client";
import type { Org, Site } from "@/lib/api/types";

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

/** In-memory stand-in for the backend, speaking the same JSON as the real API. */
export function createFakeApi(options: { signedIn?: boolean } = {}) {
  const user = { id: "u1", email: "founder@example.com", full_name: null };
  const orgs: Org[] = [{ id: "o1", name: "Acme Agency", role: "owner" }];
  const sitesByOrg = new Map<string, Site[]>([["o1", []]]);
  let loggedIn = options.signedIn ?? false;
  const calls: { method: string; path: string; headers: Record<string, string>; body?: unknown }[] = [];

  const tokenBody = () => ({
    access_token: "access-1",
    token_type: "bearer",
    expires_in: 900,
    refresh_token: null,
    user,
    orgs,
  });

  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const url = new URL(String(input));
    const method = init?.method ?? "GET";
    const headers = (init?.headers ?? {}) as Record<string, string>;
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ method, path: url.pathname, headers, body });

    if (url.pathname === "/auth/refresh") return loggedIn ? json(tokenBody()) : json({ detail: "Missing refresh token" }, 401);
    if (url.pathname === "/auth/login") {
      if (body.password !== "correct-password") return json({ detail: "Incorrect email or password" }, 401);
      loggedIn = true;
      return json(tokenBody());
    }
    if (url.pathname === "/auth/register") {
      loggedIn = true;
      orgs[0] = { id: "o1", name: body.org_name, role: "owner" };
      return json(tokenBody(), 201);
    }
    if (url.pathname === "/auth/logout") {
      loggedIn = false;
      return new Response(null, { status: 204 });
    }
    if (!headers["Authorization"]) return json({ detail: "Not authenticated" }, 401);

    const orgId = headers["X-Org-Id"] ?? orgs[0]!.id;
    if (url.pathname === "/orgs" && method === "POST") {
      const org: Org = { id: `o${orgs.length + 1}`, name: body.name, role: "owner" };
      orgs.push(org);
      sitesByOrg.set(org.id, []);
      return json(org, 201);
    }
    if (url.pathname === "/sites" && method === "GET") return json(sitesByOrg.get(orgId) ?? []);
    if (url.pathname === "/sites" && method === "POST") {
      const domain = String(body.domain)
        .replace(/^https?:\/\//, "")
        .replace(/^www\./, "")
        .replace(/\/.*$/, "")
        .toLowerCase();
      if (!domain.includes(".")) return json({ detail: "Domain must include a top-level domain, e.g. example.com" }, 422);
      const site: Site = {
        id: `s${(sitesByOrg.get(orgId)?.length ?? 0) + 1}`,
        org_id: orgId,
        domain,
        uses_www: /^(https?:\/\/)?www\./.test(String(body.domain)),
        display_name: body.display_name ?? domain,
        settings: {},
        autopilot_enabled: false,
        created_at: "2026-10-11T10:00:00Z",
      };
      sitesByOrg.get(orgId)?.push(site);
      return json(site, 201);
    }
    return json({ detail: "Not found" }, 404);
  });

  const client = new ApiClient({ baseUrl: "http://api.test", fetch: fetchMock as unknown as typeof fetch });
  return { client, fetchMock, calls, orgs, sitesByOrg };
}
