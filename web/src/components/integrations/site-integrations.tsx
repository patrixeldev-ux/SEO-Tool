import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { AlertTriangle, CheckCircle2, ExternalLink, RefreshCw } from "lucide-react";
import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import type { IntegrationStatus, Site, SiteIntegrations } from "@/lib/api/types";
import { useAuth } from "@/lib/auth";
import { redirectTo } from "@/lib/navigate";
import { backfillText, connectionLabel } from "@/lib/search-utils";

const REASONS: Record<string, string> = {
  denied: "Google access was not granted, so nothing was connected.",
  not_allowed: "Only owners and admins can connect Google.",
  exchange_failed: "Google did not accept the sign-in. Please try again.",
  no_refresh_token:
    "Google did not give long-term access. Remove this app at myaccount.google.com/permissions and connect again.",
  state_invalid: "The sign-in took too long or was changed. Please try again.",
  missing_state: "The sign-in did not start from this app. Please try again.",
  missing_code: "Google did not send a sign-in code. Please try again.",
  google_error: "Google reported a problem. Please try again.",
};

export function returnMessage(search: Record<string, unknown>): { kind: "ok" | "error"; text: string } | null {
  if (search.google === "connected") return { kind: "ok", text: "Google is connected. Now choose the Search Console property below." };
  if (search.google === "error") {
    const reason = typeof search.reason === "string" ? search.reason : "";
    return { kind: "error", text: REASONS[reason] ?? "Google could not be connected. Please try again." };
  }
  return null;
}

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : "Something went wrong";
}

function Row({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section aria-label={title} className="space-y-3 border-t pt-4 first:border-t-0 first:pt-0">
      <h3 className="text-sm font-semibold">{title}</h3>
      {children}
    </section>
  );
}

function ConfirmDisconnect({
  what,
  onConfirm,
  pending,
}: {
  what: string;
  onConfirm: () => void;
  pending: boolean;
}) {
  const [asking, setAsking] = useState(false);
  if (!asking) {
    return (
      <Button variant="outline" size="sm" onClick={() => setAsking(true)}>
        Disconnect
      </Button>
    );
  }
  return (
    <div role="group" aria-label={`Confirm disconnecting ${what}`} className="flex flex-wrap items-center gap-2 text-sm">
      <span>Disconnect {what}? Data already imported is kept.</span>
      <Button variant="destructive" size="sm" disabled={pending} onClick={onConfirm}>
        {pending ? "Disconnecting…" : "Yes, disconnect"}
      </Button>
      <Button variant="ghost" size="sm" onClick={() => setAsking(false)}>
        Cancel
      </Button>
    </div>
  );
}

