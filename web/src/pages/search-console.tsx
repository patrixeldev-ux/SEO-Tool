import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "@tanstack/react-router";
import { ArrowLeft, ChartLine } from "lucide-react";
import { useState } from "react";

import { PageHeader } from "@/components/page-header";
import { AiReportsPanel } from "@/components/search/ai-reports";
import { OpportunitiesPanel } from "@/components/search/opportunities";
import { SearchPageSheet } from "@/components/search/page-sheet";
import { SearchTables } from "@/components/search/query-tables";
import { TotalsCharts } from "@/components/search/totals-charts";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import type { SearchRange, Site } from "@/lib/api/types";
import { useAuth } from "@/lib/auth";
import { backfillText, RANGES } from "@/lib/search-utils";

function siteLabel(site: Site): string {
  return `${site.uses_www ? "www." : ""}${site.domain}`;
}

// ---- /search-console: pick a site ---------------------------------------------------------------
export function SearchConsoleIndexPage() {
  const { client, activeOrg } = useAuth();
  const sites = useQuery({
    queryKey: ["sites", activeOrg?.id],
    queryFn: () => client.listSites(),
    enabled: !!activeOrg,
  });
  return (
    <>
      <PageHeader
        title="Search Console"
        description="Clicks, impressions, rankings and ranking opportunities from Google Search Console. Pick a site."
      />
      {sites.isPending && <Skeleton className="h-24 w-full" aria-label="Loading sites" />}
      {sites.isError && (
        <p role="alert" className="text-sm text-destructive">
          Could not load sites: {sites.error.message}
        </p>
      )}
      {sites.data && sites.data.length === 0 && (
        <Card className="items-center py-14 text-center">
          <CardContent className="flex max-w-sm flex-col items-center gap-3">
            <h2 className="text-lg font-semibold">No sites yet</h2>
            <p className="text-sm text-muted-foreground">Add a website first, then connect Google Search Console.</p>
            <Button asChild>
              <Link to="/sites">Go to Sites</Link>
            </Button>
          </CardContent>
        </Card>
      )}
      {sites.data && sites.data.length > 0 && (
        <Card className="py-0">
          <ul className="divide-y" aria-label="Choose a site">
            {sites.data.map((site) => (
              <li key={site.id}>
                <Link
                  to="/search-console/$siteId"
                  params={{ siteId: site.id }}
                  className="flex items-center gap-3 px-5 py-4 transition-colors hover:bg-accent/50"
                >
                  <ChartLine className="size-4 text-muted-foreground" />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate font-medium">{site.display_name}</span>
                    <span className="block truncate text-sm text-muted-foreground">{siteLabel(site)}</span>
                  </span>
                  <span className="text-sm text-primary">Open</span>
                </Link>
              </li>
            ))}
          </ul>
        </Card>
      )}
    </>
  );
}

// ---- /search-console/$siteId ----------------------------------------------------------------------
type Tab = "overview" | "opportunities" | "ai";
const TABS: { key: Tab; label: string }[] = [
  { key: "overview", label: "Overview" },
  { key: "opportunities", label: "Opportunities" },
  { key: "ai", label: "AI reports" },
];

export function ConnectPrompt({ configured, canAdmin }: { configured: boolean; canAdmin: boolean }) {
  return (
    <Card className="items-center py-14 text-center">
      <CardContent className="flex max-w-md flex-col items-center gap-3">
        <ChartLine className="size-8 text-muted-foreground" />
        <h2 className="text-lg font-semibold">Connect Google Search Console to see this</h2>
        <p className="text-sm text-muted-foreground">
          {configured
            ? "Once connected, the last 16 months of clicks, impressions and rankings are imported in the background."
            : "Google isn't set up yet. It takes about ten minutes, once, and the guide walks you through it."}
        </p>
        <Button asChild>
          {configured ? <Link to="/settings">Go to Settings</Link> : <Link to="/help/google-setup">Open the setup guide</Link>}
        </Button>
        {!canAdmin && <p className="text-xs text-muted-foreground">Only owners and admins can connect Google.</p>}
      </CardContent>
    </Card>
  );
}

