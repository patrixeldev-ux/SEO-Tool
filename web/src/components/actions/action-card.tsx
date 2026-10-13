import { ChevronDown, ChevronRight } from "lucide-react";
import { useState } from "react";

import { DiffView } from "@/components/actions/diff-view";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  confidenceText,
  EDITABLE_TYPES,
  editorText,
  EFFORT_LABELS,
  gainBasis,
  gainText,
  hasValue,
  parseEditorText,
  problemsOf,
  RISK_LABELS,
  STATUS_LABELS,
  whyText,
} from "@/lib/action-utils";
import type { ActionStatus, PlanAction } from "@/lib/api/types";
import { shortUrl } from "@/lib/search-utils";

const STATUS_VARIANT: Record<ActionStatus, "secondary" | "success" | "warning" | "destructive" | "outline"> = {
  proposed: "secondary",
  needs_ai: "warning",
  no_suggestion: "warning",
  approved: "success",
  deployed: "success",
  rejected: "destructive",
  rolled_back: "destructive",
  manual_done: "success",
  stale: "warning",
  needs_manual: "warning",
  obsolete: "outline",
};

export interface ActionHandlers {
  onApprove: (action: PlanAction) => void;
  onReopen: (action: PlanAction) => void;
  onRestore: (action: PlanAction) => void;
  onReject: (action: PlanAction, reason: string) => void;
  /** Resolves when saved; rejects with the API error (whose `detail.problems` lists the validator messages). */
  onSave: (action: PlanAction, value: unknown) => Promise<void>;
}

function ManualDeployNote({ action }: { action: PlanAction }) {
  const note = action.generation.deploy_manual as { reason?: string; handoff?: string } | undefined;
  return (
    <div role="note" className="space-y-2 rounded-md border border-warning/40 bg-warning/10 px-3 py-2 text-sm">
      <p className="font-medium">A developer needs to make this change.</p>
      <p className="text-muted-foreground">
        {note?.reason ?? "Search OS could not find where this page lives in your code."} Nothing was changed on your site. Send the
        text below to whoever edits the website.
      </p>
      {note?.handoff && (
        <pre className="max-h-48 overflow-auto rounded bg-card p-2 text-xs whitespace-pre-wrap" aria-label="Instructions for your developer">
          {note.handoff}
        </pre>
      )}
    </div>
  );
}

function Evidence({ action }: { action: PlanAction }) {
  const ev = action.evidence;
  const rows: [string, string][] = [];
  const search = ev.search as { impressions_28d?: number; clicks_28d?: number } | undefined;
  if (search?.impressions_28d !== undefined) {
    rows.push(["Search Console, last 28 days", `${search.impressions_28d} impressions, ${search.clicks_28d ?? 0} clicks`]);
  }
  if (typeof ev.target_query === "string" && ev.target_query) rows.push(["Main search", ev.target_query]);
  if (Array.isArray(ev.issues) && ev.issues.length) {
    rows.push(["Found by the audit", (ev.issues as { check?: string }[]).map((i) => i.check ?? "").filter(Boolean).join(", ")]);
  }
  if (typeof ev.gain_note === "string") rows.push([gainBasis(action) === "gsc" ? "How the gain is estimated" : "About this number", ev.gain_note]);
  const reason = action.generation.reason;
  if (typeof reason === "string" && reason) rows.push(["About the suggestion", reason]);
  return (
    <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs text-muted-foreground">
      {rows.map(([k, v]) => (
        <div key={k} className="contents">
          <dt className="font-medium">{k}</dt>
          <dd className="break-words">{v}</dd>
        </div>
      ))}
    </dl>
  );
}

