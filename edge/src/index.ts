import { normalisePath, parseRules } from "./rules";
import { jsonLdBlocksToDrop, needsPrescan, rewrite } from "./rewrite";
import type { Env, Rules } from "./types";

const KV_KEY = "searchos:rules";
export const FRESH_MS = 300_000;
const FORCE_REFRESH_MIN_AGE_MS = 10_000;
export const EDGE_HEADER = "x-searchos-edge";

interface Cached {
  body: string;
  sig: string;
  fetchedAt: number;
}

let memory: { token: string; cached: Cached; rules: Rules } | null = null;

export function resetMemoryForTests(): void {
  memory = null;
}

async function loadRules(env: Env, ctx: ExecutionContext, nowMs: number, force: boolean): Promise<Rules | null> {
  const nowS = Math.floor(nowMs / 1000);
  const mem = memory && memory.token === env.SITE_TOKEN ? memory : null;
  const age = (c: Cached) => nowMs - c.fetchedAt;
  const usable = (c: Cached) => age(c) < FRESH_MS && !(force && age(c) > FORCE_REFRESH_MIN_AGE_MS);
  if (mem && usable(mem.cached)) return mem.rules;

  let kv: Cached | null = null;
  try {
    kv = await env.RULES.get<Cached>(KV_KEY, "json");
  } catch {
    kv = null;
  }
  if (kv && usable(kv)) {
    const rules = await parseRules(kv.body, kv.sig, env.SIGNING_SECRET, nowS);
    if (rules) {
      memory = { token: env.SITE_TOKEN, cached: kv, rules };
      return rules;
    }
  }

  try {
    const resp = await fetch(`${env.API_BASE.replace(/\/+$/, "")}/edge/rules/${encodeURIComponent(env.SITE_TOKEN)}`, {
      headers: { accept: "application/json" },
    });
    if (resp.ok) {
      const body = await resp.text();
      const sig = resp.headers.get("x-searchos-signature") ?? "";
      const rules = await parseRules(body, sig, env.SIGNING_SECRET, nowS);
      if (rules) {
        const cached: Cached = { body, sig, fetchedAt: nowMs };
        memory = { token: env.SITE_TOKEN, cached, rules };
        ctx.waitUntil(env.RULES.put(KV_KEY, JSON.stringify(cached), { expirationTtl: 90_000 }).catch(() => undefined));
        return rules;
      }
    }
  } catch {
    // API unreachable: fall through to the stored rules
  }

  // Stale but still signed and under 24 hours old: keep applying them rather than flapping.
  for (const c of [mem?.cached, kv]) {
    if (!c) continue;
    const rules = await parseRules(c.body, c.sig, env.SIGNING_SECRET, nowS);
    if (rules) return rules;
  }
  return null;
}

function tag(response: Response, state: string): Response {
  try {
    const r = new Response(response.body, response);
    r.headers.set(EDGE_HEADER, state);
    return r;
  } catch {
    return response;
  }
}

function isDisabled(env: Env): boolean {
  const v = (env.SEARCHOS_DISABLED ?? "").toLowerCase();
  return v === "1" || v === "true";
}

/**
 * The whole behaviour of the Worker. Fails open: whatever goes wrong, the visitor (or crawler) gets the page the
 * origin sent. `origin` is injectable for tests.
 */
export async function handle(
  request: Request,
  env: Env,
  ctx: ExecutionContext,
  origin: (r: Request) => Promise<Response> = fetch,
  nowMs: number = Date.now(),
): Promise<Response> {
  const response = await origin(request);
  if (isDisabled(env)) return tag(response, "off");
  try {
    if (request.method !== "GET" || response.status !== 200) return tag(response, "pass");
    if (!/text\/html|application\/xhtml\+xml/i.test(response.headers.get("content-type") ?? "")) {
      return tag(response, "pass");
    }
    const url = new URL(request.url);
    const rules = await loadRules(env, ctx, nowMs, url.searchParams.has("searchos_verify"));
    if (!rules) return tag(response, "pass");
    if (!rules.enabled) return tag(response, "off");
    if (url.hostname.replace(/^www\./, "").toLowerCase() !== rules.host.toLowerCase()) return tag(response, "pass");
    const ops = rules.pages[normalisePath(url.pathname)];
    if (!ops || ops.length === 0) return tag(response, "ready");
    const drop = needsPrescan(ops) ? jsonLdBlocksToDrop(await response.clone().text(), ops) : new Set<number>();
    return tag(rewrite(response, ops, url.origin + url.pathname, drop), "on");
  } catch {
    return tag(response, "error");
  }
}

export default {
  async fetch(request: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
    return handle(request, env, ctx);
  },
} satisfies ExportedHandler<Env>;
