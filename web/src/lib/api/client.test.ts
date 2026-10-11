import { describe, expect, it, vi } from "vitest";

import { ApiClient, ApiError } from "./client";

type Handler = (url: string, init: RequestInit) => Response | Promise<Response>;

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function tokens(access: string) {
  return {
    access_token: access,
    token_type: "bearer",
    expires_in: 900,
    refresh_token: null,
    user: { id: "u1", email: "a@example.com", full_name: null },
    orgs: [{ id: "o1", name: "Acme", role: "owner" }],
  };
}

function setup(handler: Handler) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) =>
    handler(String(input), init ?? {}),
  );
  const onAuthLost = vi.fn();
  const client = new ApiClient({ baseUrl: "http://api.test/", fetch: fetchMock as unknown as typeof fetch, onAuthLost });
  return { client, fetchMock, onAuthLost };
}

function authHeader(init: RequestInit): string | undefined {
  return (init.headers as Record<string, string>)["Authorization"];
}

describe("ApiClient", () => {
  it("sends bearer token, org header and credentials", async () => {
    const { client, fetchMock } = setup(() => json([]));
    client.setAccessToken("tok");
    client.setOrgId("org-1");
    await client.listSites();
    const [url, init] = fetchMock.mock.calls[0]!;
    expect(url).toBe("http://api.test/sites");
    const headers = init!.headers as Record<string, string>;
    expect(headers["Authorization"]).toBe("Bearer tok");
    expect(headers["X-Org-Id"]).toBe("org-1");
    expect(init!.credentials).toBe("include");
  });

  it("silently refreshes on 401 and replays the request once with the new token", async () => {
    let sitesCalls = 0;
    const { client, fetchMock, onAuthLost } = setup((url, init) => {
      if (url.endsWith("/auth/refresh")) return json(tokens("fresh"));
      sitesCalls += 1;
      return authHeader(init) === "Bearer fresh" ? json([{ id: "s1" }]) : json({ detail: "Not authenticated" }, 401);
    });
    client.setAccessToken("expired");

    const result = await client.request<unknown[]>("/sites");

    expect(result).toEqual([{ id: "s1" }]);
    expect(sitesCalls).toBe(2); // original + replay
    expect(fetchMock.mock.calls.filter(([u]) => String(u).endsWith("/auth/refresh"))).toHaveLength(1);
    expect(client.getAccessToken()).toBe("fresh");
    expect(onAuthLost).not.toHaveBeenCalled();
  });

  it("shares one refresh call between concurrent 401s (refresh tokens are single use)", async () => {
    let refreshCalls = 0;
    const { client } = setup(async (url, init) => {
      if (url.endsWith("/auth/refresh")) {
        refreshCalls += 1;
        await new Promise((r) => setTimeout(r, 20));
        return json(tokens("fresh"));
      }
      return authHeader(init) === "Bearer fresh" ? json({ ok: true }) : json({ detail: "x" }, 401);
    });
    client.setAccessToken("expired");

    const results = await Promise.all([
      client.request("/sites"),
      client.request("/sites"),
      client.request("/orgs"),
    ]);

    expect(results).toHaveLength(3);
    expect(refreshCalls).toBe(1);
  });

  it("reports a lost session when the refresh is rejected, and does not loop", async () => {
    let protectedCalls = 0;
    const { client, onAuthLost, fetchMock } = setup((url) => {
      if (url.endsWith("/auth/refresh")) return json({ detail: "Invalid or expired refresh token" }, 401);
      protectedCalls += 1;
      return json({ detail: "Not authenticated" }, 401);
    });
    client.setAccessToken("expired");

    await expect(client.request("/sites")).rejects.toMatchObject({ status: 401 });

    expect(protectedCalls).toBe(1); // never replayed without a valid token
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(onAuthLost).toHaveBeenCalledTimes(1);
    expect(client.getAccessToken()).toBeNull();
  });

  it("gives up after one replay if the server still answers 401", async () => {
    let sitesCalls = 0;
    const { client, onAuthLost } = setup((url) => {
      if (url.endsWith("/auth/refresh")) return json(tokens("fresh"));
      sitesCalls += 1;
      return json({ detail: "Not authenticated" }, 401);
    });
    await expect(client.request("/sites")).rejects.toBeInstanceOf(ApiError);
    expect(sitesCalls).toBe(2);
    expect(onAuthLost).toHaveBeenCalledTimes(1);
  });

  it("does not try to refresh when login itself returns 401", async () => {
    const { client, fetchMock, onAuthLost } = setup(() => json({ detail: "Incorrect email or password" }, 401));
    await expect(client.login("a@example.com", "wrong")).rejects.toMatchObject({
      status: 401,
      message: "Incorrect email or password",
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(onAuthLost).not.toHaveBeenCalled();
  });

  it("keeps the session on a network error during refresh", async () => {
    const { client, onAuthLost } = setup((url) => {
      if (url.endsWith("/auth/refresh")) throw new TypeError("Failed to fetch");
      return json({ detail: "x" }, 401);
    });
    client.setAccessToken("expired");
    await expect(client.request("/sites")).rejects.toThrow("Failed to fetch");
    expect(onAuthLost).not.toHaveBeenCalled();
  });

  it("stores the token returned by login and surfaces validation messages", async () => {
    const { client } = setup((url) =>
      url.endsWith("/auth/login")
        ? json(tokens("t1"))
        : json({ detail: [{ loc: ["body", "password"], msg: "String should have at least 10 characters" }] }, 422),
    );
    await client.login("a@example.com", "pw");
    expect(client.getAccessToken()).toBe("t1");
    await expect(client.register({ email: "a@example.com", password: "short", org_name: "x" })).rejects.toMatchObject({
      status: 422,
      message: "password: String should have at least 10 characters",
    });
  });

  it("returns undefined for 204 responses", async () => {
    const { client } = setup(() => new Response(null, { status: 204 }));
    await expect(client.logout()).resolves.toBeUndefined();
    expect(client.getAccessToken()).toBeNull();
  });
});
