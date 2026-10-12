import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { useAuth } from "@/lib/auth";

const PAGE_SIZE = 50;

export function PagesTable({ auditId, onOpenPage }: { auditId: string; onOpenPage: (pageId: string) => void }) {
  const { client, activeOrg } = useAuth();
  const [q, setQ] = useState("");
  const [withIssues, setWithIssues] = useState(false);
  const [offset, setOffset] = useState(0);

  const pages = useQuery({
    queryKey: ["audit-pages", activeOrg?.id, auditId, q, withIssues, offset],
    queryFn: () => client.auditPages(auditId, { q, withIssues, limit: PAGE_SIZE, offset }),
    placeholderData: (previous) => previous,
  });

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <Input
          aria-label="Search pages"
          placeholder="Search by URL"
          value={q}
          onChange={(e) => {
            setQ(e.target.value);
            setOffset(0);
          }}
          className="max-w-xs"
        />
        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={withIssues}
            onChange={(e) => {
              setWithIssues(e.target.checked);
              setOffset(0);
            }}
          />
          Only pages with issues
        </label>
      </div>

      {pages.isPending && <Skeleton className="h-40 w-full" aria-label="Loading pages" />}
      {pages.isError && (
        <p role="alert" className="text-sm text-destructive">
          Could not load pages: {pages.error.message}
        </p>
      )}
      {pages.data && pages.data.items.length === 0 && (
        <p className="text-sm text-muted-foreground">No pages match.</p>
      )}
      {pages.data && pages.data.items.length > 0 && (
        <Card className="overflow-x-auto py-0">
          <table className="w-full text-sm">
            <caption className="sr-only">Crawled pages</caption>
            <thead className="border-b text-left text-xs text-muted-foreground">
              <tr>
                <th className="px-4 py-2 font-medium">URL</th>
                <th className="px-2 py-2 font-medium">Status</th>
                <th className="px-2 py-2 text-right font-medium">Depth</th>
                <th className="px-2 py-2 text-right font-medium">Words</th>
                <th className="px-4 py-2 text-right font-medium">Issues</th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {pages.data.items.map((p) => (
                <tr key={p.id} className="hover:bg-accent/40">
                  <td className="max-w-md px-4 py-2">
                    <button
                      type="button"
                      onClick={() => onOpenPage(p.id)}
                      className="max-w-full truncate text-left text-primary underline-offset-4 hover:underline"
                      title={p.url}
                    >
                      {p.url}
                    </button>
                    {p.title && <p className="truncate text-xs text-muted-foreground">{p.title}</p>}
                  </td>
                  <td className="px-2 py-2">
                    {p.status_code ? (
                      <Badge variant={p.status_code >= 400 ? "destructive" : p.status_code >= 300 ? "warning" : "success"}>
                        {p.status_code}
                      </Badge>
                    ) : (
                      <Badge variant="outline">{p.fetch_error?.replaceAll("_", " ") ?? "not fetched"}</Badge>
                    )}
                  </td>
                  <td className="px-2 py-2 text-right tabular-nums">{p.depth ?? "–"}</td>
                  <td className="px-2 py-2 text-right tabular-nums">{p.word_count}</td>
                  <td className="px-4 py-2 text-right font-medium tabular-nums">{p.issue_count}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}
      {pages.data && pages.data.total > PAGE_SIZE && (
        <div className="flex items-center justify-between text-sm text-muted-foreground">
          <span>
            {offset + 1}–{Math.min(offset + PAGE_SIZE, pages.data.total)} of {pages.data.total}
          </span>
          <div className="flex gap-2">
            <Button variant="outline" size="sm" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>
              Previous
            </Button>
            <Button
              variant="outline"
              size="sm"
              disabled={offset + PAGE_SIZE >= pages.data.total}
              onClick={() => setOffset(offset + PAGE_SIZE)}
            >
              Next
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
