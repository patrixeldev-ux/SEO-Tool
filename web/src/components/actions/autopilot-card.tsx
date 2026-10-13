import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import type { AutopilotSettings } from "@/lib/api/types";

export function AutopilotCard({
  enabled,
  canChange,
  busy,
  onChange,
  settings,
  onSaveCap,
}: {
  enabled: boolean;
  canChange: boolean;
  busy: boolean;
  onChange: (next: boolean) => void;
  settings?: AutopilotSettings;
  onSaveCap?: (cap: number) => void;
}) {
  const [cap, setCap] = useState(String(settings?.weekly_cap ?? 20));
  useEffect(() => {
    if (settings) setCap(String(settings.weekly_cap));
  }, [settings?.weekly_cap]); // eslint-disable-line react-hooks/exhaustive-deps
  const capNumber = Number(cap);
  const capValid = Number.isInteger(capNumber) && capNumber >= 0 && capNumber <= (settings?.max_cap ?? 200);

  return (
    <Card className="py-4">
      <CardContent className="space-y-3">
        <div className="flex flex-wrap items-center gap-4">
          <div className="min-w-0 flex-1">
            <h2 id="autopilot-label" className="text-sm font-semibold">
              Autopilot
            </h2>
            <p className="text-sm text-muted-foreground">
              When on, Search OS applies low-risk changes (titles, descriptions, alt text, structured data, internal links) by
              itself, once a day, a few at a time. Each change is checked on the live page and undone automatically if it does
              not show up. Everything else always waits for you.
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
        </div>
        {settings && (
          <div className="space-y-2 border-t pt-3 text-sm">
            <form
              className="flex flex-wrap items-center gap-2"
              onSubmit={(e) => {
                e.preventDefault();
                if (capValid) onSaveCap?.(capNumber);
              }}
            >
              <label htmlFor="autopilot-cap" className="text-muted-foreground">
                Most changes per week
              </label>
              <Input
                id="autopilot-cap"
                type="number"
                min={0}
                max={settings.max_cap}
                value={cap}
                disabled={!canChange}
                onChange={(e) => setCap(e.target.value)}
                className="h-8 w-24"
              />
              <Button size="sm" variant="outline" type="submit" disabled={!canChange || !capValid || busy || capNumber === settings.weekly_cap}>
                Save
              </Button>
              <span className="text-muted-foreground">
                {settings.used_this_week} used in the last 7 days. A page is changed at most once every 28 days, so you can tell what worked.
              </span>
            </form>
            {enabled && !settings.ready && (
              <p role="status" className="text-warning">
                Autopilot is on but cannot deploy yet: {settings.note}
              </p>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
