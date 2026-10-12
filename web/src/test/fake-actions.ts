import type {
  ActionStatus,
  ActionSummary,
  AiSettings,
  Handoff,
  PlanAction,
} from "@/lib/api/types";

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

const OPEN: ActionStatus[] = ["proposed", "needs_ai", "no_suggestion", "approved"];

export const AI_OFF: AiSettings = {
  configured: false,
  provider: null,
  model: null,
  selected_by: null,
  anthropic_key_present: false,
  gemini_key_present: false,
  gemini_model_set: false,
  problem: null,
  monthly_token_budget: 2_000_000,
  budget_is_custom: false,
  tokens_used_this_month: 0,
  month_starts: "2026-10-01",
  usage_by_purpose: [],
};

export function planAction(over: Partial<PlanAction> = {}): PlanAction {
  return {
    id: "a1",
    site_id: "s1",
    type: "rewrite_title",
    type_label: "Rewrite the page title",
    risk_class: "auto",
    target_url: "https://shop.example.com/beans",
    field: "title",
    current_value: "Beans",
    proposed_value: "Fresh roasted coffee beans for home brewing",
    original_value: "Fresh roasted coffee beans for home brewing",
    edited_by_user: false,
    evidence: {
      why: ["The title is too short to tell searchers what the page offers."],
      gain_basis: "prior",
      gain_note: "A prior, not measured: a small fixed guess.",
      issues: [{ check: "TITLE_LENGTH", length: 5 }],
    },
    expected_gain: 3,
    confidence: 0.2,
    effort: 1,
    priority: 1.5,
    status: "proposed",
    created_by: "system",
    source_kind: "audit",
    sources: ["TITLE_LENGTH"],
    generation: { state: "ok" },
    checklist_done: [],
    reject_reason: null,
    decided_at: null,
    created_at: "2026-10-12T10:00:00Z",
    updated_at: "2026-10-12T10:00:00Z",
    ...over,
  };
}

export const CHECKLIST = {
  kind: "checklist",
  title: "Earn links and mentions",
  intro: "Links from other sites that are chosen by people, not arranged, are among the strongest signals a page can have.",
  items: [
    { id: "pick-pages", text: "Choose the one to three pages you would be proud to be cited for.", detail: "Suggested: The espresso guide." },
    { id: "partners", text: "List the businesses and groups you already deal with." },
    { id: "log", text: "Keep a simple log of who you wrote to." },
  ],
  never: ["Buy links, or accept money for a link that is not marked rel=\"sponsored\"."],
};

export function manualTask(over: Partial<PlanAction> = {}): PlanAction {
  return planAction({
    id: "m1",
    type: "earn_links",
    type_label: "Earn links and mentions",
    risk_class: "manual",
    target_url: null,
    field: "links",
    current_value: null,
    proposed_value: CHECKLIST,
    original_value: CHECKLIST,
    evidence: { why: ["Pages rank better when other sites choose to cite them."], gain_basis: "prior" },
    expected_gain: 5,
    effort: 5,
    priority: 0.5,
    ...over,
  });
}

export interface ActionsState {
  actions: PlanAction[];
  ai: AiSettings;
  planRunning: boolean;
  /** Problems the next edit is rejected with (one-shot), to show validator errors. */
  rejectEdit: string[] | null;
  handoff: Handoff;
}

export function createActionsState(): ActionsState {
  return {
    actions: [],
    ai: { ...AI_OFF },
    planRunning: false,
    rejectEdit: null,
    handoff: { text: "Developer hand-off for Shop (shop.example.com)\n1. Rewrite the page title\n", count: 1, generated_on: "2026-10-13", includes_unapproved: false },
  };
}

export function summarise(state: ActionsState, autopilot: boolean): ActionSummary {
  const live = state.actions.filter((a) => a.status !== "obsolete");
  const board = live.filter((a) => OPEN.includes(a.status));
  const count = <K extends string>(key: (a: PlanAction) => K) => {
    const out: Record<string, number> = {};
    for (const a of board) out[key(a)] = (out[key(a)] ?? 0) + 1;
    return out;
  };
  const by_status: Record<string, number> = {};
  for (const a of live) by_status[a.status] = (by_status[a.status] ?? 0) + 1;
  const gain = (basis: string) =>
    board.filter((a) => (a.evidence.gain_basis === "gsc") === (basis === "gsc")).reduce((s, a) => s + a.expected_gain, 0);
  return {
    total: live.length,
    by_status,
    by_risk: count((a) => a.risk_class),
    by_type: count((a) => a.type),
    needs_ai: by_status.needs_ai ?? 0,
    gain_measured: gain("gsc"),
    gain_prior: gain("prior"),
    gain_approved: live.filter((a) => a.status === "approved").reduce((s, a) => s + a.expected_gain, 0),
    last_plan: null,
    plan_running: state.planRunning,
    ai: state.ai,
    autopilot_enabled: autopilot,
  };
}

