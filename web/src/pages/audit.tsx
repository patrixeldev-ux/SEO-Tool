import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "@tanstack/react-router";
import { AlertTriangle, ArrowLeft, Download, ListChecks, Play } from "lucide-react";
import { useState } from "react";

import { ChangesCard } from "@/components/audit/changes-card";
import { IssueList } from "@/components/audit/issue-list";
import { PageDrawer } from "@/components/audit/page-drawer";
import { PagesTable } from "@/components/audit/pages-table";
import { ScoreCard } from "@/components/audit/score-card";
import { PageHeader } from "@/components/page-header";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import type { Audit, Site } from "@/lib/api/types";
import { auditPoll } from "@/lib/audit-config";
import { isActive, pickCurrent, progressText, stopReasonText } from "@/lib/audit-utils";
import { useAuth } from "@/lib/auth";

const DEFAULT_PAGE_LIMIT = 500;

function siteLabel(site: Site): string {
  return `${site.uses_www ? "www." : ""}${site.domain}`;
}

// ---- /audit: pick a site ------------------------------------------------------------------------
export function AuditIndexPage() {
  const { client, activeOrg } = useAuth();
  const sites = useQuery({
    queryKey: ["sites", activeOrg?.id],
    queryFn: () => client.listSites(),
    enabled: !!activeOrg,
  });

  return (
    <>
      <PageHeader
        title="Audit"
        description="Technical, on-page and AI-readiness issues found by crawling your site. Pick a site to see its audit."
      />
      {sites.isPending && <Skeleton className="h-24 w-full" aria-label="Loading sites" />}
      {sites.isError && (
        <p role="alert" className="text-sm text-destructive">
          Could not load sites: {sites.error.message}
        </p>
      )}
      {sites.data && sites.data.length === 0 && (
        <Card className="items-center py-14 text-center">
          <CardContent className="flex max-w-sm flex-col items-center gap-3">
            <h2 className="text-lg font-semibold">No sites to audit yet</h2>
            <p className="text-sm text-muted-foreground">Add a website first, then come back to run its first audit.</p>
            <Button asChild>
              <Link to="/sites">Go to Sites</Link>
            </Button>
          </CardContent>
        </Card>
      )}
      {sites.data && sites.data.length > 0 && (
        <Card className="py-0">
          <ul className="divide-y" aria-label="Choose a site">
            {sites.data.map((site) => (
              <li key={site.id}>
                <Link
                  to="/audit/$siteId"
                  params={{ siteId: site.id }}
                  className="flex items-center gap-3 px-5 py-4 transition-colors hover:bg-accent/50"
                >
                  <ListChecks className="size-4 text-muted-foreground" />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate font-medium">{site.display_name}</span>
                    <span className="block truncate text-sm text-muted-foreground">{siteLabel(site)}</span>
                  </span>
                  <span className="text-sm text-primary">Open audit</span>
                </Link>
              </li>
            ))}
          </ul>
        </Card>
      )}
    </>
  );
}

// ---- /audit/$siteId -----------------------------------------------------------------------------
function RunControls({
  site,
  hasAudits,
  running,
  canRun,
  onRun,
  pending,
  error,
}: {
  site: Site;
  hasAudits: boolean;
  running: boolean;
  canRun: boolean;
  onRun: (pageLimit: number) => void;
  pending: boolean;
  error: string | null;
}) {
  const [limit, setLimit] = useState(String(DEFAULT_PAGE_LIMIT));
  const parsed = Number(limit);
  const valid = Number.isInteger(parsed) && parsed >= 1 && parsed <= 10000;
  if (!canRun) return null;
  return (
    <form
      className="flex flex-wrap items-end gap-3"
      onSubmit={(e) => {
        e.preventDefault();
        if (valid) onRun(parsed);
      }}
    >
      <div className="grid gap-1.5">
        <Label htmlFor="page-limit">Page limit</Label>
        <Input
          id="page-limit"
          type="number"
          inputMode="numeric"
          min={1}
          max={10000}
          value={limit}
          onChange={(e) => setLimit(e.target.value)}
          className="w-28"
          aria-describedby="page-limit-help"
        />
      </div>
      <Button type="submit" disabled={!valid || running || pending} aria-label={`Run audit for ${site.display_name}`}>
        <Play /> {pending ? "Starting…" : hasAudits ? "Run audit again" : "Run audit"}
      </Button>
      <p id="page-limit-help" className="basis-full text-xs text-muted-foreground">
        The crawl stops after this many pages (1–10,000). Only {siteLabel(site)} is crawled.
      </p>
      {error && (
        <p role="alert" className="basis-full text-sm text-destructive">
          {error}
        </p>
      )}
    </form>
  );
}