export function SiteSearchPage() {
  const { siteId } = useParams({ from: "/app/search-console/$siteId" });
  const { client, activeOrg } = useAuth();
  const [range, setRange] = useState<SearchRange>("28d");
  const [tab, setTab] = useState<Tab>("overview");
  const [pageUrl, setPageUrl] = useState<string | null>(null);

  const sites = useQuery({
    queryKey: ["sites", activeOrg?.id],
    queryFn: () => client.listSites(),
    enabled: !!activeOrg,
  });
  const site = sites.data?.find((s) => s.id === siteId);
  const coverage = useQuery({
    queryKey: ["search-coverage", activeOrg?.id, siteId],
    queryFn: () => client.searchCoverage(siteId),
    refetchInterval: (query) => (query.state.data?.active_job ? 5000 : false),
  });
  const status = useQuery({ queryKey: ["integration-status"], queryFn: () => client.integrationStatus() });
  const hasData = (coverage.data?.days_stored ?? 0) > 0;
  const totals = useQuery({
    queryKey: ["search-totals", activeOrg?.id, siteId, range, coverage.data?.days_stored],
    queryFn: () => client.searchTotals(siteId, range),
    enabled: hasData,
  });
  const canAdmin = activeOrg?.role === "owner" || activeOrg?.role === "admin";

  if (sites.isSuccess && !site) {
    return (
      <>
        <PageHeader title="Search Console" />
        <p role="alert" className="text-sm text-destructive">
          That site was not found in this organisation.
        </p>
        <Button asChild variant="outline" className="mt-3">
          <Link to="/search-console">Back to sites</Link>
        </Button>
      </>
    );
  }

  return (
    <>
      <PageHeader
        title={site ? `Search Console: ${site.display_name}` : "Search Console"}
        description={site ? siteLabel(site) : undefined}
        actions={
          <Button asChild variant="ghost" size="sm">
            <Link to="/search-console">
              <ArrowLeft /> All sites
            </Link>
          </Button>
        }
      />

      {coverage.isPending && <Skeleton className="h-32 w-full" aria-label="Loading Search Console data" />}
      {coverage.isError && (
        <p role="alert" className="text-sm text-destructive">
          Could not load Search Console data: {coverage.error.message}
        </p>
      )}

      {coverage.data && (
        <div className="space-y-6">
          <div role="tablist" aria-label="Search Console sections" className="flex gap-1 border-b">
            {TABS.map(({ key, label }) => (
              <button
                key={key}
                type="button"
                role="tab"
                aria-selected={tab === key}
                onClick={() => setTab(key)}
                className="-mb-px border-b-2 border-transparent px-4 py-2 text-sm font-medium text-muted-foreground aria-selected:border-primary aria-selected:text-foreground"
              >
                {label}
              </button>
            ))}
          </div>

          {tab === "ai" && <AiReportsPanel siteId={siteId} />}

          {tab !== "ai" && !hasData && !coverage.data.active_job && (
            <ConnectPrompt configured={status.data?.google.configured ?? true} canAdmin={canAdmin} />
          )}
          {tab !== "ai" && !hasData && coverage.data.active_job && (
            <Card>
              <CardContent className="space-y-2">
                <h2 className="font-semibold">Importing your Search Console history</h2>
                <p className="text-sm text-muted-foreground" aria-live="polite">
                  {backfillText(coverage.data)}. This page fills in as days arrive.
                </p>
              </CardContent>
            </Card>
          )}

          {tab !== "ai" && hasData && (
            <>
              {coverage.data.active_job && (
                <p className="text-xs text-muted-foreground" aria-live="polite">
                  {backfillText(coverage.data)}
                </p>
              )}
              {coverage.data.last_error && (
                <p role="alert" className="text-sm text-destructive">
                  The last update had a problem: {coverage.data.last_error}
                </p>
              )}
              {tab === "overview" && (
                <>
                  <div role="group" aria-label="Date range" className="flex items-center gap-1">
                    {RANGES.map((r) => (
                      <Button
                        key={r.value}
                        size="sm"
                        variant={range === r.value ? "default" : "outline"}
                        aria-pressed={range === r.value}
                        onClick={() => setRange(r.value)}
                      >
                        {r.label}
                      </Button>
                    ))}
                  </div>
                  {totals.isPending && <Skeleton className="h-48 w-full" aria-label="Loading charts" />}
                  {totals.isError && (
                    <p role="alert" className="text-sm text-destructive">
                      Could not load the charts: {totals.error.message}
                    </p>
                  )}
                  {totals.data && <TotalsCharts totals={totals.data} />}
                  <SearchTables siteId={siteId} range={range} onOpenPage={setPageUrl} />
                </>
              )}
              {tab === "opportunities" && <OpportunitiesPanel siteId={siteId} onOpenPage={setPageUrl} />}
            </>
          )}
        </div>
      )}

      <SearchPageSheet
        siteId={siteId}
        url={pageUrl}
        range={range}
        onOpenPage={setPageUrl}
        onClose={() => setPageUrl(null)}
      />
    </>
  );
}
