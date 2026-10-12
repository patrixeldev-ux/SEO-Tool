import { vi } from "vitest";

import { ApiClient } from "@/lib/api/client";
import type {
  Audit,
  AuditChanges,
  AuditIssues,
  AuditPages,
  Org,
  PageDetail,
  Site,
} from "@/lib/api/types";

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

/** In-memory stand-in for the backend, speaking the same JSON as the real API. */
export function createFakeApi(options: { signedIn?: boolean; role?: Org["role"] } = {}) {
  const user = { id: "u1", email: "founder@example.com", full_name: null };
  const orgs: Org[] = [{ id: "o1", name: "Acme Agency", role: options.role ?? "owner" }];
  const sitesByOrg = new Map<string, Site[]>([["o1", []]]);
  let loggedIn = options.signedIn ?? false;
  const audits = new Map<string, Audit[]>();
  const auditData = {
    issues: { groups: [], passed: [] } as AuditIssues,
    changes: {
      previous_audit_id: null,
      previous_score: null,
      score: null,
      score_delta: null,
      new: [],
      fixed: [],
      persisting_count: 0,
      not_rechecked_count: 0,
    } as AuditChanges,
    pages: { total: 0, items: [] } as AuditPages,
    page: null as PageDetail | null,
    /** Called on every GET of the audit list; lets a test move a running audit forward. */
    onPoll: null as ((list: Audit[]) => void) | null,
  };
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
    const siteAudits = url.pathname.match(/^\/sites\/([^/]+)\/audits$/);
    if (siteAudits) {
      const siteId = siteAudits[1]!;
      const list = audits.get(siteId) ?? [];
      if (method === "GET") {
        auditData.onPoll?.(list);
        return json(list);
      }
      if (orgs[0]!.role === "client_viewer") return json({ detail: "Requires role 'member' or higher" }, 403);
      if (list.some((a) => a.status === "queued" || a.status === "running")) {
        return json({ detail: "An audit is already running for this site" }, 409);
      }
      const site = Array.from(sitesByOrg.values()).flat().find((s) => s.id === siteId);
      const audit: Audit = {
        id: `a${list.length + 1}`,
        site_id: siteId,
        job_id: `j${list.length + 1}`,
        status: "queued",
        progress: 0,
        start_url: `https://${site?.domain ?? "example.com"}/`,
        page_limit: body?.page_limit ?? 500,
        pages_crawled: 0,
        urls_discovered: 0,
        created_at: "2026-10-12T10:00:00Z",
        started_at: null,
        finished_at: null,
        error: null,
        warnings: [],
        score: null,
        subscores: {},
        issue_counts: {},
        stop_reason: null,
      };
      audits.set(siteId, [audit, ...list]);
      return json(audit, 202);
    }
    const auditPath = url.pathname.match(/^\/audits\/([^/]+)(\/.*)?$/);
    if (auditPath) {
      const rest = auditPath[2] ?? "";
      if (rest === "/issues") return json(auditData.issues);
      if (rest === "/changes") return json(auditData.changes);
      if (rest === "/pages") return json(auditData.pages);
      if (rest.startsWith("/pages/") && auditData.page) return json(auditData.page);
      if (rest === "/export.csv") {
        return new Response("severity,check_id\nhigh,TITLE_MISSING\n", { status: 200, headers: { "Content-Type": "text/csv" } });
      }
      if (rest === "/cancel" && method === "POST") {
        for (const list of audits.values()) {
          const found = list.find((a) => a.id === auditPath[1]);
          if (found) {
            found.status = "cancelled";
            return json(found);
          }
        }
      }
    }
    return json({ detail: "Not found" }, 404);
  });

  const client = new ApiClient({ baseUrl: "http://api.test", fetch: fetchMock as unknown as typeof fetch });
  return { client, fetchMock, calls, orgs, sitesByOrg, audits, auditData };
}
