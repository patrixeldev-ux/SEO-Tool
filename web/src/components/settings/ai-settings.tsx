import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, X } from "lucide-react";
import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { useAuth } from "@/lib/auth";

function Present({ yes, what }: { yes: boolean; what: string }) {
  return (
    <li className="flex items-center gap-2 text-sm">
      {yes ? <Check className="size-4 text-success" aria-hidden /> : <X className="size-4 text-muted-foreground" aria-hidden />}
      <span>
        {what}: <strong className="font-medium">{yes ? "present" : "not set"}</strong>
      </span>
    </li>
  );
}

function tokens(n: number): string {
  return n.toLocaleString("en-US");
}

export function AiSettingsPanel() {
  const { client, activeOrg } = useAuth();
  const queryClient = useQueryClient();
  const canAdmin = activeOrg?.role === "owner" || activeOrg?.role === "admin";
  const query = useQuery({ queryKey: ["ai-settings", activeOrg?.id], queryFn: () => client.aiSettings(), enabled: !!activeOrg });
  const [draft, setDraft] = useState<string | null>(null);
  const save = useMutation({
    mutationFn: (value: number | null) => client.setAiBudget(value),
    onSuccess: (data) => {
      queryClient.setQueryData(["ai-settings", activeOrg?.id], data);
      setDraft(null);
    },
  });

  if (query.isPending) return <Skeleton className="h-48 w-full" aria-label="Loading AI settings" />;
  if (query.isError) {
    return (
      <p role="alert" className="text-sm text-destructive">
        Could not load the AI settings: {query.error.message}
      </p>
    );
  }
  const ai = query.data;
  const used = Math.min(ai.tokens_used_this_month, ai.monthly_token_budget || 1);
  const percent = ai.monthly_token_budget > 0 ? Math.round((used / ai.monthly_token_budget) * 100) : 100;
  const parsed = draft === null ? null : Number(draft.replaceAll(",", "").trim());
  const draftValid = parsed !== null && Number.isInteger(parsed) && parsed >= 0;

  return (
    <div className="grid gap-4 md:grid-cols-2">
      <Card className="md:col-span-2">
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            AI provider
            <Badge variant={ai.configured ? "success" : "warning"}>{ai.configured ? "Ready" : "Not set up"}</Badge>
          </CardTitle>
          <CardDescription>
            The AI writes titles, descriptions, alt text and structured data for the action plan. Everything it writes is
            checked by fixed rules before you see it. Without it the plan still works; those suggestions wait.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {ai.configured ? (
            <p className="text-sm">
              In use: <strong className="font-medium capitalize">{ai.provider}</strong>, model{" "}
              <code className="rounded bg-muted px-1 py-0.5 text-xs">{ai.model}</code>
              {ai.selected_by === "LLM_PROVIDER" ? " (chosen with LLM_PROVIDER)." : " (the first key found)."}
            </p>
          ) : (
            <p className="text-sm text-muted-foreground">No AI is in use right now.</p>
          )}
          {ai.problem && (
            <p role="alert" className="text-sm text-destructive">
              {ai.problem}
            </p>
          )}
          <ul className="space-y-1" aria-label="Keys found in the server settings">
            <Present yes={ai.anthropic_key_present} what="Anthropic key (ANTHROPIC_API_KEY)" />
            <Present yes={ai.gemini_key_present} what="Gemini key (GEMINI_API_KEY)" />
            <Present yes={ai.gemini_model_set} what="Gemini model name (GEMINI_MODEL)" />
          </ul>
          <div className="rounded-md border bg-muted/40 px-3 py-2 text-sm">
            <p className="font-medium">Keys are set in the <code>.env</code> file, never here.</p>
            <p className="text-muted-foreground">
              Open <code>.env</code> in the project folder, add <code>ANTHROPIC_API_KEY=…</code> (or{" "}
              <code>GEMINI_API_KEY=…</code> and <code>GEMINI_MODEL=…</code>), restart Search OS, then press Re-plan on the
              Actions page. This page only shows whether a key is present; it can never show the key itself.
            </p>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Monthly token budget</CardTitle>
          <CardDescription>
            A cap on how much AI this organisation can use each month. Resets on the 1st (since {ai.month_starts}).
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <p className="text-sm">
            <strong className="font-medium">{tokens(ai.tokens_used_this_month)}</strong> of {tokens(ai.monthly_token_budget)} tokens used
            ({percent}%){ai.budget_is_custom ? "" : ", the default"}.
          </p>
          <div
            role="progressbar"
            aria-label="Tokens used this month"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={percent}
            className="h-2 overflow-hidden rounded-full bg-muted"
          >
            <div className={`h-full ${percent >= 90 ? "bg-destructive" : "bg-primary"}`} style={{ width: `${percent}%` }} />
          </div>
          {canAdmin ? (
            <form
              className="flex flex-wrap items-center gap-2"
              onSubmit={(e) => {
                e.preventDefault();
                if (draftValid) save.mutate(parsed);
              }}
            >
              <label htmlFor="budget" className="sr-only">
                Monthly token budget
              </label>
              <Input
                id="budget"
                inputMode="numeric"
                value={draft ?? String(ai.monthly_token_budget)}
                onChange={(e) => setDraft(e.target.value)}
                className="w-40"
                aria-invalid={draft !== null && !draftValid}
              />
              <Button type="submit" size="sm" disabled={!draftValid || save.isPending}>
                Save budget
              </Button>
              {ai.budget_is_custom && (
                <Button type="button" size="sm" variant="ghost" onClick={() => save.mutate(null)} disabled={save.isPending}>
                  Use the default
                </Button>
              )}
            </form>
          ) : (
            <p className="text-xs text-muted-foreground">Only owners and admins can change the budget.</p>
          )}
          {save.isError && (
            <p role="alert" className="text-sm text-destructive">
              {save.error.message}
            </p>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Where it went this month</CardTitle>
          <CardDescription>AI calls by task.</CardDescription>
        </CardHeader>
        <CardContent>
          {ai.usage_by_purpose.length === 0 ? (
            <p className="text-sm text-muted-foreground">Nothing used yet this month.</p>
          ) : (
            <table className="w-full text-sm">
              <thead className="text-left text-xs text-muted-foreground">
                <tr>
                  <th className="pb-1 font-medium">Task</th>
                  <th className="pb-1 text-right font-medium">Calls</th>
                  <th className="pb-1 text-right font-medium">Tokens</th>
                </tr>
              </thead>
              <tbody>
                {ai.usage_by_purpose.map((row) => (
                  <tr key={row.purpose} className="border-t">
                    <td className="py-1">{row.purpose.replaceAll("_", " ")}</td>
                    <td className="py-1 text-right tabular-nums">{tokens(row.calls)}</td>
                    <td className="py-1 text-right tabular-nums">{tokens(row.tokens)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
