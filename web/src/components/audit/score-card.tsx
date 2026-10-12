import { Card, CardContent } from "@/components/ui/card";
import type { Audit, Category } from "@/lib/api/types";
import { BAND_LABEL, CATEGORIES, CATEGORY_LABEL, formatDelta, formatScore, scoreBand, type ScoreBand } from "@/lib/audit-utils";
import { cn } from "@/lib/utils";

const BAND_TEXT: Record<ScoreBand, string> = {
  good: "text-success",
  okay: "text-warning",
  poor: "text-destructive",
  unscored: "text-muted-foreground",
};
const BAND_BAR: Record<ScoreBand, string> = {
  good: "bg-success",
  okay: "bg-warning",
  poor: "bg-destructive",
  unscored: "bg-muted-foreground/40",
};

function Subscore({ category, value }: { category: Category; value: number | null | undefined }) {
  const band = scoreBand(value);
  return (
    <li className="space-y-1.5">
      <div className="flex items-baseline justify-between gap-2 text-sm">
        <span>{CATEGORY_LABEL[category]}</span>
        <span className={cn("font-semibold tabular-nums", BAND_TEXT[band])} data-testid={`subscore-${category}`}>
          {formatScore(value)}
        </span>
      </div>
      <div className="h-1.5 overflow-hidden rounded-full bg-muted" aria-hidden="true">
        <div className={cn("h-full rounded-full", BAND_BAR[band])} style={{ width: `${Math.max(0, Math.min(100, value ?? 0))}%` }} />
      </div>
    </li>
  );
}

export function ScoreCard({ audit, delta }: { audit: Audit; delta?: number | null }) {
  const band = scoreBand(audit.score);
  const deltaText = formatDelta(delta);
  return (
    <Card>
      <CardContent className="grid gap-6 sm:grid-cols-[auto_1fr] sm:items-center">
        <div className="flex flex-col items-center gap-1 sm:items-start sm:pr-6">
          <p className="text-sm font-medium text-muted-foreground">Audit score</p>
          <p className={cn("text-6xl leading-none font-semibold tabular-nums", BAND_TEXT[band])} data-testid="audit-score" aria-label={`Audit score ${formatScore(audit.score)} out of 100`}>
            {formatScore(audit.score)}
          </p>
          <p className="text-sm text-muted-foreground">
            {BAND_LABEL[band]}
            {deltaText && <span> · {deltaText} since last audit</span>}
          </p>
          {audit.score === null && (
            <p className="max-w-48 text-center text-xs text-muted-foreground sm:text-left">
              Nothing could be scored, for example because no page was reachable.
            </p>
          )}
        </div>
        <ul className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3" aria-label="Scores by category">
          {CATEGORIES.map((category) => (
            <Subscore key={category} category={category} value={audit.subscores[category]} />
          ))}
        </ul>
      </CardContent>
    </Card>
  );
}