function ProgressCard({ audit, canCancel, onCancel }: { audit: Audit; canCancel: boolean; onCancel: () => void }) {
  return (
    <Card>
      <CardContent className="space-y-3">
        <div className="flex items-center justify-between gap-3">
          <h2 className="font-semibold">{audit.status === "queued" ? "Audit queued" : "Audit running"}</h2>
          {canCancel && (
            <Button variant="outline" size="sm" onClick={onCancel}>
              Cancel
            </Button>
          )}
        </div>
        <div
          role="progressbar"
          aria-label="Audit progress"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={audit.progress}
          className="h-2 overflow-hidden rounded-full bg-muted"
        >
          <div className="h-full rounded-full bg-primary transition-[width]" style={{ width: `${audit.progress}%` }} />
        </div>
        <p className="text-sm text-muted-foreground" aria-live="polite">
          {audit.progress}% · {progressText(audit)}
        </p>
      </CardContent>
    </Card>
  );
}

function AuditResults({ audit }: { audit: Audit }) {
  const { client, activeOrg } = useAuth();
  const [tab, setTab] = useState<"issues" | "pages">("issues");
  const [pageId, setPageId] = useState<string | null>(null);
  const [downloadError, setDownloadError] = useState<string | null>(null);

  const issues = useQuery({
    queryKey: ["audit-issues", activeOrg?.id, audit.id],
    queryFn: () => client.auditIssues(audit.id),
  });
  const changes = useQuery({
    queryKey: ["audit-changes", activeOrg?.id, audit.id],
    queryFn: () => client.auditChanges(audit.id),
  });

  async function downloadCsv() {
    setDownloadError(null);
    try {
      const blob = await client.auditCsv(audit.id);
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `audit-${audit.created_at.slice(0, 10)}.csv`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
    } catch (error) {
      setDownloadError(error instanceof Error ? error.message : "Download failed");
    }
  }

  const stop = stopReasonText(audit.stop_reason);
  const notes = [...audit.warnings, ...(stop ? [stop] : [])];

  return (
    <div className="space-y-6">
      <ScoreCard audit={audit} delta={changes.data?.score_delta} />

      <div className="flex flex-wrap items-center gap-x-6 gap-y-2 text-sm text-muted-foreground">
        <span>{audit.pages_crawled} pages crawled</span>
        <span>{audit.issue_counts.total ?? 0} issues found</span>
        {audit.finished_at && <span>Finished {new Date(audit.finished_at).toLocaleString()}</span>}
        <Button variant="outline" size="sm" className="ml-auto" onClick={() => void downloadCsv()}>
          <Download /> Export CSV
        </Button>
      </div>
      {downloadError && (
        <p role="alert" className="text-sm text-destructive">
          Could not download the CSV: {downloadError}
        </p>
      )}

      {notes.length > 0 && (
        <ul aria-label="Notes about this audit" className="space-y-1.5">
          {notes.map((note) => (
            <li key={note} className="flex items-start gap-2 text-sm text-muted-foreground">
              <AlertTriangle className="mt-0.5 size-4 shrink-0 text-warning" />
              {note}
            </li>
          ))}
        </ul>
      )}

      {changes.data && <ChangesCard changes={changes.data} />}

      <div>
        <div role="tablist" aria-label="Audit results" className="mb-4 flex gap-1 border-b">
          {(
            [
              ["issues", "Issues"],
              ["pages", "Pages"],
            ] as const
          ).map(([key, label]) => (
            <button
              key={key}
              type="button"
              role="tab"
              id={`tab-${key}`}
              aria-selected={tab === key}
              aria-controls={`panel-${key}`}
              onClick={() => setTab(key)}
              className="-mb-px border-b-2 border-transparent px-4 py-2 text-sm font-medium text-muted-foreground aria-selected:border-primary aria-selected:text-foreground"
            >
              {label}
            </button>
          ))}
        </div>
        <div role="tabpanel" id={`panel-${tab}`} aria-labelledby={`tab-${tab}`}>
          {tab === "issues" && (
            <>
              {issues.isPending && <Skeleton className="h-40 w-full" aria-label="Loading issues" />}
              {issues.isError && (
                <p role="alert" className="text-sm text-destructive">
                  Could not load issues: {issues.error.message}
                </p>
              )}
              {issues.data && <IssueList issues={issues.data} onOpenPage={setPageId} />}
            </>
          )}
          {tab === "pages" && <PagesTable auditId={audit.id} onOpenPage={setPageId} />}
        </div>
      </div>

      <PageDrawer auditId={audit.id} pageId={pageId} onClose={() => setPageId(null)} />
    </div>
  );
}

