import type {
  AppNotification,
  AutopilotSettings,
  DeployBatch,
  DeployConnection,
  DeployMethod,
  Deployment,
  Ownership,
} from "@/lib/api/types";

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

export function deployment(over: Partial<Deployment> = {}): Deployment {
  return {
    id: "d1",
    action_id: "a1",
    batch_id: "b1",
    method: "cloudflare",
    url: "https://shop.example.com/beans",
    action_type: "rewrite_title",
    field: "title",
    status: "verified",
    error: null,
    ops: [{ op: "title", value: "Fresh roasted coffee beans for home brewing" }],
    before_snapshot: { value: "Beans" },
    after_snapshot: { value: "Fresh roasted coffee beans for home brewing" },
    pr_url: null,
    can_rollback: true,
    verify_attempts: 1,
    created_at: "2026-10-13T10:00:00Z",
    deployed_at: "2026-10-13T10:00:01Z",
    verified_at: "2026-10-13T10:00:20Z",
    reverted_at: null,
    ...over,
  };
}

export function batch(over: Partial<DeployBatch> = {}): DeployBatch {
  return {
    id: "b1",
    method: "cloudflare",
    trigger: "manual",
    status: "verified",
    created_by: "u1",
    created_at: "2026-10-13T10:00:00Z",
    finished_at: null,
    summary: {},
    counts: { verified: 1 },
    ...over,
  };
}

export function connection(method: DeployMethod, over: Partial<DeployConnection> = {}): DeployConnection {
  return { method, status: "connected", label: method, last_error: null, last_tested_at: null, meta: {}, chosen: false, ...over };
}

export function createDeployState() {
  return {
    ownership: {
      verified: false,
      verified_at: null,
      method: null,
      checked_at: null,
      error: null,
      token: "tok123",
      dns_host: "shop.example.com",
      dns_value: "searchos-verify=tok123",
      file_path: "/.well-known/searchos-tok123.txt",
      file_url: "https://shop.example.com/.well-known/searchos-tok123.txt",
      file_content: "tok123",
    } as Ownership,
    /** What a "Check now" will find. */
    proofPresent: false,
    connections: [] as DeployConnection[],
    deployMethod: null as DeployMethod | null,
    deployments: [] as Deployment[],
    batches: [] as DeployBatch[],
    notifications: [] as AppNotification[],
    autopilot: { enabled: false, weekly_cap: 20, used_this_week: 3, max_cap: 200, ready: true, note: "" } as AutopilotSettings,
    /** Answers for the next deploy call. */
    deployOutcome: "applied" as Deployment["status"],
    skipped: [] as { action_id: string; reason: string }[],
    testOk: true,
  };
}
export type DeployState = ReturnType<typeof createDeployState>;

