import { Card, CardContent } from "@/components/ui/card";
import { RISK_LABELS } from "@/lib/action-utils";
import type { ActionSummary, RiskClass } from "@/lib/api/types";

function Stat({ label, value, note }: { label: string; value: React.ReactNode; note?: React.ReactNode }) {
  return (
    <Card className="gap-1 py-4">
      <CardContent className="space-y-0.5">
        <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">{label}</p>
        <p className="text-2xl font-semibold tabular-nums">{value}</p>
        {note && <p className="text-xs text-muted-foreground">{note}</p>}
      </CardContent>
    </Card>
  );
}

function fmt(n: number): string {
  return n >= 100 ? Math.round(n).toLocaleString("en-US") : n.toFixed(1);
}

export function SummaryCards({ summary }: { summary: ActionSummary }) {
  const board = Object.values(summary.by_risk).reduce((a, b) => a + (b ?? 0), 0);
  const status = summary.by_status;
  const risks: RiskClass[] = ["auto", "approve", "manual"];
  return (
    <section aria-label="Plan summary" className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
      <Stat
        label="On the board"
        value={board}
        note={`${status.proposed ?? 0} proposed · ${status.approved ?? 0} approved · ${(status.needs_ai ?? 0) + (status.no_suggestion ?? 0)} waiting for a suggestion`}
      />
      <Stat
        label="By risk"
        value={
          <span className="flex gap-3 text-base">
            {risks.map((r) => (
              <span key={r} title={RISK_LABELS[r].hint}>
                <span className="text-2xl font-semibold">{summary.by_risk[r] ?? 0}</span>{" "}
                <span className="text-xs font-normal text-muted-foreground">{RISK_LABELS[r].label.toLowerCase()}</span>
              </span>
            ))}
          </span>
        }
      />
      <Stat
        label="Expected extra clicks / 28 days"
        value={fmt(summary.gain_measured + summary.gain_prior)}
        note={
          <>
            {fmt(summary.gain_measured)} from Search Console data, {fmt(summary.gain_prior)} prior, not measured
          </>
        }
      />
      <Stat
        label="Approved so far"
        value={fmt(summary.gain_approved)}
        note={`${status.approved ?? 0} approved · ${status.rejected ?? 0} rejected · ${status.manual_done ?? 0} done`}
      />
    </section>
  );
}
