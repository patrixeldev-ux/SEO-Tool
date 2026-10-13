import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "@tanstack/react-router";
import { ArrowLeft, Bell, ExternalLink, History, Undo2 } from "lucide-react";
import { useState } from "react";

import { DeploymentBadge } from "@/components/deploy/deployment-badge";
import { PageHeader } from "@/components/page-header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import type { DeployBatch, Site } from "@/lib/api/types";
import { useAuth } from "@/lib/auth";
import { BATCH_LABELS, countsText, METHOD_LABELS, showValue } from "@/lib/deploy-utils";
import { shortUrl } from "@/lib/search-utils";

function siteLabel(site: Site): string {
  return `${site.uses_www ? "www." : ""}${site.domain}`;
}

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : "Something went wrong";
}

// ---- /deployments: pick a site, and the notifications ------------------------------------------------------
export function DeploymentsIndexPage() {
  const { client, activeOrg } = useAuth();
  const sites = useQuery({ queryKey: ["sites", activeOrg?.id], queryFn: () => client.listSites(), enabled: !!activeOrg });
  return (
    <>
      <PageHeader
        title="Deployments"
        description="What was changed on your websites, when, and how to undo it. Pick a site."
      />
      <NotificationsPanel />
      {sites.isPending && <Skeleton className="h-24 w-full" aria-label="Loading sites" />}
      {sites.isError && (
        <p role="alert" className="text-sm text-destructive">
          Could not load sites: {sites.error.message}
        </p>
      )}
      {sites.data && sites.data.length === 0 && (
        <Card className="items-center py-14 text-center">
          <CardContent className="flex max-w-sm flex-col items-center gap-3">
            <h2 className="text-lg font-semibold">No sites yet</h2>
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
                  to="/deployments/$siteId"
                  params={{ siteId: site.id }}
                  className="flex items-center gap-3 px-5 py-4 transition-colors hover:bg-accent/50"
                >
                  <History className="size-4 text-muted-foreground" />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate font-medium">{site.display_name}</span>
                    <span className="block truncate text-sm text-muted-foreground">{siteLabel(site)}</span>
                  </span>
                  <span className="text-sm text-primary">Open</span>
                </Link>
              </li>
            ))}
          </ul>
        </Card>
      )}
    </>
  );
}

export function NotificationsPanel() {
  const { client, activeOrg } = useAuth();
  const queryClient = useQueryClient();
  const query = useQuery({ queryKey: ["notifications", activeOrg?.id], queryFn: () => client.notifications(), enabled: !!activeOrg });
  const readAll = useMutation({
    mutationFn: () => client.readAllNotifications(),
    onSuccess: (data) => queryClient.setQueryData(["notifications", activeOrg?.id], data),
  });
  const items = query.data?.items.slice(0, 5) ?? [];
  if (items.length === 0) return null;
  return (
    <Card className="mb-4 py-4" aria-label="Notifications">
      <CardContent className="space-y-3">
        <div className="flex items-center gap-2">
          <h2 className="flex items-center gap-2 text-sm font-semibold">
            <Bell className="size-4" /> Notifications
            {!!query.data?.unread && <Badge variant="warning">{query.data.unread} new</Badge>}
          </h2>
          {!!query.data?.unread && (
            <Button size="sm" variant="ghost" className="ml-auto" onClick={() => readAll.mutate()} disabled={readAll.isPending}>
              Mark all as read
            </Button>
          )}
        </div>
        <ul className="divide-y rounded-md border">
          {items.map((n) => (
            <li key={n.id} className="space-y-1 px-3 py-2 text-sm" data-read={n.read}>
              <p className={n.read ? "text-muted-foreground" : "font-medium"}>{n.title}</p>
              <p className="whitespace-pre-line text-xs text-muted-foreground">{n.body}</p>
              <p className="text-xs text-muted-foreground">{new Date(n.created_at).toLocaleString()}</p>
              {n.site_id && (
                <Link to="/deployments/$siteId" params={{ siteId: n.site_id }} className="text-xs text-primary underline">
                  See what changed
                </Link>
              )}
            </li>
          ))}
        </ul>
      </CardContent>
    </Card>
  );
}

