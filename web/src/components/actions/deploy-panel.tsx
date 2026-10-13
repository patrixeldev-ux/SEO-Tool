import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { ExternalLink, Rocket, Undo2 } from "lucide-react";
import { useState } from "react";

import { DeploymentBadge } from "@/components/deploy/deployment-badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import type { Deployment } from "@/lib/api/types";
import { useAuth } from "@/lib/auth";
import { isBusy, latestByAction, METHOD_LABELS, showValue } from "@/lib/deploy-utils";
import { shortUrl } from "@/lib/search-utils";

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : "Something went wrong";
}

/** Deploy approved changes, see where each one stands, undo any of them. */
export function DeployPanel({
  siteId,
  approvedCount,
  canDeploy,
  onChanged,
}: {
  siteId: string;
  approvedCount: number;
  canDeploy: boolean;
  onChanged: () => void;
}) {
  const { client, activeOrg } = useAuth();
  const queryClient = useQueryClient();
  const orgId = activeOrg?.id;
  const [notice, setNotice] = useState<{ kind: "ok" | "error"; text: string } | null>(null);

  const connections = useQuery({
    queryKey: ["deploy-connections", orgId, siteId],
    queryFn: () => client.deployConnections(siteId),
    enabled: canDeploy,
  });
  const deployments = useQuery({
    queryKey: ["deployments", orgId, siteId],
    queryFn: () => client.deployments(siteId),
    refetchInterval: (query) => ((query.state.data ?? []).some(isBusy) ? 4000 : false),
  });

  function refresh() {
    void queryClient.invalidateQueries({ queryKey: ["deployments", orgId, siteId] });
    void queryClient.invalidateQueries({ queryKey: ["deploy-batches", orgId, siteId] });
    onChanged();
  }

  const deploy = useMutation({
    mutationFn: () => client.deploy(siteId, { all_approved: true }),
    onSuccess: (result) => {
      const done = result.deployments.length;
      const skipped = result.skipped.length;
      setNotice({
        kind: done ? "ok" : "error",
        text:
          (done ? `Sent ${done} change${done === 1 ? "" : "s"} to your site. Search OS now checks each one on the live page.` : "Nothing was deployed.") +
          (skipped ? ` ${skipped} skipped: ${result.skipped[0]!.reason}` : ""),
      });
      refresh();
    },
    onError: (error) => setNotice({ kind: "error", text: errorText(error) }),
  });
  const undo = useMutation({
    mutationFn: (dep: Deployment) => client.rollbackDeployments(siteId, [dep.id]),
    onSuccess: (result) => {
      setNotice(
        result.reverted.length
          ? { kind: "ok", text: "Rolled back. The original value is back on the page." }
          : { kind: "error", text: result.skipped[0]?.reason ?? "Nothing was rolled back." },
      );
      refresh();
    },
    onError: (error) => setNotice({ kind: "error", text: errorText(error) }),
  });

  const conn = connections.data;
  const notVerified = conn && !conn.verified;
  const noMethod = conn && conn.verified && !conn.deploy_method;
  const blocked = notVerified ? "Prove you own this site first." : noMethod ? "Connect a way to deploy first." : null;
  const recent = [...latestByAction(deployments.data ?? []).values()].slice(0, 8);

  return (
    <Card className="py-4" aria-label="Deploying">
      <CardContent className="space-y-3">
        <div className="flex flex-wrap items-center gap-4">
          <div className="min-w-0 flex-1">
            <h2 className="flex items-center gap-2 text-sm font-semibold">
              <Rocket className="size-4" /> Deploy to your website
            </h2>
            <p className="text-sm text-muted-foreground">
              {conn?.deploy_method
                ? `Approved changes are sent through ${METHOD_LABELS[conn.deploy_method]}, then checked on the live page. Every change can be undone.`
                : "Approved changes can be sent to your website, checked on the live page, and undone."}{" "}
              {blocked && (
                <>
                  <strong className="font-medium text-foreground">{blocked}</strong>{" "}
                  <Link to="/settings" search={{ tab: "deploy" }} className="text-primary underline">
                    Open Settings, Deploy
                  </Link>
                  .
                </>
              )}
            </p>
          </div>
          <Button
            onClick={() => deploy.mutate()}
            disabled={!canDeploy || !!blocked || approvedCount === 0 || deploy.isPending || connections.isPending}
          >
            {deploy.isPending ? "Deploying…" : `Deploy approved${approvedCount ? ` (${approvedCount})` : ""}`}
          </Button>
        </div>
        {!canDeploy && <p className="text-xs text-muted-foreground">Only owners and admins can deploy.</p>}
        {notice && (
          <p role={notice.kind === "error" ? "alert" : "status"} className={`text-sm ${notice.kind === "error" ? "text-destructive" : "text-success"}`}>
            {notice.text}
          </p>
        )}
        {recent.length > 0 && (
          <ul aria-label="Deployment status" className="divide-y rounded-md border">
            {recent.map((d) => (
              <li key={d.id} data-testid="deployment-row" className="flex flex-wrap items-center gap-3 px-3 py-2 text-sm">
                <DeploymentBadge status={d.status} />
                <span className="min-w-0 flex-1">
                  <span className="block truncate font-medium">{shortUrl(d.url)}</span>
                  <span className="block truncate text-xs text-muted-foreground">
                    {d.field.replaceAll("_", " ")}: {showValue(d.before_snapshot?.value)} to {showValue((d.ops[0] as { value?: unknown } | undefined)?.value)}
                  </span>
                  {d.error && <span className="block text-xs text-destructive">{d.error}</span>}
                </span>
                {d.pr_url && (
                  <a href={d.pr_url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-xs text-primary underline">
                    Pull request <ExternalLink className="size-3" />
                  </a>
                )}
                {d.can_rollback && canDeploy && (
                  <Button size="sm" variant="outline" disabled={undo.isPending} onClick={() => undo.mutate(d)}>
                    <Undo2 /> Roll back
                  </Button>
                )}
              </li>
            ))}
          </ul>
        )}
        {recent.length > 0 && (
          <p className="text-xs text-muted-foreground">
            <Link to="/deployments/$siteId" params={{ siteId }} className="text-primary underline">
              See the full deployment history
            </Link>
          </p>
        )}
      </CardContent>
    </Card>
  );
}