export function handleDeployRoute(state: DeployState, url: URL, method: string, body: any): Response | null {
  if (url.pathname === "/notifications" && method === "GET") {
    return json({ unread: state.notifications.filter((n) => !n.read).length, items: state.notifications });
  }
  if (url.pathname === "/notifications/read-all") {
    state.notifications.forEach((n) => (n.read = true));
    return json({ unread: 0, items: state.notifications });
  }
  const m = url.pathname.match(/^\/sites\/([^/]+)\/deploy(\/.*)?$/);
  if (!m) return null;
  const rest = m[2] ?? "";
  const connections = () => ({
    deploy_method: state.deployMethod,
    verified: state.ownership.verified,
    connections: state.connections.map((c) => ({ ...c, chosen: c.method === state.deployMethod })),
  });

  if (rest === "/ownership") return json(state.ownership);
  if (rest === "/ownership/check") {
    if (state.proofPresent) state.ownership = { ...state.ownership, verified: true, verified_at: "2026-10-13T10:00:00Z", method: "dns", error: null };
    else state.ownership = { ...state.ownership, error: "The DNS record was not found yet." };
    return json(state.ownership);
  }
  if (rest === "/connections" && method === "GET") return json(connections());
  if (rest === "/method") {
    const wanted = body.method as DeployMethod | null;
    if (wanted && !state.connections.some((c) => c.method === wanted && c.status === "connected")) {
      return json({ detail: "Connect and test that deployer first." }, 409);
    }
    state.deployMethod = wanted;
    return json(connections());
  }
  const setup = () => ({
    site_token: "site-token-1",
    signing_secret: "secret-abcdef",
    rules_url: "https://api.example.com/edge/rules/site-token-1",
    host: "shop.example.com",
    route: "shop.example.com/*",
    connection: state.connections.find((c) => c.method === "cloudflare")!,
  });
  if (rest.startsWith("/connections/cloudflare") && rest !== "/connections/cloudflare/test") {
    if (rest === "/connections/cloudflare/kill-switch") {
      const c = state.connections.find((x) => x.method === "cloudflare")!;
      c.meta = { ...c.meta, kill_switch: body.off };
      return json(c);
    }
    if (rest === "/connections/cloudflare/setup") {
      return state.connections.some((c) => c.method === "cloudflare") ? json(setup()) : json({ detail: "Cloudflare is not set up for this site yet." }, 404);
    }
    if (!state.connections.some((c) => c.method === "cloudflare")) state.connections.push(connection("cloudflare", { label: "Cloudflare Worker", meta: { kill_switch: false } }));
    return json(setup());
  }
  if (rest === "/connections/wordpress" && method === "PUT") {
    state.connections = state.connections.filter((c) => c.method !== "wordpress");
    state.connections.push(
      connection("wordpress", state.testOk ? { meta: { site_url: body.site_url, username: body.username } } : { status: "needs_attention", last_error: "The password was refused." }),
    );
    return json({ ok: state.testOk, message: state.testOk ? "Connected to WordPress." : "WordPress refused the username or password.", connection: state.connections.at(-1) });
  }
  if (rest === "/connections/github" && method === "PUT") {
    state.connections = state.connections.filter((c) => c.method !== "github");
    state.connections.push(connection("github", { meta: { repo: body.repo, root: body.root, path_rules: body.path_rules } }));
    return json({ ok: true, message: "Connected to GitHub.", connection: state.connections.at(-1) });
  }
  const test = rest.match(/^\/connections\/([a-z]+)\/test$/);
  if (test) {
    const c = state.connections.find((x) => x.method === test[1]);
    if (!c) return json({ detail: "That deployer is not set up for this site yet." }, 404);
    return json({ ok: state.testOk, message: state.testOk ? "The connection works." : "The test failed.", connection: c });
  }
  const del = rest.match(/^\/connections\/([a-z]+)$/);
  if (del && method === "DELETE") {
    state.connections = state.connections.filter((c) => c.method !== del[1]);
    if (state.deployMethod === del[1]) state.deployMethod = null;
    return new Response(null, { status: 204 });
  }
  if (rest === "" && method === "POST") {
    if (!state.ownership.verified) return json({ detail: "Prove you own this website first." }, 409);
    const dep = deployment({ id: `d${state.deployments.length + 1}`, status: state.deployOutcome, batch_id: "b-new" });
    state.deployments.unshift(dep);
    state.batches.unshift(batch({ id: "b-new", status: "applied", counts: { [dep.status]: 1 } }));
    return json({ batch: state.batches[0], deployments: [dep], skipped: state.skipped }, 201);
  }
  if (rest === "/deployments") return json(state.deployments);
  if (rest === "/batches") return json(state.batches);
  const batchMatch = rest.match(/^\/batches\/([^/]+)(\/rollback)?$/);
  if (batchMatch) {
    const b = state.batches.find((x) => x.id === batchMatch[1]);
    if (!b) return json({ detail: "Batch not found" }, 404);
    const deps = state.deployments.filter((d) => d.batch_id === b.id);
    if (batchMatch[2]) {
      deps.forEach((d) => Object.assign(d, { status: "reverted", can_rollback: false }));
      Object.assign(b, { status: "reverted", counts: { reverted: deps.length } });
      return json({ reverted: deps, skipped: [] });
    }
    return json({ batch: b, deployments: deps, skipped: [] });
  }
  if (rest === "/rollback") {
    const reverted = state.deployments.filter((d) => body.deployment_ids.includes(d.id));
    reverted.forEach((d) => Object.assign(d, { status: "reverted", can_rollback: false, after_snapshot: { after_rollback: d.before_snapshot?.value, restored: true } }));
    return json({ reverted, skipped: [] });
  }
  if (rest === "/autopilot") {
    if (method === "PUT" && body.weekly_cap !== undefined) state.autopilot.weekly_cap = body.weekly_cap;
    return json(state.autopilot);
  }
  return null;
}