// ---- Google -------------------------------------------------------------------------------------
function GoogleSection({
  site,
  integrations,
  status,
  canAdmin,
}: {
  site: Site;
  integrations: SiteIntegrations;
  status: IntegrationStatus;
  canAdmin: boolean;
}) {
  const { client, activeOrg } = useAuth();
  const queryClient = useQueryClient();
  const conn = integrations.google;
  const label = connectionLabel(conn);
  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ["site-integrations", activeOrg?.id, site.id] });
    void queryClient.invalidateQueries({ queryKey: ["search-coverage", activeOrg?.id, site.id] });
  };

  const connect = useMutation({
    mutationFn: () => client.googleConnect(site.id),
    onSuccess: (data) => redirectTo(data.authorization_url),
  });
  const disconnect = useMutation({ mutationFn: () => client.googleDisconnect(site.id), onSuccess: refresh });
  const syncNow = useMutation({ mutationFn: () => client.googleSync(site.id, "incremental"), onSuccess: refresh });

  const hasProperty = !!conn?.property_url;
  const coverage = useQuery({
    queryKey: ["search-coverage", activeOrg?.id, site.id],
    queryFn: () => client.searchCoverage(site.id),
    enabled: hasProperty,
    refetchInterval: (query) => (query.state.data?.active_job ? 4000 : false),
  });

  return (
    <Row title="Google Search Console">
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant={label.tone}>{label.text}</Badge>
        {conn?.property_url && <span className="text-sm text-muted-foreground">{conn.property_url}</span>}
      </div>

      {!status.google.configured && (
        <div role="status" className="space-y-2 rounded-lg border border-dashed p-4 text-sm">
          <p className="font-medium">Google isn&apos;t set up yet</p>
          <p className="text-muted-foreground">
            {status.google.message} To read your Search Console data this app needs a free Google sign-in key, which
            you create once.
          </p>
          <Button asChild variant="outline" size="sm">
            <Link to="/help/google-setup">Open the setup guide</Link>
          </Button>
        </div>
      )}

      {status.google.configured && !conn && (
        <div className="space-y-2">
          <p className="text-sm text-muted-foreground">
            Connect Google Search Console to see clicks, impressions and ranking opportunities. Read-only access.
          </p>
          {canAdmin ? (
            <Button onClick={() => connect.mutate()} disabled={connect.isPending}>
              {connect.isPending ? "Opening Google…" : "Connect Google Search Console"}
            </Button>
          ) : (
            <p className="text-sm text-muted-foreground">Ask an owner or admin to connect Google.</p>
          )}
        </div>
      )}

      {conn?.status === "needs_reauth" && (
        <div role="alert" className="space-y-2 rounded-lg border border-destructive/40 p-4 text-sm">
          <p className="flex items-center gap-2 font-medium text-destructive">
            <AlertTriangle className="size-4" /> Google access needs to be renewed
          </p>
          <p className="text-muted-foreground">
            {conn.last_error ?? "Access was removed or has expired."} Data already imported is still available.
          </p>
          {canAdmin && (
            <Button onClick={() => connect.mutate()} disabled={connect.isPending}>
              Reconnect Google
            </Button>
          )}
        </div>
      )}

      {conn && conn.status !== "needs_reauth" && !hasProperty && (
        <PropertyPicker site={site} canAdmin={canAdmin} onChosen={refresh} />
      )}

      {hasProperty && conn?.status === "connected" && coverage.data && (
        <div className="space-y-2">
          {coverage.data.active_job && (
            <div
              role="progressbar"
              aria-label="Search Console import progress"
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={coverage.data.active_job.progress}
              className="h-2 overflow-hidden rounded-full bg-muted"
            >
              <div
                className="h-full rounded-full bg-primary transition-[width]"
                style={{ width: `${coverage.data.active_job.progress}%` }}
              />
            </div>
          )}
          <p className="text-sm text-muted-foreground" aria-live="polite">
            {backfillText(coverage.data)}
          </p>
          {coverage.data.last_error && (
            <p role="alert" className="text-sm text-destructive">
              Last update problem: {coverage.data.last_error}
            </p>
          )}
          {conn.last_sync_at && (
            <p className="text-xs text-muted-foreground">Last updated {new Date(conn.last_sync_at).toLocaleString()}</p>
          )}
        </div>
      )}

      {hasProperty && canAdmin && (
        <div className="flex flex-wrap items-center gap-2">
          {conn?.status === "connected" && (
            <Button
              variant="outline"
              size="sm"
              disabled={syncNow.isPending || !!coverage.data?.active_job}
              onClick={() => syncNow.mutate()}
            >
              <RefreshCw /> Update now
            </Button>
          )}
          <Button variant="ghost" size="sm" asChild>
            <Link to="/search-console/$siteId" params={{ siteId: site.id }}>
              Open Search Console
            </Link>
          </Button>
        </div>
      )}
      {conn && canAdmin && (
        <ConfirmDisconnect what="Google" pending={disconnect.isPending} onConfirm={() => disconnect.mutate()} />
      )}
      {[connect.error, disconnect.error, syncNow.error].map(
        (error, i) =>
          error && (
            <p key={i} role="alert" className="text-sm text-destructive">
              {errorText(error)}
            </p>
          ),
      )}
    </Row>
  );
}

function PropertyPicker({ site, canAdmin, onChosen }: { site: Site; canAdmin: boolean; onChosen: () => void }) {
  const { client, activeOrg } = useAuth();
  const [choice, setChoice] = useState<string | null>(null);
  const list = useQuery({
    queryKey: ["google-properties", activeOrg?.id, site.id],
    queryFn: () => client.googleProperties(site.id),
  });
  const choose = useMutation({
    mutationFn: (siteUrl: string) => client.selectGoogleProperty(site.id, siteUrl),
    onSuccess: onChosen,
  });

  if (list.isPending) return <Skeleton className="h-16 w-full" aria-label="Loading Search Console properties" />;
  if (list.isError) {
    return (
      <p role="alert" className="text-sm text-destructive">
        Could not list your Search Console properties: {list.error.message}
      </p>
    );
  }
  const usable = list.data.properties.filter((p) => p.usable);
  if (usable.length === 0) {
    return (
      <p role="status" className="text-sm text-muted-foreground">
        The connected Google account has no Search Console property it can read. Add this website in Search Console
        first (search.google.com/search-console), then come back.
      </p>
    );
  }
  const value = choice ?? list.data.suggested ?? usable[0]!.site_url;
  return (
    <form
      className="space-y-2"
      onSubmit={(e) => {
        e.preventDefault();
        choose.mutate(value);
      }}
    >
      <Label htmlFor={`property-${site.id}`}>Which Search Console property is {site.domain}?</Label>
      <div className="flex flex-wrap items-center gap-2">
        <select
          id={`property-${site.id}`}
          value={value}
          onChange={(e) => setChoice(e.target.value)}
          className="h-9 min-w-0 max-w-full rounded-md border border-input bg-card px-3 text-sm"
        >
          {usable.map((p) => (
            <option key={p.site_url} value={p.site_url}>
              {p.site_url}
              {p.site_url === list.data.suggested ? " (suggested)" : ""}
            </option>
          ))}
        </select>
        <Button type="submit" disabled={!canAdmin || choose.isPending}>
          {choose.isPending ? "Saving…" : "Use this property"}
        </Button>
      </div>
      <p className="text-xs text-muted-foreground">
        The first import covers up to 16 months of history and runs in the background.
      </p>
      {choose.isError && (
        <p role="alert" className="text-sm text-destructive">
          {errorText(choose.error)}
        </p>
      )}
    </form>
  );
}

