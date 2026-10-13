import { useNavigate, useRouterState } from "@tanstack/react-router";

import { IntegrationsPanel, returnMessage } from "@/components/integrations/site-integrations";
import { PageHeader } from "@/components/page-header";
import { DeploySettingsPanel } from "@/components/settings/deploy-settings";
import { AiSettingsPanel } from "@/components/settings/ai-settings";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { useAuth } from "@/lib/auth";

type Tab = "general" | "deploy" | "ai";
const TABS: { key: Tab; label: string }[] = [
  { key: "general", label: "General" },
  { key: "deploy", label: "Deploy" },
  { key: "ai", label: "AI" },
];

export function SettingsPage() {
  const { session, activeOrg } = useAuth();
  const navigate = useNavigate();
  const search = useRouterState({ select: (s) => s.location.search }) as Record<string, unknown>;
  if (session.status !== "authenticated" || !activeOrg) return null;
  const tab: Tab = search.tab === "ai" ? "ai" : search.tab === "deploy" ? "deploy" : "general";
  return (
    <>
      <PageHeader title="Settings" description="Your account, the active organisation, its integrations and the AI." />
      <div role="tablist" aria-label="Settings sections" className="mb-6 flex gap-1 border-b">
        {TABS.map(({ key, label }) => (
          <button
            key={key}
            type="button"
            role="tab"
            aria-selected={tab === key}
            onClick={() => void navigate({ to: "/settings", search: key === "general" ? {} : { tab: key } })}
            className="-mb-px border-b-2 border-transparent px-4 py-2 text-sm font-medium text-muted-foreground aria-selected:border-primary aria-selected:text-foreground"
          >
            {label}
          </button>
        ))}
      </div>
      {tab === "ai" ? (
        <AiSettingsPanel />
      ) : tab === "deploy" ? (
        <DeploySettingsPanel />
      ) : (
        <>
          <div className="grid gap-4 md:grid-cols-2">
            <Card>
              <CardHeader>
                <CardTitle>Account</CardTitle>
                <CardDescription>Who you are signed in as.</CardDescription>
              </CardHeader>
              <CardContent className="space-y-1 text-sm">
                <p className="font-medium">{session.user.full_name ?? "—"}</p>
                <p className="text-muted-foreground">{session.user.email}</p>
              </CardContent>
            </Card>
            <Card>
              <CardHeader>
                <CardTitle>Organisation</CardTitle>
                <CardDescription>The workspace you are currently working in.</CardDescription>
              </CardHeader>
              <CardContent className="flex items-center justify-between gap-2 text-sm">
                <p className="font-medium">{activeOrg.name}</p>
                <Badge>{activeOrg.role.replace("_", " ")}</Badge>
              </CardContent>
            </Card>
          </div>
          <IntegrationsPanel banner={returnMessage(search)} />
        </>
      )}
    </>
  );
}
