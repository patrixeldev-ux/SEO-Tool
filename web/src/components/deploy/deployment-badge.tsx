import { Badge } from "@/components/ui/badge";
import type { DeploymentStatus } from "@/lib/api/types";
import { DEPLOYMENT_EXPLAIN, DEPLOYMENT_LABELS, DEPLOYMENT_VARIANT } from "@/lib/deploy-utils";

export function DeploymentBadge({ status }: { status: DeploymentStatus }) {
  return (
    <Badge variant={DEPLOYMENT_VARIANT[status]} title={DEPLOYMENT_EXPLAIN[status]} data-testid="deployment-status">
      {DEPLOYMENT_LABELS[status]}
    </Badge>
  );
}