// ---- /deployments/$siteId ---------------------------------------------------------------------------------------
export function SiteDeploymentsPage() {
  const { siteId } = useParams({ from: "/app/deployments/$siteId" });
  const { client, activeOrg } = useAuth();
  const queryClient = useQueryClient();
  const orgId = activeOrg?.id;
  const canAdmin = activeOrg?.role === "owner" || activeOrg?.role === "admin";
  const [notice, setNotice] = useState<{ kind: "ok" | "error"; text: string } | null>(null);
  const [open, setOpen] = useState<string | null>(null);

  const sites = useQuery({ queryKey: ["sites", orgId], queryFn: () => client.listSites(), enabled: !!activeOrg });
  const site = sites.data?.find((s) => s.id === siteId);
  const batches = useQuery({ queryKey: ["deploy-batches", orgId, siteId], queryFn: () => client.deployBatches(siteId) });

  const undoBatch = useMutation({
    mutationFn: (batch: DeployBatch) => client.rollbackBatch(siteId, batch.id),
    onSuccess: (result) => {
      setNotice(
        result.reverted.length
          ? { kind: "ok", text: `Rolled back ${result.reverted.length} change${result.reverted.length === 1 ? "" : "s"}.` }
          : { kind: "error", text: result.skipped[0]?.reason ?? "Nothing to roll back." },
      );
      void queryClient.invalidateQueries({ queryKey: ["deploy-batches", orgId, siteId] });
      void queryClient.invalidateQueries({ queryKey: ["deploy-batch", orgId, siteId] });
      void queryClient.invalidateQueries({ queryKey: ["deployments", orgId, siteId] });
    },
    onError: (error) => setNotice({ kind: "error", text: errorText(error) }),
  });

  return (
    <>
      <PageHeader
        title={site ? `Deployments: ${site.display_name}` : "Deployments"}
        description="Every batch of changes sent to this website, what each page looked like before and after, and a way back."
        actions={
          <Button asChild variant="ghost" size="sm">
            <Link to="/deployments">
              <ArrowLeft /> All sites
            </Link>
          </Button>
        }
      />
      {notice && (
        <p role={notice.kind === "error" ? "alert" : "status"} className={`mb-4 text-sm ${notice.kind === "error" ? "text-destructive" : "text-success"}`}>
          {notice.text}
        </p>
      )}
      {batches.isPending && <Skeleton className="h-32 w-full" aria-label="Loading deployments" />}
      {batches.isError && (
        <p role="alert" className="text-sm text-destructive">
          Could not load deployments: {batches.error.message}
        </p>
      )}
      {batches.data && batches.data.length === 0 && (
        <Card className="items-center py-14 text-center">
          <CardContent className="flex max-w-md flex-col items-center gap-3">
            <History className="size-8 text-muted-foreground" />
            <h2 className="text-lg font-semibold">Nothing deployed yet</h2>
            <p className="text-sm text-muted-foreground">
              Approve some actions, then press Deploy approved. Each batch you send shows up here.
            </p>
            <Button asChild>
              <Link to="/actions/$siteId" params={{ siteId }}>
                Go to Actions
              </Link>
            </Button>
          </CardContent>
        </Card>
      )}
      {batches.data && batches.data.length > 0 && (
        <ul className="space-y-3" aria-label="Deployment batches">
          {batches.data.map((batch) => (
            <li key={batch.id}>
              <Card className="py-4" data-testid="batch">
                <CardContent className="space-y-3">
                  <div className="flex flex-wrap items-center gap-3">
                    <div className="min-w-0 flex-1">
                      <p className="text-sm font-medium">
                        {new Date(batch.created_at).toLocaleString()} through {METHOD_LABELS[batch.method]}
                        {batch.trigger === "autopilot" && (
                          <Badge variant="secondary" className="ml-2">
                            Autopilot
                          </Badge>
                        )}
                      </p>
                      <p className="text-xs text-muted-foreground">{countsText(batch.counts)}</p>
                    </div>
                    <Badge variant={batch.status === "failed" ? "destructive" : batch.status === "verified" ? "success" : batch.status === "reverted" ? "outline" : "warning"}>
                      {BATCH_LABELS[batch.status]}
                    </Badge>
                    <Button size="sm" variant="outline" aria-expanded={open === batch.id} onClick={() => setOpen(open === batch.id ? null : batch.id)}>
                      {open === batch.id ? "Hide changes" : "Show changes"}
                    </Button>
                    {canAdmin && batch.status !== "reverted" && batch.status !== "failed" && (
                      <Button size="sm" variant="outline" disabled={undoBatch.isPending} onClick={() => undoBatch.mutate(batch)}>
                        <Undo2 /> Roll back batch
                      </Button>
                    )}
                  </div>
                  {open === batch.id && <BatchDetail siteId={siteId} batchId={batch.id} canAdmin={canAdmin} />}
                </CardContent>
              </Card>
            </li>
          ))}
        </ul>
      )}
    </>
  );
}

