import { CheckCircle2, ChevronDown, ChevronRight } from "lucide-react";
import { useState } from "react";

import { SeverityBadge } from "@/components/audit/severity-badge";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import type { AuditIssues, Category, IssueGroup, IssueUrl } from "@/lib/api/types";
import { CATEGORIES, CATEGORY_LABEL, filterGroups, sectionsBySeverity } from "@/lib/audit-utils";

function detailSummary(details: Record<string, unknown>): string {
  return Object.entries(details)
    .filter(([, v]) => v !== null && v !== undefined && typeof v !== "object")
    .slice(0, 3)
    .map(([k, v]) => `${k.replaceAll("_", " ")}: ${String(v)}`)
    .join(" · ");
}

function ItemRow({ item, onOpenPage }: { item: IssueUrl; onOpenPage: (pageId: string) => void }) {
  const summary = detailSummary(item.details);
  return (
    <li className="flex flex-col gap-0.5 py-1.5">
      {item.url && item.page_id ? (
        <button
          type="button"
          onClick={() => onOpenPage(item.page_id as string)}
          className="w-fit max-w-full truncate text-left text-sm text-primary underline-offset-4 hover:underline"
          title={item.url}
        >
          {item.url}
        </button>
      ) : (
        <span className="text-sm font-medium">Whole site</span>
      )}
      {summary && <span className="truncate text-xs text-muted-foreground">{summary}</span>}
    </li>
  );
}

function GroupRow({ group, onOpenPage }: { group: IssueGroup; onOpenPage: (pageId: string) => void }) {
  const [open, setOpen] = useState(false);
  const panelId = `issue-${group.check_id}`;
  return (
    <li>
      <button
        type="button"
        aria-expanded={open}
        aria-controls={panelId}
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-3 px-5 py-3 text-left transition-colors hover:bg-accent/50"
      >
        {open ? <ChevronDown className="size-4 shrink-0" /> : <ChevronRight className="size-4 shrink-0" />}
        <span className="min-w-0 flex-1 truncate font-medium">{group.title}</span>
        <span className="hidden text-xs text-muted-foreground sm:block">{CATEGORY_LABEL[group.category]}</span>
        <SeverityBadge severity={group.severity} />
        <span className="w-14 text-right text-sm font-semibold tabular-nums" aria-label={`${group.count} affected`}>
          {group.count}
        </span>
      </button>
      {open && (
        <div id={panelId} className="space-y-4 border-t bg-muted/30 px-5 py-4">
          <div className="grid gap-4 md:grid-cols-2">
            <div className="space-y-1">
              <h4 className="text-sm font-semibold">Why it matters</h4>
              <p className="text-sm text-muted-foreground">{group.why_it_matters}</p>
            </div>
            <div className="space-y-1">
              <h4 className="text-sm font-semibold">How to fix it</h4>
              <p className="text-sm text-muted-foreground">{group.how_to_fix}</p>
            </div>
          </div>
          <div>
            <h4 className="text-sm font-semibold">{group.count === 1 ? "Affected" : `Affected (${group.count})`}</h4>
            <ul className="mt-1 divide-y" aria-label={`Affected by ${group.title}`}>
              {group.items.map((item, i) => (
                <ItemRow key={`${item.page_id ?? "site"}-${i}`} item={item} onOpenPage={onOpenPage} />
              ))}
            </ul>
            {group.truncated && (
              <p className="mt-2 text-xs text-muted-foreground">
                Showing the first {group.items.length} of {group.count}. Export the CSV to see all of them.
              </p>
            )}
          </div>
        </div>
      )}
    </li>
  );
}

export function IssueList({ issues, onOpenPage }: { issues: AuditIssues; onOpenPage: (pageId: string) => void }) {
  const [category, setCategory] = useState<Category | "all">("all");
  const [text, setText] = useState("");
  const visible = filterGroups(issues.groups, { category, text });
  const sections = sectionsBySeverity(visible);

  if (issues.groups.length === 0) {
    return (
      <Card className="items-center py-12 text-center">
        <div className="flex max-w-sm flex-col items-center gap-2 px-5">
          <CheckCircle2 className="size-8 text-success" />
          <h3 className="text-lg font-semibold">No issues found</h3>
          <p className="text-sm text-muted-foreground">Every check passed on the pages we crawled.</p>
        </div>
      </Card>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <Input
          aria-label="Filter issues"
          placeholder="Filter by issue or URL"
          value={text}
          onChange={(e) => setText(e.target.value)}
          className="max-w-xs"
        />
        <div role="group" aria-label="Category" className="flex flex-wrap gap-1.5">
          {(["all", ...CATEGORIES] as const).map((c) => (
            <button
              key={c}
              type="button"
              aria-pressed={category === c}
              onClick={() => setCategory(c)}
              className="rounded-md border px-2.5 py-1 text-xs font-medium transition-colors hover:bg-accent aria-pressed:bg-accent aria-pressed:text-accent-foreground"
            >
              {c === "all" ? "All" : CATEGORY_LABEL[c]}
            </button>
          ))}
        </div>
      </div>

      {sections.length === 0 && <p className="text-sm text-muted-foreground">No issues match this filter.</p>}

      {sections.map((section) => (
        <section key={section.severity} aria-label={`${section.label} issues`} className="space-y-2">
          <h3 className="flex items-center gap-2 text-sm font-semibold">
            {section.label}
            <span className="font-normal text-muted-foreground">
              {section.groups.length} {section.groups.length === 1 ? "check" : "checks"} · {section.total} affected
            </span>
          </h3>
          <Card className="py-0">
            <ul className="divide-y">
              {section.groups.map((g) => (
                <GroupRow key={g.check_id} group={g} onOpenPage={onOpenPage} />
              ))}
            </ul>
          </Card>
        </section>
      ))}

      {issues.passed.length > 0 && (
        <details className="rounded-xl border bg-card px-5 py-3 text-sm">
          <summary className="cursor-pointer font-medium">Passed checks ({issues.passed.length})</summary>
          <ul className="mt-2 grid gap-1 sm:grid-cols-2">
            {issues.passed.map((p) => (
              <li key={p.check_id} className="flex items-center gap-2 text-muted-foreground">
                <CheckCircle2 className="size-3.5 shrink-0 text-success" />
                {p.title}
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}