export function ActionCard({
  action,
  selected,
  onSelect,
  busy,
  handlers,
}: {
  action: PlanAction;
  selected: boolean;
  onSelect: (checked: boolean) => void;
  busy: boolean;
  handlers: ActionHandlers;
}) {
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState("");
  const [problems, setProblems] = useState<string[]>([]);
  const [saving, setSaving] = useState(false);
  const [rejecting, setRejecting] = useState(false);
  const [reason, setReason] = useState("");
  const [showWhy, setShowWhy] = useState(false);

  const open = ["proposed", "needs_ai", "no_suggestion", "approved"].includes(action.status);
  const editable = open && EDITABLE_TYPES.includes(action.type);
  const risk = RISK_LABELS[action.risk_class];
  const label = `${action.type_label}${action.target_url ? ` on ${shortUrl(action.target_url)}` : ""}`;
  const why = whyText(action);

  function startEdit() {
    setText(editorText(action));
    setProblems([]);
    setEditing(true);
  }

  async function save() {
    const parsed = parseEditorText(action.type, text);
    if (!parsed.ok) {
      setProblems([parsed.error]);
      return;
    }
    setSaving(true);
    try {
      await handlers.onSave(action, parsed.value);
      setEditing(false);
      setProblems([]);
    } catch (error) {
      const found = problemsOf(error);
      setProblems(found.length ? found : [error instanceof Error ? error.message : "Could not save"]);
    } finally {
      setSaving(false);
    }
  }

  return (
    <li className="space-y-3 px-5 py-4" data-testid="action-card" data-status={action.status} aria-label={label}>
      <div className="flex flex-wrap items-start gap-x-3 gap-y-2">
        {open && (
          <input
            type="checkbox"
            checked={selected}
            onChange={(e) => onSelect(e.target.checked)}
            aria-label={`Select ${label}`}
            className="mt-1 size-4 accent-primary"
          />
        )}
        <div className="min-w-0 flex-1 space-y-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-medium">{action.type_label}</span>
            <Badge variant={STATUS_VARIANT[action.status]}>{STATUS_LABELS[action.status]}</Badge>
            <Badge variant="outline" title={risk.hint}>
              {risk.label}
            </Badge>
            {action.edited_by_user && <Badge variant="outline">Edited by you</Badge>}
          </div>
          <p className="truncate text-sm text-muted-foreground" title={action.target_url ?? undefined}>
            {action.target_url ? shortUrl(action.target_url) : "Whole site"} · {action.field.replace("_", " ")}
          </p>
        </div>
        <div className="text-right text-sm">
          <p className="font-medium tabular-nums" title={`Priority ${action.priority}`}>
            {gainText(action.expected_gain)}
          </p>
          <p className="text-xs text-muted-foreground">
            {gainBasis(action) === "gsc" ? "from your Search Console data" : "prior, not measured"} ·{" "}
            {confidenceText(action.confidence)} confidence · effort {EFFORT_LABELS[action.effort]}
          </p>
        </div>
      </div>

      {!editing && <DiffView type={action.type} before={action.current_value} after={action.proposed_value} />}

      {action.status === "needs_ai" && (
        <p className="text-sm text-warning">Waiting for an AI suggestion. You can also write it yourself.</p>
      )}
      {action.status === "no_suggestion" && (
        <p className="text-sm text-warning">
          The AI could not produce a suggestion that passes the checks. {String(action.generation.reason ?? "")}
        </p>
      )}
      {action.status === "needs_manual" && <ManualDeployNote action={action} />}
      {action.status === "rejected" && action.reject_reason && (
        <p className="text-sm text-muted-foreground">Rejected: {action.reject_reason}</p>
      )}

      {editing && (
        <div className="space-y-2">
          <label className="block text-sm font-medium" htmlFor={`edit-${action.id}`}>
            Your version
          </label>
          <textarea
            id={`edit-${action.id}`}
            value={text}
            onChange={(e) => setText(e.target.value)}
            rows={action.type === "rewrite_title" || action.type === "add_alt" ? 2 : 8}
            className="w-full rounded-md border border-input bg-card px-3 py-2 font-mono text-sm shadow-xs outline-none focus-visible:border-primary focus-visible:ring-[3px] focus-visible:ring-ring"
          />
          {problems.length > 0 && (
            <ul role="alert" aria-label="The new value does not pass these checks" className="list-disc space-y-0.5 pl-5 text-sm text-destructive">
              {problems.map((p) => (
                <li key={p}>{p}</li>
              ))}
            </ul>
          )}
          <div className="flex gap-2">
            <Button size="sm" onClick={save} disabled={saving}>
              {saving ? "Checking…" : "Save"}
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setEditing(false)} disabled={saving}>
              Cancel
            </Button>
          </div>
        </div>
      )}

      {rejecting && (
        <form
          className="flex flex-wrap items-center gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            if (!reason.trim()) return;
            handlers.onReject(action, reason.trim());
            setRejecting(false);
            setReason("");
          }}
        >
          <label htmlFor={`reason-${action.id}`} className="sr-only">
            Why are you rejecting this?
          </label>
          <Input
            id={`reason-${action.id}`}
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="Why? It is kept so this is not suggested again for 90 days."
            className="max-w-md"
            autoFocus
          />
          <Button size="sm" variant="destructive" type="submit" disabled={!reason.trim() || busy}>
            Reject
          </Button>
          <Button size="sm" variant="ghost" type="button" onClick={() => setRejecting(false)}>
            Cancel
          </Button>
        </form>
      )}

      {!editing && !rejecting && (
        <div className="flex flex-wrap items-center gap-2">
          {action.status === "proposed" && action.risk_class !== "manual" && hasValue(action) && (
            <Button size="sm" onClick={() => handlers.onApprove(action)} disabled={busy}>
              Approve
            </Button>
          )}
          {action.status === "needs_manual" && (
            <Button size="sm" variant="outline" onClick={() => handlers.onApprove(action)} disabled={busy}>
              Try deploying again
            </Button>
          )}
          {editable && (
            <Button size="sm" variant="outline" onClick={startEdit} disabled={busy}>
              {hasValue(action) ? "Edit" : "Write it yourself"}
            </Button>
          )}
          {action.edited_by_user && open && (
            <Button size="sm" variant="ghost" onClick={() => handlers.onRestore(action)} disabled={busy}>
              Restore AI version
            </Button>
          )}
          {open && (
            <Button size="sm" variant="ghost" onClick={() => setRejecting(true)} disabled={busy}>
              Reject
            </Button>
          )}
          {["approved", "rejected"].includes(action.status) && (
            <Button size="sm" variant="ghost" onClick={() => handlers.onReopen(action)} disabled={busy}>
              {action.status === "approved" ? "Undo approval" : "Reopen"}
            </Button>
          )}
          <button
            type="button"
            onClick={() => setShowWhy((v) => !v)}
            aria-expanded={showWhy}
            className="ml-auto inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
          >
            {showWhy ? <ChevronDown className="size-3" /> : <ChevronRight className="size-3" />}
            Why this?
          </button>
        </div>
      )}
      {why && <p className="text-sm text-muted-foreground">{why}</p>}
      {showWhy && <Evidence action={action} />}
    </li>
  );
}