// ---- Bing ---------------------------------------------------------------------------------------
function BingSection({ site, integrations, canAdmin }: { site: Site; integrations: SiteIntegrations; canAdmin: boolean }) {
  const { client, activeOrg } = useAuth();
  const queryClient = useQueryClient();
  const [key, setKey] = useState("");
  const [siteUrl, setSiteUrl] = useState("");
  const conn = integrations.bing;
  const label = connectionLabel(conn);
  const refresh = () => void queryClient.invalidateQueries({ queryKey: ["site-integrations", activeOrg?.id, site.id] });
  const save = useMutation({
    mutationFn: () => client.saveBingKey(site.id, key.trim(), siteUrl.trim() || undefined),
    onSuccess: () => {
      setKey("");
      refresh();
    },
  });
  const remove = useMutation({ mutationFn: () => client.bingDisconnect(site.id), onSuccess: refresh });

  return (
    <Row title="Bing Webmaster Tools">
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant={label.tone}>{label.text}</Badge>
        {conn?.property_url && <span className="text-sm text-muted-foreground">{conn.property_url}</span>}
      </div>
      {conn?.status === "needs_reauth" && (
        <p role="alert" className="text-sm text-destructive">
          Bing rejected the saved key: {conn.last_error ?? "it may have been regenerated"}. Enter the key again.
        </p>
      )}
      {conn?.status === "connected" && conn.last_sync_at && (
        <p className="text-xs text-muted-foreground">Last updated {new Date(conn.last_sync_at).toLocaleString()}</p>
      )}
      {conn?.status === "connected" && conn.last_error && (
        <p role="alert" className="text-sm text-destructive">
          Last update problem: {conn.last_error}
        </p>
      )}
      {(!conn || conn.status === "needs_reauth") &&
        (canAdmin ? (
          <form
            className="space-y-2"
            onSubmit={(e) => {
              e.preventDefault();
              if (key.trim().length >= 8) save.mutate();
            }}
          >
            <div className="grid gap-1.5">
              <Label htmlFor={`bing-key-${site.id}`}>Bing Webmaster API key</Label>
              <Input
                id={`bing-key-${site.id}`}
                type="password"
                autoComplete="off"
                value={key}
                onChange={(e) => setKey(e.target.value)}
                placeholder="Paste the key from Bing Webmaster Tools"
              />
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor={`bing-url-${site.id}`}>Site address in Bing (optional)</Label>
              <Input
                id={`bing-url-${site.id}`}
                value={siteUrl}
                onChange={(e) => setSiteUrl(e.target.value)}
                placeholder={`https://${site.uses_www ? "www." : ""}${site.domain}/`}
              />
            </div>
            <Button type="submit" disabled={key.trim().length < 8 || save.isPending}>
              {save.isPending ? "Checking the key…" : "Save and check key"}
            </Button>
            <p className="text-xs text-muted-foreground">
              The key is checked with Bing, then stored encrypted. It is never shown again. Where to find it is in the{" "}
              <Link to="/help/google-setup" className="text-primary underline-offset-4 hover:underline">
                setup guide
              </Link>
              .
            </p>
            {save.isError && (
              <p role="alert" className="text-sm text-destructive">
                {errorText(save.error)}
              </p>
            )}
          </form>
        ) : (
          <p className="text-sm text-muted-foreground">Ask an owner or admin to add the Bing key.</p>
        ))}
      {conn && canAdmin && (
        <ConfirmDisconnect what="Bing" pending={remove.isPending} onConfirm={() => remove.mutate()} />
      )}
      {remove.isError && (
        <p role="alert" className="text-sm text-destructive">
          {errorText(remove.error)}
        </p>
      )}
    </Row>
  );
}

