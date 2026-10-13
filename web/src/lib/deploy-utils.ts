import type { BatchStatus, DeployMethod, Deployment, DeploymentStatus } from "@/lib/api/types";

export const METHOD_LABELS: Record<DeployMethod, string> = {
  cloudflare: "Cloudflare",
  wordpress: "WordPress",
  github: "GitHub",
};

export const METHOD_HINTS: Record<DeployMethod, string> = {
  cloudflare: "A small Worker on your Cloudflare site applies the changes as pages are served. Works for any website behind Cloudflare.",
  wordpress: "A plugin on your WordPress site stores the changes per page. Works with Yoast and Rank Math.",
  github: "Changes are proposed as a pull request on your repository. Nothing goes live until you merge it. Best for static HTML sites.",
};

/** The word shown for each stage, in plain language. */
export const DEPLOYMENT_LABELS: Record<DeploymentStatus, string> = {
  pending: "Starting",
  applied: "Deploying",
  pending_merge: "Waiting for merge",
  verified: "Verified live",
  failed: "Failed",
  reverting: "Rolling back",
  reverted: "Rolled back",
};

export const DEPLOYMENT_EXPLAIN: Record<DeploymentStatus, string> = {
  pending: "The change is being prepared.",
  applied: "Sent to your site. Search OS is now checking that the live page shows it.",
  pending_merge: "A pull request is open. The change goes live when you merge it.",
  verified: "Search OS looked at the live page the way a crawler does and the change is there.",
  failed: "The change did not show up on the live page, or could not be applied.",
  reverting: "A pull request that undoes the change is open. Merge it to finish the rollback.",
  reverted: "The original value was put back.",
};

export const DEPLOYMENT_VARIANT: Record<DeploymentStatus, "secondary" | "success" | "warning" | "destructive" | "outline"> = {
  pending: "secondary",
  applied: "warning",
  pending_merge: "warning",
  verified: "success",
  failed: "destructive",
  reverting: "warning",
  reverted: "outline",
};

export const BATCH_LABELS: Record<BatchStatus, string> = {
  pending: "Starting",
  applied: "Deploying",
  verified: "All verified",
  partial: "Partly verified",
  failed: "Failed",
  reverted: "Rolled back",
};

/** Latest deployment per action (the list comes newest first). */
export function latestByAction(deployments: Deployment[]): Map<string, Deployment> {
  const map = new Map<string, Deployment>();
  for (const d of deployments) if (!map.has(d.action_id)) map.set(d.action_id, d);
  return map;
}

export function isBusy(d: Deployment): boolean {
  return d.status === "pending" || d.status === "applied" || d.status === "reverting";
}

export function showValue(value: unknown): string {
  if (value === null || value === undefined || value === "") return "(nothing)";
  if (typeof value === "string") return value;
  return JSON.stringify(value);
}

export function countsText(counts: Record<string, number>): string {
  const order: DeploymentStatus[] = ["verified", "applied", "pending_merge", "reverting", "failed", "reverted", "pending"];
  const parts = order.filter((k) => counts[k]).map((k) => `${counts[k]} ${DEPLOYMENT_LABELS[k].toLowerCase()}`);
  return parts.length ? parts.join(", ") : "nothing deployed";
}