function BatchDetail({ siteId, batchId, canAdmin }: { siteId: string; batchId: string; canAdmin: boolean }) {
  const { client, activeOrg } = useAuth();
  const queryClient = useQueryClient();
  const orgId = activeOrg?.id;
  const detail = useQuery({ queryKey: ["deploy-batch", orgId, siteId, batchId], queryFn: () => client.deployBatch(siteId, batchId) });
  const undo = useMutation({
    mutationFn: (id: string) => client.rollbackDeployments(siteId, [id]),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["deploy-batch", orgId, siteId, batchId] });
      void queryClient.invalidateQueries({ queryKey: ["deploy-batches", orgId, siteId] });
    },
  });
  if (detail.isPending) return <Skeleton className="h-16 w-full" aria-label="Loading changes" />;
  if (detail.isError) return <p role="alert" className="text-sm text-destructive">Could not load this batch: {detail.error.message}</p>;
  return (
    <div className="space-y-2">
      <ul aria-label="Changes in this batch" className="divide-y rounded-md border">
        {detail.data.deployments.map((d) => (
          <li key={d.id} data-testid="batch-deployment" className="space-y-2 px-3 py-2 text-sm">
            <div className="flex flex-wrap items-center gap-3">
              <DeploymentBadge status={d.status} />
              <span className="min-w-0 flex-1 truncate font-medium">{shortUrl(d.url)}</span>
              <span className="text-xs text-muted-foreground">{d.field.replaceAll("_", " ")}</span>
              {d.pr_url && (
                <a href={d.pr_url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-xs text-primary underline">
                  Pull request <ExternalLink className="size-3" />
                </a>
              )}
              {d.can_rollback && canAdmin && (
                <Button size="sm" variant="outline" disabled={undo.isPending} onClick={() => undo.mutate(d.id)}>
                  <Undo2 /> Roll back
                </Button>
              )}
            </div>
            <dl className="grid gap-2 text-xs sm:grid-cols-2">
              <div className="rounded bg-destructive/10 p-2">
                <dt className="font-medium">Before</dt>
                <dd className="break-words">{showValue(d.before_snapshot?.value)}</dd>
              </div>
              <div className="rounded bg-success/10 p-2">
                <dt className="font-medium">{d.status === "reverted" ? "Put back" : "After"}</dt>
                <dd className="break-words">
                  {d.status === "reverted"
                    ? showValue(d.after_snapshot?.after_rollback ?? d.before_snapshot?.value)
                    : showValue(d.after_snapshot?.value ?? (d.ops[0] as { value?: unknown } | undefined)?.value)}
                </dd>
              </div>
            </dl>
            {d.error && <p className="text-xs text-destructive">{d.error}</p>}
          </li>
        ))}
      </ul>
      {detail.data.skipped.length > 0 && (
        <p className="text-xs text-muted-foreground">
          {detail.data.skipped.length} action{detail.data.skipped.length === 1 ? " was" : "s were"} left out: {detail.data.skipped[0]!.reason}
        </p>
      )}
    </div>
  );
}
