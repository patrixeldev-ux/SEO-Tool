import { Badge } from "@/components/ui/badge";
import type { Severity } from "@/lib/api/types";
import { SEVERITY_LABEL } from "@/lib/audit-utils";

const VARIANT = {
  critical: "destructive",
  high: "destructive",
  medium: "warning",
  low: "secondary",
  info: "outline",
} as const;

export function SeverityBadge({ severity }: { severity: Severity }) {
  return <Badge variant={VARIANT[severity]}>{SEVERITY_LABEL[severity]}</Badge>;
}