export function SiteAuditPage() {
  const { siteId } = useParams({ from: "/app/audit/$siteId" });
  const { client, activeOrg } = useAuth();
  const queryClient = useQueryClient();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const canRun = activeOrg ? activeOrg.role !== "client_viewer" : false;

  const sites = useQuery({
    queryKey: ["sites", activeOrg?.id],
    queryFn: () => client.listSites(),
    enabled: !!activeOrg,
  });
  const audits = useQuery({
    queryKey: ["audits", activeOrg?.id, siteId],
    queryFn: () => client.listAudits(siteId),
    enabled: !!activeOrg,
    refetchInterval: (query) => (query.state.data?.some((a) => isActive(a.status)) ? auditPoll.ms : false),
  });

  const start = useMutation({
    mutationFn: (pageLimit: number) => client.startAudit(siteId, pageLimit),
    onSuccess: async (audit) => {
      setSelectedId(audit.id);
      await queryClient.invalidateQueries({ queryKey: ["audits", activeOrg?.id, siteId] });
    },
  });
  const cancel = useMutation({
    mutationFn: (auditId: string) => client.cancelAudit(auditId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["audits", activeOrg?.id, siteId] }),
  });

  const site = sites.data?.find((s) => s.id === siteId);
  const list = audits.data ?? [];
  const current = pickCurrent(list, selectedId);
  const running = list.some((a) => isActive(a.status));

  if (sites.data && !site) {
    return (
      <>
        <PageHeader title="Audit" />
        <p role="alert" className="text-sm text-destructive">
          We could not find this site in the current organisation.
        </p>
        <Link to="/audit" className="text-sm text-primary underline-offset-4 hover:underline">
          Back to all sites
        </Link>
      </>
    );
  }

  return (
    <>
      <Link
        to="/audit"
        className="mb-3 inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft className="size-3.5" /> All sites
      </Link>
      <PageHeader
        title={site ? `Audit: ${site.display_name}` : "Audit"}
        description={site ? `${siteLabel(site)}. Crawls the site and checks it for technical, on-page and AI-readiness issues.` : undefined}
      />

      {site && (
        <div className="mb-6">
          <RunControls
            site={site}
            hasAudits={list.length > 0}
            running={running}
            canRun={canRun}
            onRun={(limit) => start.mutate(limit)}
            pending={start.isPending}
            error={start.error ? start.error.message : null}
          />
        </div>
      )}

      {audits.isPending && <Skeleton className="h-40 w-full" aria-label="Loading audits" />}
      {audits.isError && (
        <p role="alert" className="text-sm text-destructive">
          Could not load audits: {audits.error.message}
        </p>
      )}

      {audits.data && list.length === 0 && (
        <Card className="items-center py-14 text-center">
          <CardContent className="flex max-w-md flex-col items-center gap-3">
            <span className="flex size-12 items-center justify-center rounded-full bg-accent text-accent-foreground">
              <ListChecks className="size-5" />
            </span>
            <h2 className="text-lg font-semibold">No audit yet</h2>
            <p className="text-sm text-muted-foreground">
              {canRun
                ? "Click Run audit to crawl this site. You will get a score, a list of what to fix and why it matters."
                : "No audit has been run for this site yet. Ask someone on your team to run one."}
            </p>
          </CardContent>
        </Card>
      )}

      {list.length > 1 && current && (
        <div className="mb-4 flex items-center gap-2 text-sm">
          <Label htmlFor="audit-history">Audit from</Label>
          <select
            id="audit-history"
            className="h-9 rounded-md border bg-card px-2"
            value={current.id}
            onChange={(e) => setSelectedId(e.target.value)}
          >
            {list.map((a) => (
              <option key={a.id} value={a.id}>
                {new Date(a.created_at).toLocaleString()} ({a.status})
              </option>
            ))}
          </select>
        </div>
      )}

      {current && isActive(current.status) && (
        <ProgressCard audit={current} canCancel={canRun} onCancel={() => cancel.mutate(current.id)} />
      )}

      {current?.status === "failed" && (
        <Card>
          <CardContent className="space-y-1">
            <h2 className="font-semibold">The audit could not finish</h2>
            <p role="alert" className="text-sm text-destructive">
              {current.error ?? "Something went wrong."}
            </p>
            {canRun && <p className="text-sm text-muted-foreground">Check the site is online, then run the audit again.</p>}
          </CardContent>
        </Card>
      )}

      {current?.status === "cancelled" && (
        <Card>
          <CardContent>
            <h2 className="font-semibold">This audit was cancelled</h2>
            <p className="text-sm text-muted-foreground">No results were saved for it. Run the audit again when you are ready.</p>
          </CardContent>
        </Card>
      )}

      {current?.status === "done" && <AuditResults key={current.id} audit={current} />}
    </>
  );
}
