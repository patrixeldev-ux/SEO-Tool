import { SeverityBadge } from "@/components/audit/severity-badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import type { AuditChanges, ChangeItem } from "@/lib/api/types";
import { formatDelta } from "@/lib/audit-utils";

const SHOWN = 15;

function ChangeList({ label, items }: { label: string; items: ChangeItem[] }) {
  if (items.length === 0) return null;
  return (
    <details className="rounded-lg border px-4 py-2 text-sm" open={items.length <= 5}>
      <summary className="cursor-pointer font-medium">
        {label} ({items.length})
      </summary>
      <ul className="mt-2 divide-y" aria-label={label}>
        {items.slice(0, SHOWN).map((c) => (
          <li key={`${c.check_id}-${c.url ?? ""}`} className="flex flex-wrap items-center gap-2 py-1.5">
            <SeverityBadge severity={c.severity} />
            <span className="font-medium">{c.title}</span>
            <span className="min-w-0 truncate text-muted-foreground">{c.url ?? "whole site"}</span>
          </li>
        ))}
      </ul>
      {items.length > SHOWN && <p className="mt-1 text-xs text-muted-foreground">and {items.length - SHOWN} more</p>}
    </details>
  );
}

export function ChangesCard({ changes }: { changes: AuditChanges }) {
  if (!changes.previous_audit_id) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Changes since the last audit</CardTitle>
          <CardDescription>
            This is the first finished audit for this site. After the next one, you will see here what was fixed and
            what is new.
          </CardDescription>
        </CardHeader>
      </Card>
    );
  }
  const delta = formatDelta(changes.score_delta);
  return (
    <Card>
      <CardHeader>
        <CardTitle>Changes since the last audit</CardTitle>
        <CardDescription>
          {delta ? `Score ${delta}. ` : ""}
          {changes.fixed.length} fixed, {changes.new.length} new, {changes.persisting_count} still open
          {changes.not_rechecked_count > 0 && `, ${changes.not_rechecked_count} on pages this audit did not reach`}.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <ChangeList label="Fixed" items={changes.fixed} />
        <ChangeList label="New" items={changes.new} />
        {changes.fixed.length === 0 && changes.new.length === 0 && (
          <p className="text-sm text-muted-foreground">Nothing changed between the two audits.</p>
        )}
      </CardContent>
    </Card>
  );
}
