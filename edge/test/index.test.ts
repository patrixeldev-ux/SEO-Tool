import { env } from "cloudflare:workers";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { EDGE_HEADER, FRESH_MS, handle, resetMemoryForTests } from "../src/index";
import { sign } from "../src/rules";
import type { Env, Op } from "../src/types";

const NOW = 1_760_000_000_000;
const HTML = "<html><head><title>Old</title></head><body><p>Hello coffee world</p></body></html>";
const pendingWrites: Promise<unknown>[] = [];
const ctx = {
  waitUntil: (p: Promise<unknown>) => void pendingWrites.push(p),
  passThroughOnException() {},
} as unknown as ExecutionContext;
const settle = async () => void (await Promise.all(pendingWrites.splice(0)));
const testEnv = env as unknown as Env;

function page(html = HTML, init: ResponseInit = {}) {
  return async () => new Response(html, { status: 200, headers: { "content-type": "text/html; charset=utf-8" }, ...init });
}

async function rulesResponse(
  secret: string,
  over: Record<string, unknown> = {},
  pages: Record<string, Op[]> = { "/shop": [{ op: "title", value: "New title" }] },
  signWith = secret,
) {
  const body = JSON.stringify({
    v: 1,
    issued_at: Math.floor(NOW / 1000),
    enabled: true,
    host: "shop.example",
    pages,
    ...over,
  });
  return new Response(body, { headers: { "x-searchos-signature": await sign(body, signWith) } });
}

function mockApi(responder: () => Promise<Response> | Response) {
  const calls: string[] = [];
  vi.stubGlobal("fetch", async (input: RequestInfo | URL) => {
    calls.push(String(input));
    return responder();
  });
  return calls;
}

beforeEach(async () => {
  await settle();
  resetMemoryForTests();
  vi.unstubAllGlobals();
  await testEnv.RULES.delete("searchos:rules");
});

const req = (path = "/shop", init: RequestInit = {}) => new Request(`https://shop.example${path}`, init);

describe("applying rules", () => {
  it("rewrites the page from signed rules fetched from the API", async () => {
    const calls = mockApi(() => rulesResponse(testEnv.SIGNING_SECRET));
    const res = await handle(req(), testEnv, ctx, page(), NOW);
    expect(await res.text()).toContain("<title>New title</title>");
    expect(res.headers.get(EDGE_HEADER)).toBe("on");
    expect(calls).toEqual([`https://api.test/edge/rules/${testEnv.SITE_TOKEN}`]);
  });

  it("uses the cache for 5 minutes and fetches again after", async () => {
    const calls = mockApi(() => rulesResponse(testEnv.SIGNING_SECRET));
    await handle(req(), testEnv, ctx, page(), NOW);
    await handle(req(), testEnv, ctx, page(), NOW + FRESH_MS - 1000);
    expect(calls.length).toBe(1);
    await settle();
    resetMemoryForTests(); // a new isolate: the memory cache is gone, the KV copy is still fresh
    await handle(req(), testEnv, ctx, page(), NOW + 1000);
    expect(calls.length).toBe(1);
    resetMemoryForTests();
    await handle(req(), testEnv, ctx, page(), NOW + FRESH_MS + 1000);
    expect(calls.length).toBe(2);
  });

  it("a verification request (?searchos_verify) refreshes rules older than 10 seconds", async () => {
    let title = "One";
    const calls = mockApi(() => rulesResponse(testEnv.SIGNING_SECRET, {}, { "/shop": [{ op: "title", value: title }] }));
    await handle(req(), testEnv, ctx, page(), NOW);
    title = "Two";
    const res = await handle(req("/shop?searchos_verify=ab12"), testEnv, ctx, page(), NOW + 30_000);
    expect(await res.text()).toContain("<title>Two</title>");
    expect(calls.length).toBe(2);
  });

  it("matches www and path normalisation", async () => {
    mockApi(() => rulesResponse(testEnv.SIGNING_SECRET));
    const res = await handle(new Request("https://www.shop.example/shop/?utm=1"), testEnv, ctx, page(), NOW);
    expect(await res.text()).toContain("New title");
  });
});

