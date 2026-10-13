import type { ActionStatus, PlanAction, RiskClass } from "@/lib/api/types";

export const STATUS_LABELS: Record<ActionStatus, string> = {
  proposed: "Proposed",
  needs_ai: "Needs AI",
  no_suggestion: "No suggestion",
  approved: "Approved",
  deployed: "Deployed",
  rejected: "Rejected",
  rolled_back: "Rolled back",
  manual_done: "Done",
  stale: "Out of date",
  needs_manual: "Needs a developer",
  obsolete: "Obsolete",
};

export const RISK_LABELS: Record<RiskClass, { label: string; hint: string }> = {
  auto: { label: "Low risk", hint: "Text or markup. Autopilot may apply it by itself when you switch autopilot on." },
  approve: { label: "Needs approval", hint: "Always waits for a person before anything changes." },
  manual: { label: "Manual task", hint: "Work only a person can do. Never automated." },
};

/** Statuses that count as "still on the board". */
export const OPEN_BOARD: ActionStatus[] = ["proposed", "needs_ai", "no_suggestion", "approved"];

// ---- word diff ---------------------------------------------------------------------------------------
export interface DiffPart {
  kind: "same" | "del" | "add";
  text: string;
}

const MAX_DIFF_TOKENS = 1500;

/** Word-level diff (longest common subsequence). Whitespace is kept, so the parts join back into the text. */
export function diffWords(before: string, after: string): DiffPart[] {
  const a = before.match(/\s+|\S+/g) ?? [];
  const b = after.match(/\s+|\S+/g) ?? [];
  if (a.length > MAX_DIFF_TOKENS || b.length > MAX_DIFF_TOKENS) {
    return [...(before ? [{ kind: "del" as const, text: before }] : []), ...(after ? [{ kind: "add" as const, text: after }] : [])];
  }
  const rows = a.length + 1;
  const cols = b.length + 1;
  const table = new Uint16Array(rows * cols);
  for (let i = a.length - 1; i >= 0; i--) {
    for (let j = b.length - 1; j >= 0; j--) {
      table[i * cols + j] =
        a[i] === b[j]
          ? table[(i + 1) * cols + j + 1]! + 1
          : Math.max(table[(i + 1) * cols + j]!, table[i * cols + j + 1]!);
    }
  }
  const parts: DiffPart[] = [];
  const push = (kind: DiffPart["kind"], text: string) => {
    const last = parts[parts.length - 1];
    if (last && last.kind === kind) last.text += text;
    else parts.push({ kind, text });
  };
  let i = 0;
  let j = 0;
  while (i < a.length && j < b.length) {
    if (a[i] === b[j]) {
      push("same", a[i]!);
      i++;
      j++;
    } else if (table[(i + 1) * cols + j]! >= table[i * cols + j + 1]!) {
      push("del", a[i++]!);
    } else {
      push("add", b[j++]!);
    }
  }
  while (i < a.length) push("del", a[i++]!);
  while (j < b.length) push("add", b[j++]!);
  return parts;
}

// ---- values as text ------------------------------------------------------------------------------------
interface Link {
  to?: string;
  anchor?: string;
}

/** A value of any action type as readable text (what the diff shows and what the editor starts from). */
export function valueText(type: string, value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "string") return value;
  const v = value as Record<string, unknown>;
  if (type === "add_internal_links" && Array.isArray(v.links)) {
    return (v.links as Link[]).map((l) => `"${l.anchor ?? ""}" → ${l.to ?? ""}`).join("\n");
  }
  if (type === "collapse_redirect") return `${v.status ?? 301} redirect: ${v.from ?? ""} → ${v.to ?? ""}`;
  if (type === "fix_link") {
    return v.mode === "remove" ? `Remove the link to ${v.from ?? ""}` : `${v.from ?? ""} → ${v.to ?? ""}`;
  }
  if (v.kind === "brief" && Array.isArray(v.steps)) return (v.steps as string[]).map((s, i) => `${i + 1}. ${s}`).join("\n");
  return JSON.stringify(value, null, 2);
}

/** Types whose proposed value is a single string (the editor shows a plain text box). */
export const STRING_TYPES = ["rewrite_title", "rewrite_meta", "add_alt", "fix_canonical"];
/** Types whose editor takes JSON-LD text (the server parses it). */
export const JSONLD_TYPES = ["add_jsonld", "fix_jsonld"];
/** Types a person may edit (the rest are ticked off or only approved). */
export const EDITABLE_TYPES = [
  ...STRING_TYPES,
  ...JSONLD_TYPES,
  "add_internal_links",
  "collapse_redirect",
  "fix_link",
  "refresh_content",
  "consolidate_pages",
];

export function editorText(action: Pick<PlanAction, "type" | "proposed_value" | "current_value">): string {
  const value = action.proposed_value;
  if (value === null || value === undefined) return "";
  if (STRING_TYPES.includes(action.type)) return String(value);
  if (JSONLD_TYPES.includes(action.type)) return typeof value === "string" ? value : JSON.stringify(value, null, 2);
  return JSON.stringify(value, null, 2);
}

/** What to send to the server for the text in the editor, or an error sentence for the person. */
export function parseEditorText(type: string, text: string): { ok: true; value: unknown } | { ok: false; error: string } {
  if (STRING_TYPES.includes(type) || JSONLD_TYPES.includes(type)) return { ok: true, value: text };
  try {
    return { ok: true, value: JSON.parse(text) };
  } catch {
    return { ok: false, error: "That is not valid JSON. Check the brackets, quotes and commas." };
  }
}

// ---- numbers -----------------------------------------------------------------------------------------
export function gainText(gain: number): string {
  if (gain <= 0) return "no clicks estimated";
  if (gain < 1) return "<1 click / 28 days";
  return `+${gain >= 100 ? Math.round(gain) : gain.toFixed(gain < 10 ? 1 : 0)} clicks / 28 days`;
}

export function confidenceText(confidence: number): string {
  if (confidence >= 0.6) return "high";
  if (confidence >= 0.3) return "medium";
  return "low";
}

export const EFFORT_LABELS = ["", "minutes", "under an hour", "a few hours", "a day or two", "days"];

export function gainBasis(action: Pick<PlanAction, "evidence">): "gsc" | "prior" {
  return action.evidence.gain_basis === "gsc" ? "gsc" : "prior";
}

export function whyText(action: Pick<PlanAction, "evidence">): string {
  const why = action.evidence.why;
  return Array.isArray(why) && typeof why[0] === "string" ? why[0] : "";
}

export function hasValue(action: Pick<PlanAction, "proposed_value">): boolean {
  return action.proposed_value !== null && action.proposed_value !== undefined;
}

export function isChecklist(action: Pick<PlanAction, "proposed_value">): boolean {
  const v = action.proposed_value as { kind?: unknown } | null;
  return !!v && typeof v === "object" && v.kind === "checklist";
}

export function problemsOf(error: unknown): string[] {
  const detail = (error as { detail?: unknown } | null)?.detail as { problems?: unknown } | undefined;
  return Array.isArray(detail?.problems) ? (detail.problems as string[]) : [];
}