/** Speaks the actions API. `autopilot` is read and written through the callbacks (the site record lives in fake-api). */
export function handleActionsRoute(
  state: ActionsState,
  url: URL,
  method: string,
  body: unknown,
  autopilot: { get: () => boolean },
): Response | null {
  if (url.pathname === "/ai/status") return json(state.ai);
  if (url.pathname === "/ai/budget" && method === "PUT") {
    const value = (body as { monthly_token_budget: number | null }).monthly_token_budget;
    state.ai = { ...state.ai, monthly_token_budget: value ?? 2_000_000, budget_is_custom: value !== null };
    return json(state.ai);
  }
  const m = url.pathname.match(/^\/sites\/([^/]+)\/actions(\/[^/]+)?(\/[^/]+)?$/);
  if (!m) return null;
  const rest = m[2] ?? "";
  const sub = m[3] ?? "";

  if (rest === "" && method === "GET") {
    const statuses = url.searchParams.getAll("status");
    const risks = url.searchParams.getAll("risk_class");
    const types = url.searchParams.getAll("type");
    const manual = url.searchParams.get("manual");
    const rows = state.actions
      .filter((a) => (statuses.length ? statuses.includes(a.status) : a.status !== "obsolete"))
      .filter((a) => (risks.length ? risks.includes(a.risk_class) : true))
      .filter((a) => (types.length ? types.includes(a.type) : true))
      .filter((a) => (manual === null ? true : (a.risk_class === "manual") === (manual === "true")))
      .sort((a, b) => b.priority - a.priority);
    return json(rows);
  }
  if (rest === "/summary") return json(summarise(state, autopilot.get()));
  if (rest === "/replan" && method === "POST") {
    state.planRunning = true;
    return json({ id: "j1", type: "plan", status: "queued", progress: 0, error: null, payload: {}, result: null }, 202);
  }
  if (rest === "/export.csv") return new Response("priority,status\n1.5,proposed\n", { status: 200, headers: { "Content-Type": "text/csv" } });
  if (rest === "/handoff") return json(state.handoff);

  if (rest === "/bulk" && method === "POST") {
    const { ids, op, reason } = body as { ids: string[]; op: string; reason?: string };
    const updated: PlanAction[] = [];
    const failed: { id: string; error: string }[] = [];
    for (const id of ids) {
      const a = state.actions.find((x) => x.id === id);
      if (!a) {
        failed.push({ id, error: "Action not found" });
        continue;
      }
      if (op === "approve" && a.risk_class === "manual") {
        failed.push({ id, error: "This is a manual task: tick it off when it is done instead of approving it." });
        continue;
      }
      if (op === "approve") a.status = "approved";
      if (op === "reject") {
        a.status = "rejected";
        a.reject_reason = reason ?? null;
      }
      if (op === "reopen") a.status = "proposed";
      if (op === "done") a.status = "manual_done";
      updated.push(a);
    }
    return json({ updated, failed });
  }

  const action = state.actions.find((a) => `/${a.id}` === rest);
  if (!action) return json({ detail: "Action not found" }, 404);
  if (rest !== "" && sub === "" && method === "GET") return json(action);
  if (sub === "/approve") {
    action.status = "approved";
    return json(action);
  }
  if (sub === "/reject") {
    action.status = "rejected";
    action.reject_reason = (body as { reason: string }).reason;
    return json(action);
  }
  if (sub === "/reopen") {
    action.status = "proposed";
    action.reject_reason = null;
    return json(action);
  }
  if (sub === "/done") {
    action.status = "manual_done";
    return json(action);
  }
  if (sub === "/restore") {
    action.proposed_value = action.original_value;
    action.edited_by_user = false;
    return json(action);
  }
  if (sub === "" && method === "PATCH") {
    const changes = body as { proposed_value?: unknown; checklist_done?: string[] };
    if ("proposed_value" in changes) {
      const problems = state.rejectEdit ?? (typeof changes.proposed_value === "string" && changes.proposed_value.length < 30
        ? [`The title is ${changes.proposed_value.length} characters; it must be at least 30.`]
        : null);
      state.rejectEdit = null;
      if (problems) return json({ detail: { message: "The new value does not pass the checks.", problems } }, 422);
      action.proposed_value = changes.proposed_value;
      action.edited_by_user = true;
      if (action.status !== "approved") action.status = "proposed";
      else action.status = "proposed";
    }
    if (changes.checklist_done) action.checklist_done = changes.checklist_done;
    return json(action);
  }
  return json({ detail: "Not found" }, 404);
}