describe("passthrough (fail open)", () => {
  it("unsigned or wrongly signed rules are ignored", async () => {
    mockApi(() => rulesResponse(testEnv.SIGNING_SECRET, {}, undefined, "someone-elses-secret"));
    const res = await handle(req(), testEnv, ctx, page(), NOW);
    expect(await res.text()).toBe(HTML);
    expect(res.headers.get(EDGE_HEADER)).toBe("pass");
  });

  it("rules older than 24 hours are ignored even when the API is down", async () => {
    mockApi(() => rulesResponse(testEnv.SIGNING_SECRET));
    await handle(req(), testEnv, ctx, page(), NOW);
    await settle();
    mockApi(() => {
      throw new Error("API down");
    });
    resetMemoryForTests();
    const stale = await handle(req(), testEnv, ctx, page(), NOW + 25 * 3600 * 1000);
    expect(await stale.text()).toBe(HTML);
  });

  it("keeps applying signed rules (under 24 h) while the API is down", async () => {
    const body = await rulesResponse(testEnv.SIGNING_SECRET);
    await testEnv.RULES.put(
      "searchos:rules",
      JSON.stringify({ body: await body.clone().text(), sig: body.headers.get("x-searchos-signature"), fetchedAt: NOW - 3_600_000 }),
    );
    mockApi(() => {
      throw new Error("API down");
    });
    const res = await handle(req(), testEnv, ctx, page(), NOW);
    expect(await res.text()).toContain("New title");
  });

  it("kill switch in the rules: enabled false", async () => {
    mockApi(() => rulesResponse(testEnv.SIGNING_SECRET, { enabled: false }));
    const res = await handle(req(), testEnv, ctx, page(), NOW);
    expect(await res.text()).toBe(HTML);
    expect(res.headers.get(EDGE_HEADER)).toBe("off");
  });

  it("kill switch in the environment: no rules are even fetched", async () => {
    const calls = mockApi(() => rulesResponse(testEnv.SIGNING_SECRET));
    const res = await handle(req(), { ...testEnv, SEARCHOS_DISABLED: "1" }, ctx, page(), NOW);
    expect(await res.text()).toBe(HTML);
    expect(calls).toEqual([]);
    expect(res.headers.get(EDGE_HEADER)).toBe("off");
  });

  it("non-HTML, non-200 and non-GET responses pass through untouched", async () => {
    const calls = mockApi(() => rulesResponse(testEnv.SIGNING_SECRET));
    const json = await handle(req(), testEnv, ctx, async () => new Response('{"a":1}', { headers: { "content-type": "application/json" } }), NOW);
    expect(await json.text()).toBe('{"a":1}');
    const notFound = await handle(req(), testEnv, ctx, page(HTML, { status: 404 }), NOW);
    expect(notFound.status).toBe(404);
    expect(await notFound.text()).toBe(HTML);
    const post = await handle(req("/shop", { method: "POST", body: "x" }), testEnv, ctx, page(), NOW);
    expect(await post.text()).toBe(HTML);
    expect(calls).toEqual([]);
  });

  it("a page without rules, or a different host, is untouched", async () => {
    mockApi(() => rulesResponse(testEnv.SIGNING_SECRET));
    const none = await handle(req("/other"), testEnv, ctx, page(), NOW);
    expect(await none.text()).toBe(HTML);
    const foreign = await handle(new Request("https://evil.example/shop"), testEnv, ctx, page(), NOW);
    expect(await foreign.text()).toBe(HTML);
  });

  it("any error in the Worker returns the original page", async () => {
    mockApi(() => rulesResponse(testEnv.SIGNING_SECRET, {}, { "/shop": [{ op: "jsonld", id: "x", value: {}, replace: { types: null as never } }] }));
    const withLd = HTML.replace("</head>", '<script type="application/ld+json">{"@type":"Organization"}</script></head>');
    const res = await handle(req(), testEnv, ctx, page(withLd), NOW);
    expect(res.status).toBe(200);
    expect(await res.text()).toBe(withLd);
    expect(res.headers.get(EDGE_HEADER)).toBe("error");
  });

  it("every response carries the marker header", async () => {
    mockApi(() => new Response("nope", { status: 500 }));
    const res = await handle(req(), testEnv, ctx, page(), NOW);
    expect(res.headers.get(EDGE_HEADER)).toBe("pass");
  });
});
