import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { EFFORT_LABELS, gainBasis, gainText, whyText } from "@/lib/action-utils";
import type { PlanAction } from "@/lib/api/types";
import { shortUrl } from "@/lib/search-utils";

interface ChecklistItem {
  id: string;
  text: string;
  detail?: string;
}

interface ChecklistValue {
  kind: "checklist";
  title: string;
  intro: string;
  items: ChecklistItem[];
  never: string[];
}

interface BriefValue {
  kind: "brief";
  title?: string;
  steps: string[];
}

export function ManualTaskCard({
  action,
  busy,
  onTick,
  onDone,
  onReopen,
  onDismiss,
}: {
  action: PlanAction;
  busy: boolean;
  onTick: (action: PlanAction, done: string[]) => void;
  onDone: (action: PlanAction) => void;
  onReopen: (action: PlanAction) => void;
  onDismiss: (action: PlanAction) => void;
}) {
  const [open, setOpen] = useState(false);
  const value = action.proposed_value as ChecklistValue | BriefValue | null;
  const finished = action.status === "manual_done";
  const closed = finished || action.status === "rejected";
  const items = value?.kind === "checklist" ? value.items : [];
  const done = new Set(action.checklist_done);
  const urls = Array.isArray(action.evidence.urls) ? (action.evidence.urls as string[]) : [];
  const title = value?.title ?? action.type_label;

  return (
    <li className="space-y-3 px-5 py-4" data-testid="manual-task" aria-label={title}>
      <div className="flex flex-wrap items-start gap-2">
        <div className="min-w-0 flex-1 space-y-1">
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={() => setOpen((v) => !v)}
              aria-expanded={open || !closed}
              className="text-left font-medium hover:underline"
            >
              {title}
            </button>
            {finished && <Badge variant="success">Done</Badge>}
            {items.length > 0 && (
              <Badge variant="outline">
                {done.size} of {items.length} done
              </Badge>
            )}
            <Badge variant="outline">Manual task</Badge>
          </div>
          {whyText(action) && <p className="text-sm text-muted-foreground">{whyText(action)}</p>}
        </div>
        <p className="text-right text-xs text-muted-foreground">
          {gainText(action.expected_gain)}
          <br />
          {gainBasis(action) === "gsc" ? "from your data" : "prior, not measured"} · effort {EFFORT_LABELS[action.effort]}
        </p>
      </div>

      {(!closed || open) && value?.kind === "checklist" && (
        <div className="space-y-3">
          <p className="text-sm">{value.intro}</p>
          <ul className="space-y-2" aria-label={`Steps: ${title}`}>
            {value.items.map((item) => {
              const id = `${action.id}-${item.id}`;
              return (
                <li key={item.id} className="flex items-start gap-2">
                  <input
                    id={id}
                    type="checkbox"
                    checked={done.has(item.id)}
                    disabled={busy || action.status === "rejected"}
                    onChange={(e) => {
                      const next = new Set(done);
                      if (e.target.checked) next.add(item.id);
                      else next.delete(item.id);
                      onTick(action, value.items.map((i) => i.id).filter((i) => next.has(i)));
                    }}
                    className="mt-1 size-4 accent-primary"
                  />
                  <label htmlFor={id} className="min-w-0 text-sm">
                    <span className={done.has(item.id) ? "text-muted-foreground line-through" : ""}>{item.text}</span>
                    {item.detail && <span className="mt-0.5 block text-xs text-muted-foreground">{item.detail}</span>}
                  </label>
                </li>
              );
            })}
          </ul>
          {value.never.length > 0 && (
            <div className="rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2">
              <p className="text-xs font-semibold text-destructive">Do not</p>
              <ul className="list-disc pl-5 text-xs text-muted-foreground">
                {value.never.map((n) => (
                  <li key={n}>{n}</li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}

      {(!closed || open) && value?.kind === "brief" && (
        <div className="space-y-2">
          <ol className="list-decimal space-y-1 pl-5 text-sm">
            {value.steps.map((s) => (
              <li key={s}>{s}</li>
            ))}
          </ol>
          {urls.length > 0 && (
            <details className="text-xs text-muted-foreground">
              <summary className="cursor-pointer">Affected pages ({urls.length})</summary>
              <ul className="mt-1 space-y-0.5">
                {urls.slice(0, 50).map((u) => (
                  <li key={u} className="truncate" title={u}>
                    {shortUrl(u)}
                  </li>
                ))}
              </ul>
            </details>
          )}
        </div>
      )}

      <div className="flex gap-2">
        {!closed && (
          <Button size="sm" onClick={() => onDone(action)} disabled={busy}>
            Mark task done
          </Button>
        )}
        {!closed && (
          <Button size="sm" variant="ghost" onClick={() => onDismiss(action)} disabled={busy} title="Hides it for 90 days">
            Not for us
          </Button>
        )}
        {closed && (
          <Button size="sm" variant="outline" onClick={() => onReopen(action)} disabled={busy}>
            Reopen
          </Button>
        )}
      </div>
    </li>
  );
}