// ---- PageSpeed Insights ---------------------------------------------------------------------------
function PsiSection({ integrations }: { integrations: SiteIntegrations }) {
  const { psi } = integrations;
  return (
    <Row title="PageSpeed Insights">
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant={psi.enabled ? "success" : "secondary"}>{psi.enabled ? "On" : "Off"}</Badge>
        <Badge variant={psi.key_configured ? "success" : "secondary"}>
          {psi.key_configured ? "API key set" : "No API key"}
        </Badge>
      </div>
      <p className="text-sm text-muted-foreground">
        {psi.enabled
          ? "Each audit tests your 20 busiest pages for loading speed, responsiveness and layout shift."
          : "Speed tests are switched off on this server (PSI_ENABLED=false)."}{" "}
        {psi.enabled && !psi.key_configured
          ? "It works without a key, but Google allows only a few tests per day that way. Add PSI_API_KEY to your .env file for more."
          : ""}
      </p>
    </Row>
  );
}

// ---- one site -----------------------------------------------------------------------------------
export function SiteIntegrationsCard({ site, status, canAdmin }: { site: Site; status: IntegrationStatus; canAdmin: boolean }) {
  const { client, activeOrg } = useAuth();
  const integrations = useQuery({
    queryKey: ["site-integrations", activeOrg?.id, site.id],
    queryFn: () => client.siteIntegrations(site.id),
  });
  return (
    <Card aria-label={`Integrations for ${site.display_name}`}>
      <CardHeader>
        <CardTitle>{site.display_name}</CardTitle>
        <CardDescription>
          {site.uses_www ? "www." : ""}
          {site.domain}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {integrations.isPending && <Skeleton className="h-24 w-full" aria-label="Loading integrations" />}
        {integrations.isError && (
          <p role="alert" className="text-sm text-destructive">
            Could not load integrations: {integrations.error.message}
          </p>
        )}
        {integrations.data && (
          <>
            <GoogleSection site={site} integrations={integrations.data} status={status} canAdmin={canAdmin} />
            <BingSection site={site} integrations={integrations.data} canAdmin={canAdmin} />
            <PsiSection integrations={integrations.data} />
          </>
        )}
      </CardContent>
    </Card>
  );
}

export function IntegrationsPanel({ banner }: { banner: { kind: "ok" | "error"; text: string } | null }) {
  const { client, activeOrg } = useAuth();
  const status = useQuery({ queryKey: ["integration-status"], queryFn: () => client.integrationStatus() });
  const sites = useQuery({
    queryKey: ["sites", activeOrg?.id],
    queryFn: () => client.listSites(),
    enabled: !!activeOrg,
  });
  const canAdmin = activeOrg?.role === "owner" || activeOrg?.role === "admin";

  return (
    <section aria-label="Integrations" className="mt-8 space-y-4">
      <div>
        <h2 className="text-lg font-semibold">Integrations</h2>
        <p className="text-sm text-muted-foreground">
          Connect each site to Google Search Console and Bing Webmaster Tools. Everything else in the app works
          without them.
        </p>
      </div>
      {banner && (
        <p
          role={banner.kind === "error" ? "alert" : "status"}
          className={`flex items-start gap-2 rounded-lg border p-3 text-sm ${banner.kind === "error" ? "border-destructive/40 text-destructive" : "border-success/40"}`}
        >
          {banner.kind === "ok" ? <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-success" /> : <AlertTriangle className="mt-0.5 size-4 shrink-0" />}
          {banner.text}
        </p>
      )}
      {(status.isPending || sites.isPending) && <Skeleton className="h-32 w-full" aria-label="Loading integrations" />}
      {status.isError && (
        <p role="alert" className="text-sm text-destructive">
          Could not check the integration setup: {status.error.message}
        </p>
      )}
      {sites.data && sites.data.length === 0 && (
        <Card className="items-center py-10 text-center">
          <CardContent className="space-y-2">
            <p className="font-medium">No sites yet</p>
            <p className="text-sm text-muted-foreground">Add a website first, then connect its search data here.</p>
            <Button asChild variant="outline" size="sm">
              <Link to="/sites">Go to Sites</Link>
            </Button>
          </CardContent>
        </Card>
      )}
      {status.data &&
        sites.data?.map((site) => <SiteIntegrationsCard key={site.id} site={site} status={status.data} canAdmin={canAdmin} />)}
      <p className="flex items-center gap-1 text-xs text-muted-foreground">
        <ExternalLink className="size-3" /> New to this?{" "}
        <Link to="/help/google-setup" className="text-primary underline-offset-4 hover:underline">
          Read the step-by-step Google setup guide
        </Link>
      </p>
    </section>
  );
}
