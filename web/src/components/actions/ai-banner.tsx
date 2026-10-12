import { Link } from "@tanstack/react-router";
import { Sparkles } from "lucide-react";

import type { ActionSummary } from "@/lib/api/types";

/** Shown while suggestions are waiting for an AI: no key, a misconfigured key, or an exhausted budget. */
export function NeedsAiBanner({ summary }: { summary: ActionSummary }) {
  if (summary.needs_ai === 0) return null;
  const { ai } = summary;
  const count = summary.needs_ai;
  const what = `${count} suggestion${count === 1 ? " is" : "s are"} waiting for an AI`;
  return (
    <div role="status" className="flex flex-wrap items-center gap-3 rounded-xl border border-warning/40 bg-warning/10 px-5 py-4 text-sm">
      <Sparkles className="size-5 shrink-0 text-warning" />
      <div className="min-w-0 flex-1">
        <p className="font-medium">
          {what}.
        </p>
        <p className="text-muted-foreground">
          {!ai.configured
            ? ai.problem
              ? `${ai.problem} `
              : "No AI key is set, so titles, descriptions, alt text and structured data cannot be written automatically. Everything else in the plan works. "
            : "An AI is set up, but the monthly budget may be used up or the last plan stopped early. Re-plan to continue. "}
          You can also write any of them yourself.
        </p>
      </div>
      <Link to="/settings" search={{ tab: "ai" }} className="shrink-0 font-medium text-primary underline-offset-4 hover:underline">
        Open Settings &gt; AI
      </Link>
    </div>
  );
}
