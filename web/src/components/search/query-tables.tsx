import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import type { SearchRange, SearchSegment, SearchTable } from "@/lib/api/types";
import { useAuth } from "@/lib/auth";
import { fmtInt, fmtPct, fmtPosition, shortUrl } from "@/lib/search-utils";

const SEGMENTS: { value: SearchSegment; label: string }[] = [
  { value: "real", label: "Real searches" },
  { value: "all", label: "All" },
  { value: "conversational", label: "Conversational only" },
];
const PAGE_SIZE = 25;

function Table({
  kind,
  data,
  onOpenPage,
}: {
  kind: "queries" | "pages";
  data: SearchTable;
  onOpenPage: (url: string) => void;
}) {
  if (data.rows.length === 0) {
    return <p className="px-5 py-6 text-sm text-muted-foreground">Nothing to show for this selection.</p>;
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm" aria-label={kind === "queries" ? "Top queries" : "Top pages"}>
        <thead className="border-b text-left text-xs text-muted-foreground">
          <tr>
            <th className="px-5 py-2 font-medium">{kind === "queries" ? "Query" : "Page"}</th>
            <th className="px-3 py-2 text-right font-medium">Clicks</th>
            <th className="px-3 py-2 text-right font-medium">Impressions</th>
            <th className="px-3 py-2 text-right font-medium">CTR</th>
            <th className="px-5 py-2 text-right font-medium">Position</th>
          </tr>
        </thead>
        <tbody className="divide-y">
          {data.rows.map((row) => (
            <tr key={row.key}>
              <td className="max-w-[28rem] px-5 py-2">
                <div className="flex items-center gap-2">
                  {kind === "pages" ? (
                    <button
                      type="button"
                      onClick={() => onOpenPage(row.key)}
                      className="min-w-0 truncate text-left text-primary underline-offset-4 hover:underline"
                      title={row.key}
                    >
                      {shortUrl(row.key)}
                    </button>
                  ) : (
                    <span className="min-w-0 truncate" title={row.key}>
                      {row.key}
                    </span>
                  )}
                  {row.is_conversational && <Badge variant="outline">conversational</Badge>}
                </div>
              </td>
              <td className="px-3 py-2 text-right tabular-nums">{fmtInt(row.clicks)}</td>
              <td className="px-3 py-2 text-right tabular-nums">{fmtInt(row.impressions)}</td>
              <td className="px-3 py-2 text-right tabular-nums">{fmtPct(row.ctr, 2)}</td>
              <td className="px-5 py-2 text-right tabular-nums">{fmtPosition(row.position)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function SearchTables({
  siteId,
  range,
  onOpenPage,
}: {
  siteId: string;
  range: SearchRange;
  onOpenPage: (url: string) => void;
}) {
  const { client, activeOrg } = useAuth();
  const [kind, setKind] = useState<"queries" | "pages">("queries");
  const [segment, setSegment] = useState<SearchSegment>("real");
  const [offset, setOffset] = useState(0);
  const table = useQuery({
    queryKey: ["search-table", activeOrg?.id, siteId, kind, range, segment, offset],
    queryFn: () => client.searchTable(siteId, kind, { range, segment, limit: PAGE_SIZE, offset }),
    placeholderData: (previous) => previous,
  });
  const data = table.data;
  const share = data && data.all_impressions > 0 ? data.conversational_impressions / data.all_impressions : 0;

  return (
    <section aria-label="Queries and pages" className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div role="tablist" aria-label="Table" className="flex gap-1 border-b">
          {(
            [
              ["queries", "Top queries"],
              ["pages", "Top pages"],
            ] as const
          ).map(([key, label]) => (
            <button
              key={key}
              type="button"
              role="tab"
              aria-selected={kind === key}
              onClick={() => {
                setKind(key);
                setOffset(0);
              }}
              className="-mb-px border-b-2 border-transparent px-4 py-2 text-sm font-medium text-muted-foreground aria-selected:border-primary aria-selected:text-foreground"
            >
              {label}
            </button>
          ))}
        </div>
        <div role="group" aria-label="Conversational queries" className="flex items-center gap-1">
          {SEGMENTS.map((s) => (
            <Button
              key={s.value}
              size="sm"
              variant={segment === s.value ? "default" : "outline"}
              aria-pressed={segment === s.value}
              onClick={() => {
                setSegment(s.value);
                setOffset(0);
              }}
            >
              {s.label}
            </Button>
          ))}
        </div>
      </div>

      {data && data.conversational_impressions > 0 && (
        <p className="text-xs text-muted-foreground">
          About {fmtPct(share, 1)} of impressions in this period come from queries that look like follow-up messages in
          an AI conversation (&quot;yes&quot;, &quot;tell me more&quot;). This is a <strong>heuristic</strong>: it can be
          wrong in both directions. They are left out of the CTR curve and of every opportunity.
        </p>
      )}

      <Card className="py-0">
        {table.isPending && <Skeleton className="m-4 h-40" aria-label="Loading table" />}
        {table.isError && (
          <p role="alert" className="px-5 py-4 text-sm text-destructive">
            Could not load this table: {table.error.message}
          </p>
        )}
        {data && <Table kind={kind} data={data} onOpenPage={onOpenPage} />}
        {data && data.total > PAGE_SIZE && (
          <div className="flex items-center justify-between border-t px-5 py-2 text-xs text-muted-foreground">
            <span>
              {offset + 1}–{Math.min(offset + PAGE_SIZE, data.total)} of {fmtInt(data.total)}
            </span>
            <span className="flex gap-1">
              <Button size="sm" variant="outline" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>
                Previous
              </Button>
              <Button size="sm" variant="outline" disabled={offset + PAGE_SIZE >= data.total} onClick={() => setOffset(offset + PAGE_SIZE)}>
                Next
              </Button>
            </span>
          </div>
        )}
      </Card>
      {data && <p className="text-xs text-muted-foreground">{data.note}</p>}
    </section>
  );
}
