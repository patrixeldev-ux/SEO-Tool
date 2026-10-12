import { Card, CardContent } from "@/components/ui/card";

export function AutopilotCard({
  enabled,
  canChange,
  busy,
  onChange,
}: {
  enabled: boolean;
  canChange: boolean;
  busy: boolean;
  onChange: (next: boolean) => void;
}) {
  return (
    <Card className="py-4">
      <CardContent className="flex flex-wrap items-center gap-4">
        <div className="min-w-0 flex-1">
          <h2 id="autopilot-label" className="text-sm font-semibold">
            Autopilot
          </h2>
          <p className="text-sm text-muted-foreground">
            When on, low-risk changes (titles, descriptions, alt text, structured data, internal links) will be applied without
            asking, with a check afterwards and a rollback. Everything else always waits for you.{" "}
            <strong className="font-medium text-foreground">Deploying arrives in Phase 4</strong>: for now this only saves your
            choice.
          </p>
          {!canChange && <p className="mt-1 text-xs text-muted-foreground">Only owners and admins can change this.</p>}
        </div>
        <button
          type="button"
          role="switch"
          aria-checked={enabled}
          aria-labelledby="autopilot-label"
          disabled={!canChange || busy}
          onClick={() => onChange(!enabled)}
          className="relative inline-flex h-6 w-11 shrink-0 items-center rounded-full border bg-muted transition-colors outline-none focus-visible:ring-[3px] focus-visible:ring-ring disabled:opacity-50 aria-checked:bg-primary"
        >
          <span className="sr-only">{enabled ? "Autopilot is on" : "Autopilot is off"}</span>
          <span
            aria-hidden
            className={`inline-block size-4 rounded-full bg-white shadow transition-transform ${enabled ? "translate-x-6" : "translate-x-1"}`}
          />
        </button>
      </CardContent>
    </Card>
  );
}
