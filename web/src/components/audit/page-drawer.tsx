import { useQuery } from "@tanstack/react-query";

import { SeverityBadge } from "@/components/audit/severity-badge";
import { Sheet, SheetContent, SheetDescription, SheetTitle } from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import type { LinkInfo, PageDetail } from "@/lib/api/types";
import { useAuth } from "@/lib/auth";

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[8rem_1fr] gap-2 py-1.5 text-sm">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="min-w-0 break-words">{children ?? "–"}</dd>
    </div>
  );
}

function LinkList({ label, links, total }: { label: string; links: LinkInfo[]; total?: number }) {
  if (links.length === 0) return null;
  return (
    <details className="rounded-lg border px-3 py-2 text-sm">
      <summary className="cursor-pointer font-medium">
        {label} ({total ?? links.length})
      </summary>
      <ul className="mt-2 space-y-1" aria-label={label}>
        {links.map((l, i) => (
          <li key={`${l.url}-${i}`} className="min-w-0">
            <span className="block truncate" title={l.url}>
              {l.url}
            </span>
            {(l.anchor || l.rel) && (
              <span className="block truncate text-xs text-muted-foreground">
                {l.anchor ? `“${l.anchor}”` : ""} {l.rel ? `rel=${l.rel}` : ""}
              </span>
            )}
          </li>
        ))}
      </ul>
      {total !== undefined && total > links.length && (
        <p className="mt-1 text-xs text-muted-foreground">Showing the first {links.length}.</p>
      )}
    </details>
  );
}

function Body({ page }: { page: PageDetail }) {
  return (
    <div className="space-y-5">
      <section aria-label="Issues on this page" className="space-y-2">
        <h3 className="text-sm font-semibold">Issues on this page</h3>
        {page.issues.length === 0 && <p className="text-sm text-muted-foreground">No issues found on this page.</p>}
        <ul className="space-y-2">
          {page.issues.map((issue, i) => (
            <li key={`${issue.check_id}-${i}`} className="space-y-1 rounded-lg border p-3">
              <div className="flex items-center gap-2">
                <SeverityBadge severity={issue.severity} />
                <span className="text-sm font-medium">{issue.title}</span>
              </div>
              <p className="text-sm text-muted-foreground">{issue.how_to_fix}</p>
            </li>
          ))}
        </ul>
      </section>

      <section aria-label="Page facts">
        <h3 className="text-sm font-semibold">Details</h3>
        <dl className="divide-y">
          <Field label="Status">{page.status_code ?? page.fetch_error?.replaceAll("_", " ")}</Field>
          <Field label="Title">{page.title}</Field>
          <Field label="Description">{page.meta_description}</Field>
          <Field label="H1">{page.h1.join(" | ") || undefined}</Field>
          <Field label="Canonical">{page.canonical}</Field>
          <Field label="Robots">{[page.robots_meta, page.x_robots_tag].filter(Boolean).join(" · ") || undefined}</Field>
          <Field label="Language">{page.lang}</Field>
          <Field label="Words">
            {page.word_count}
            {page.main_text_method ? ` (${page.main_text_method})` : ""}
          </Field>
          <Field label="Click depth">{page.depth ?? "Not reachable by links"}</Field>
          <Field label="Inbound links">{page.inlinks_count}</Field>
          <Field label="In sitemap">{page.in_sitemap ? "Yes" : "No"}</Field>
          <Field label="Response time">{page.ttfb_ms === null ? undefined : `${page.ttfb_ms} ms`}</Field>
          <Field label="Structured data">{page.schema_types.join(", ") || "None found"}</Field>
          {page.redirect_chain.length > 0 && (
            <Field label="Redirects">
              <ol className="space-y-0.5">
                {page.redirect_chain.map((hop, i) => (
                  <li key={`${hop.url}-${i}`} className="truncate" title={hop.url}>
                    {hop.status} {hop.url}
                  </li>
                ))}
              </ol>
            </Field>
          )}
        </dl>
      </section>

      <LinkList label="Linked from" links={page.inlinks} total={page.inlinks_count} />
      <LinkList label="Links out" links={page.outlinks} total={page.outlinks_total} />
    </div>
  );
}

export function PageDrawer({
  auditId,
  pageId,
  onClose,
}: {
  auditId: string;
  pageId: string | null;
  onClose: () => void;
}) {
  const { client, activeOrg } = useAuth();
  const page = useQuery({
    queryKey: ["audit-page", activeOrg?.id, auditId, pageId],
    queryFn: () => client.auditPage(auditId, pageId as string),
    enabled: !!pageId,
  });

  return (
    <Sheet open={!!pageId} onOpenChange={(open) => !open && onClose()}>
      <SheetContent aria-describedby="page-drawer-description">
        <div className="space-y-1 pr-8">
          <SheetTitle className="text-lg leading-tight font-semibold break-words">
            {page.data?.url ?? "Page details"}
          </SheetTitle>
          <SheetDescription id="page-drawer-description" className="text-sm text-muted-foreground">
            What the crawler found on this page and what to fix.
          </SheetDescription>
        </div>
        {page.isPending && pageId && <Skeleton className="h-48 w-full" aria-label="Loading page" />}
        {page.isError && (
          <p role="alert" className="text-sm text-destructive">
            Could not load this page: {page.error.message}
          </p>
        )}
        {page.data && <Body page={page.data} />}
      </SheetContent>
    </Sheet>
  );
}
