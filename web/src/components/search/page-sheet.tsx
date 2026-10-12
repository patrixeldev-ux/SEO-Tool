import { useQuery } from "@tanstack/react-query";
import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceDot,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { OpportunityRow } from "@/components/search/opportunities";
import { Badge } from "@/components/ui/badge";
import { Sheet, SheetContent, SheetDescription, SheetTitle } from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import type { SearchPageDetail, SearchRange, SearchSegment } from "@/lib/api/types";
import { useAuth } from "@/lib/auth";
import { fmtInt, fmtPct, fmtPosition } from "@/lib/search-utils";

function verdict(vsCurve: number): { text: string; variant: "success" | "warning" | "destructive" | "secondary" } {
  if (vsCurve <= -0.02) return { text: "below the curve", variant: "destructive" };
  if (vsCurve >= 0.02) return { text: "above the curve", variant: "success" };
  return { text: "about as expected", variant: "secondary" };
}

function CurveChart({ detail }: { detail: SearchPageDetail }) {
  const data = detail.curve.positions.map((position, i) => ({ position, ctr: detail.curve.values[i] ?? 0 }));
  const pos = detail.summary?.position;
  return (
    <div role="img" aria-label="Click-through rate by position: the site's curve and this page" className="h-44 w-full">
      <ResponsiveContainer width="100%" height="100%" minWidth={0}>
        <LineChart data={data} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
          <CartesianGrid vertical={false} strokeDasharray="3 3" stroke="var(--border, #e5e7eb)" />
          <XAxis dataKey="position" type="number" domain={[1, 20]} tick={{ fontSize: 10 }} />
          <YAxis width={40} tick={{ fontSize: 10 }} tickFormatter={(v: number) => fmtPct(v, 0)} />
          <Tooltip formatter={(v) => [typeof v === "number" ? fmtPct(v, 1) : String(v), "Expected CTR"]} labelFormatter={(p) => `Position ${String(p)}`} />
          <Line type="monotone" dataKey="ctr" stroke="var(--chart-1, #2563eb)" dot={false} strokeWidth={2} isAnimationActive={false} />
          {detail.summary && pos !== null && pos !== undefined && (
            <ReferenceDot x={Math.min(20, Math.max(1, pos))} y={detail.summary.ctr} r={5} fill="var(--chart-4, #d97706)" stroke="none" />
          )}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

function Body({ detail, onOpenPage }: { detail: SearchPageDetail; onOpenPage: (url: string) => void }) {
  const s = detail.summary;
  if (!s) {
    return <p className="text-sm text-muted-foreground">No Search Console data for this page in this period.</p>;
  }
  const gap = detail.expected_ctr === null ? null : s.ctr - detail.expected_ctr;
  return (
    <div className="space-y-5">
      <section aria-label="Page summary" className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        {[
          ["Clicks", fmtInt(s.clicks)],
          ["Impressions", fmtInt(s.impressions)],
          ["CTR", fmtPct(s.ctr, 2)],
          ["Average position", fmtPosition(s.position)],
        ].map(([label, value]) => (
          <div key={label} className="rounded-lg border p-3">
            <p className="text-xs text-muted-foreground">{label}</p>
            <p className="text-lg font-semibold tabular-nums">{value}</p>
          </div>
        ))}
      </section>

      <section aria-label="Click-through rate against the curve" className="space-y-2">
        <h3 className="text-sm font-semibold">Click-through rate against your curve</h3>
        {detail.expected_ctr !== null && gap !== null && (
          <p className="flex flex-wrap items-center gap-2 text-sm">
            At position {fmtPosition(s.position)} your pages usually get {fmtPct(detail.expected_ctr, 2)}; this page gets{" "}
            {fmtPct(s.ctr, 2)}.<Badge variant={verdict(gap).variant}>{verdict(gap).text}</Badge>
          </p>
        )}
        <CurveChart detail={detail} />
        <p className="text-xs text-muted-foreground">
          The line is built from your own data (confidence here: {detail.curve_confidence ?? "n/a"}). The dot is this page.
        </p>
      </section>

      {detail.opportunities.length > 0 && (
        <section aria-label="Opportunities for this page" className="space-y-1">
          <h3 className="text-sm font-semibold">Opportunities for this page</h3>
          <ul className="divide-y rounded-lg border">
            {detail.opportunities.map((o) => (
              <OpportunityRow key={o.id} item={o} onOpenPage={onOpenPage} />
            ))}
          </ul>
        </section>
      )}

      <section aria-label="Queries for this page" className="space-y-2">
        <h3 className="text-sm font-semibold">Queries</h3>
        <div className="overflow-x-auto rounded-lg border">
          <table className="w-full text-sm" aria-label="Queries for this page">
            <thead className="border-b text-left text-xs text-muted-foreground">
              <tr>
                <th className="px-3 py-2 font-medium">Query</th>
                <th className="px-2 py-2 text-right font-medium">Position</th>
                <th className="px-2 py-2 text-right font-medium">Impr.</th>
                <th className="px-2 py-2 text-right font-medium">CTR</th>
                <th className="px-3 py-2 text-right font-medium">Expected</th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {detail.queries.map((q) => (
                <tr key={q.query}>
                  <td className="max-w-[16rem] truncate px-3 py-2" title={q.query}>
                    {q.query}
                    {q.is_conversational && <Badge variant="outline" className="ml-2">conversational</Badge>}
                  </td>
                  <td className="px-2 py-2 text-right tabular-nums">{fmtPosition(q.position)}</td>
                  <td className="px-2 py-2 text-right tabular-nums">{fmtInt(q.impressions)}</td>
                  <td className="px-2 py-2 text-right tabular-nums">{fmtPct(q.ctr, 2)}</td>
                  <td
                    className={`px-3 py-2 text-right tabular-nums ${q.vs_curve <= -0.02 ? "text-destructive" : ""}`}
                    title={`${q.vs_curve >= 0 ? "+" : ""}${(q.vs_curve * 100).toFixed(1)} points versus the curve`}
                  >
                    {fmtPct(q.expected_ctr, 2)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}

export function SearchPageSheet({
  siteId,
  url,
  range,
  segment = "real",
  onOpenPage,
  onClose,
}: {
  siteId: string;
  url: string | null;
  range: SearchRange;
  segment?: SearchSegment;
  onOpenPage: (url: string) => void;
  onClose: () => void;
}) {
  const { client, activeOrg } = useAuth();
  const detail = useQuery({
    queryKey: ["search-page", activeOrg?.id, siteId, url, range, segment],
    queryFn: () => client.searchPage(siteId, url as string, range, segment),
    enabled: !!url,
  });
  return (
    <Sheet open={!!url} onOpenChange={(open) => !open && onClose()}>
      <SheetContent aria-describedby="search-page-description">
        <div className="space-y-1 pr-8">
          <SheetTitle className="text-lg leading-tight font-semibold break-words">{url ?? "Page"}</SheetTitle>
          <SheetDescription id="search-page-description" className="text-sm text-muted-foreground">
            What people searched to reach this page, where it ranks, and how often they clicked.
          </SheetDescription>
        </div>
        {detail.isPending && url && <Skeleton className="h-48 w-full" aria-label="Loading page" />}
        {detail.isError && (
          <p role="alert" className="text-sm text-destructive">
            Could not load this page: {detail.error.message}
          </p>
        )}
        {detail.data && <Body detail={detail.data} onOpenPage={onOpenPage} />}
      </SheetContent>
    </Sheet>
  );
}
