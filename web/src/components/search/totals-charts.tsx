import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { Card, CardContent } from "@/components/ui/card";
import type { SearchTotals, TotalsDay, TotalsSummary } from "@/lib/api/types";
import { changePct, fmtInt, fmtPct, fmtPosition, formatDay } from "@/lib/search-utils";

interface Metric {
  key: "clicks" | "impressions" | "ctr" | "position";
  label: string;
  color: string;
  value: (s: TotalsSummary) => string;
  point: (d: TotalsDay) => number | null;
  format: (n: number) => string;
  /** For position a lower number is better, so the chart is drawn upside down. */
  reversed?: boolean;
  change: (s: TotalsSummary, p: TotalsSummary) => string | null;
}

const METRICS: Metric[] = [
  {
    key: "clicks",
    label: "Clicks",
    color: "var(--chart-1, #2563eb)",
    value: (s) => fmtInt(s.clicks),
    point: (d) => d.clicks,
    format: fmtInt,
    change: (s, p) => changePct(s.clicks, p.clicks),
  },
  {
    key: "impressions",
    label: "Impressions",
    color: "var(--chart-2, #7c3aed)",
    value: (s) => fmtInt(s.impressions),
    point: (d) => d.impressions,
    format: fmtInt,
    change: (s, p) => changePct(s.impressions, p.impressions),
  },
  {
    key: "ctr",
    label: "Click-through rate",
    color: "var(--chart-3, #059669)",
    value: (s) => fmtPct(s.ctr, 2),
    point: (d) => d.ctr,
    format: (n) => fmtPct(n, 2),
    change: (s, p) => changePct(s.ctr, p.ctr),
  },
  {
    key: "position",
    label: "Average position",
    color: "var(--chart-4, #d97706)",
    value: (s) => fmtPosition(s.position),
    point: (d) => d.position,
    format: (n) => n.toFixed(1),
    reversed: true,
    change: (s, p) =>
      s.position === null || p.position === null ? null : changePct(s.position, p.position),
  },
];

function MetricCard({ metric, totals }: { metric: Metric; totals: SearchTotals }) {
  const summary = totals.summary;
  const change = summary && totals.previous ? metric.change(summary, totals.previous) : null;
  const data = totals.series.map((d) => ({ date: d.date, value: metric.point(d) }));
  return (
    <Card className="gap-2 py-4">
      <CardContent className="space-y-2">
        <div>
          <p className="text-xs text-muted-foreground">{metric.label}</p>
          <p className="flex items-baseline gap-2">
            <span className="text-2xl font-semibold tabular-nums">{summary ? metric.value(summary) : "–"}</span>
            {change && (
              <span className="text-xs text-muted-foreground" aria-label={`${change} compared with the previous period`}>
                {change} vs previous
              </span>
            )}
          </p>
        </div>
        <div role="img" aria-label={`${metric.label} per day, ${totals.start} to ${totals.end}`} className="h-32 w-full">
          <ResponsiveContainer width="100%" height="100%" minWidth={0}>
            <LineChart data={data} margin={{ top: 4, right: 4, bottom: 0, left: 0 }}>
              <CartesianGrid vertical={false} strokeDasharray="3 3" stroke="var(--border, #e5e7eb)" />
              <XAxis dataKey="date" tickFormatter={formatDay} tick={{ fontSize: 10 }} minTickGap={32} />
              <YAxis
                width={44}
                tick={{ fontSize: 10 }}
                tickFormatter={(v: number) => metric.format(v)}
                reversed={metric.reversed}
                domain={["auto", "auto"]}
              />
              <Tooltip
                formatter={(v) => [typeof v === "number" ? metric.format(v) : String(v), metric.label]}
                labelFormatter={(label) => formatDay(String(label))}
              />
              <Line type="monotone" dataKey="value" stroke={metric.color} dot={false} strokeWidth={2} isAnimationActive={false} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </CardContent>
    </Card>
  );
}

export function TotalsCharts({ totals }: { totals: SearchTotals }) {
  return (
    <div className="space-y-2">
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4" aria-label="Search performance charts">
        {METRICS.map((m) => (
          <MetricCard key={m.key} metric={m} totals={totals} />
        ))}
      </div>
      <p className="text-xs text-muted-foreground">
        {totals.summary?.days ?? 0} days, {totals.start} to {totals.end}. {totals.note}
      </p>
    </div>
  );
}
