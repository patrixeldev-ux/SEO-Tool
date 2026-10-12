import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import type { Opportunity, OpportunityType } from "@/lib/api/types";
import { useAuth } from "@/lib/auth";
import { fmtInt, fmtScore, shortUrl, formatDay } from "@/lib/search-utils";
import { OPPORTUNITY_LABELS, OPPORTUNITY_TYPES } from "@/lib/search-utils";

const PAGE_SIZE = 50;

function evidenceRows(o: Opportunity): [string, string][] {
  return Object.entries(o.evidence)
    .filter(([, v]) => v !== null && typeof v !== "object")
    .map(([k, v]) => [k.replaceAll("_", " "), typeof v === "number" ? String(Math.round(v * 10000) / 10000) : String(v)]);
}

export function OpportunityRow({ item, onOpenPage }: { item: Opportunity; onOpenPage: (url: string) => void }) {
  return (
    <li className="space-y-2 px-5 py-4">
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant="secondary">{item.title}</Badge>
        {item.query && <span className="font-medium">&ldquo;{item.query}&rdquo;</span>}
        {item.page && (
          <button
            type="button"
            onClick={() => onOpenPage(item.page as string)}
            className="min-w-0 truncate text-sm text-primary underline-offset-4 hover:underline"
            title={item.page}
          >
            {shortUrl(item.page)}
          </button>
        )}
        <span className="ml-auto text-sm tabular-nums" title="Estimated clicks or impressions at stake over 28 days">
          Score {fmtScore(item.score)}
        </span>
      </div>
      <p className="text-sm">{item.explanation}</p>
      <details className="text-xs text-muted-foreground">
        <summary className="cursor-pointer">Evidence</summary>
        <dl className="mt-1 grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5">
          {evidenceRows(item).map(([k, v]) => (
            <div key={k} className="contents">
              <dt>{k}</dt>
              <dd className="tabular-nums">{v}</dd>
            </div>
          ))}
        </dl>
      </details>
    </li>
  );
}

export function OpportunitiesPanel({ siteId, onOpenPage }: { siteId: string; onOpenPage: (url: string) => void }) {
  const { client, activeOrg } = useAuth();
  const [type, setType] = useState<OpportunityType | "ALL">("ALL");
  const [offset, setOffset] = useState(0);
  const query = useQuery({
    queryKey: ["opportunities", activeOrg?.id, siteId, type, offset],
    queryFn: () =>
      client.opportunities(siteId, { type: type === "ALL" ? undefined : type, limit: PAGE_SIZE, offset }),
    placeholderData: (previous) => previous,
  });
  const data = query.data;
  const total = data ? Object.values(data.counts).reduce((a, b) => a + (b ?? 0), 0) : 0;

  return (
    <section aria-label="Opportunities" className="space-y-3">
      <div role="group" aria-label="Opportunity type" className="flex flex-wrap gap-1">
        <Button
          size="sm"
          variant={type === "ALL" ? "default" : "outline"}
          aria-pressed={type === "ALL"}
          onClick={() => {
            setType("ALL");
            setOffset(0);
          }}
        >
          All{data ? ` (${fmtInt(total)})` : ""}
        </Button>
        {OPPORTUNITY_TYPES.map((t) => (
          <Button
            key={t}
            size="sm"
            variant={type === t ? "default" : "outline"}
            aria-pressed={type === t}
            title={OPPORTUNITY_LABELS[t].short}
            onClick={() => {
              setType(t);
              setOffset(0);
            }}
          >
            {OPPORTUNITY_LABELS[t].title}
            {data ? ` (${fmtInt(data.counts[t] ?? 0)})` : ""}
          </Button>
        ))}
      </div>

      {data?.computed_at && (
        <p className="text-xs text-muted-foreground">
          Based on {formatDay(data.window_start ?? "")} to {formatDay(data.window_end ?? "")}. Compared with your own
          click-through curve (
          {data.curve_summary === "site"
            ? "built from this site's data"
            : data.curve_summary === "pooled"
              ? "partly from your other sites, because this site has little data at some positions"
              : "a general industry estimate, because this site has little data yet: low confidence"}
          ).
        </p>
      )}
      {data && Object.entries(data.skipped).map(([t, reason]) => (
        <p key={t} className="text-xs text-muted-foreground">
          {OPPORTUNITY_LABELS[t as OpportunityType]?.title ?? t} was not checked: {reason}
        </p>
      ))}

      <Card className="py-0">
        {query.isPending && <Skeleton className="m-4 h-40" aria-label="Loading opportunities" />}
        {query.isError && (
          <p role="alert" className="px-5 py-4 text-sm text-destructive">
            Could not load opportunities: {query.error.message}
          </p>
        )}
        {data && !data.computed && (
          <p className="px-5 py-6 text-sm text-muted-foreground">
            Opportunities appear after the first Search Console import has finished.
          </p>
        )}
        {data?.computed && data.items.length === 0 && (
          <p className="px-5 py-6 text-sm text-muted-foreground">
            No opportunities of this kind right now. That is a good sign, or there is not enough data yet.
          </p>
        )}
        {data && data.items.length > 0 && (
          <ul className="divide-y" aria-label="Opportunities list">
            {data.items.map((item) => (
              <OpportunityRow key={item.id} item={item} onOpenPage={onOpenPage} />
            ))}
          </ul>
        )}
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
    </section>
  );
}
