import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import { Globe, ListChecks, Play, Plus } from "lucide-react";
import { useState, type FormEvent } from "react";

import { PageHeader } from "@/components/page-header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { ApiError } from "@/lib/api/client";
import type { Site } from "@/lib/api/types";
import { useAuth } from "@/lib/auth";

function AddSiteDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const { client, activeOrg } = useAuth();
  const queryClient = useQueryClient();
  const [domain, setDomain] = useState("");
  const [displayName, setDisplayName] = useState("");

  const create = useMutation({
    mutationFn: () =>
      client.createSite({ domain: domain.trim(), ...(displayName.trim() ? { display_name: displayName.trim() } : {}) }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["sites", activeOrg?.id] });
      setDomain("");
      setDisplayName("");
      onOpenChange(false);
    },
  });

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    create.mutate();
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) create.reset();
        onOpenChange(next);
      }}
    >
      <DialogContent>
        <form onSubmit={onSubmit} className="grid gap-4">
          <DialogHeader>
            <DialogTitle>Add a site</DialogTitle>
            <DialogDescription>
              Enter the domain you want to optimise. We normalise it for you (no scheme, no “www.”).
            </DialogDescription>
          </DialogHeader>
          <div className="grid gap-2">
            <Label htmlFor="site-domain">Domain</Label>
            <Input
              id="site-domain"
              placeholder="example.com"
              value={domain}
              onChange={(e) => setDomain(e.target.value)}
              required
              autoFocus
              autoCapitalize="none"
              spellCheck={false}
            />
          </div>
          <div className="grid gap-2">
            <Label htmlFor="site-name">Display name (optional)</Label>
            <Input id="site-name" placeholder="Example Inc." value={displayName} onChange={(e) => setDisplayName(e.target.value)} maxLength={120} />
          </div>
          {create.error && (
            <p role="alert" className="text-sm text-destructive">
              {create.error.message}
            </p>
          )}
          <DialogFooter>
            <Button type="submit" disabled={create.isPending || !domain.trim()}>
              {create.isPending ? "Adding…" : "Add site"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function isAlreadyRunning(error: unknown): boolean {
  return error instanceof ApiError && error.status === 409;
}

function SiteRow({ site, canRun }: { site: Site; canRun: boolean }) {
  const { client, activeOrg } = useAuth();
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const run = useMutation({
    mutationFn: () => client.startAudit(site.id),
    onSettled: async (_data, error) => {
      // 409 means an audit is already running: just show it.
      if (error && !isAlreadyRunning(error)) return;
      await queryClient.invalidateQueries({ queryKey: ["audits", activeOrg?.id, site.id] });
      await navigate({ to: "/audit/$siteId", params: { siteId: site.id } });
    },
  });
  return (
    <li className="flex flex-wrap items-center gap-x-4 gap-y-2 px-5 py-4">
      <span className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-accent text-accent-foreground">
        <Globe className="size-4" />
      </span>
      <div className="min-w-0 flex-1">
        <p className="truncate font-medium">{site.display_name}</p>
        <p className="truncate text-sm text-muted-foreground">
          {site.uses_www ? "www." : ""}
          {site.domain}
        </p>
      </div>
      <Badge variant={site.autopilot_enabled ? "success" : "secondary"}>
        Autopilot {site.autopilot_enabled ? "on" : "off"}
      </Badge>
      <time className="hidden text-sm text-muted-foreground sm:block" dateTime={site.created_at}>
        Added {new Date(site.created_at).toLocaleDateString()}
      </time>
      <div className="flex items-center gap-2">
        {canRun && (
          <Button size="sm" onClick={() => run.mutate()} disabled={run.isPending} aria-label={`Run audit for ${site.display_name}`}>
            <Play /> {run.isPending ? "Starting…" : "Run audit"}
          </Button>
        )}
        <Button variant="outline" size="sm" asChild>
          <Link to="/audit/$siteId" params={{ siteId: site.id }} aria-label={`View audit for ${site.display_name}`}>
            <ListChecks /> View audit
          </Link>
        </Button>
      </div>
      {run.isError && !isAlreadyRunning(run.error) && (
        <p role="alert" className="basis-full text-sm text-destructive">
          Could not start the audit: {run.error.message}
        </p>
      )}
    </li>
  );
}

export function SitesPage() {
  const { client, activeOrg } = useAuth();
  const [dialogOpen, setDialogOpen] = useState(false);

  const sites = useQuery({
    queryKey: ["sites", activeOrg?.id],
    queryFn: () => client.listSites(),
    enabled: !!activeOrg,
  });

  const canCreate = activeOrg ? activeOrg.role !== "client_viewer" : false;

  return (
    <>
      <PageHeader
        title="Sites"
        description="The websites in this organisation. Add one to start auditing and optimising it."
        actions={
          canCreate && (
            <Button onClick={() => setDialogOpen(true)}>
              <Plus /> Add site
            </Button>
          )
        }
      />

      {sites.isPending && (
        <Card className="py-0">
          <CardContent className="space-y-4 px-5 py-5" aria-busy="true" aria-label="Loading sites">
            <Skeleton className="h-9 w-full" />
            <Skeleton className="h-9 w-full" />
          </CardContent>
        </Card>
      )}

      {sites.isError && (
        <p role="alert" className="text-sm text-destructive">
          Could not load sites: {sites.error.message}
        </p>
      )}

      {sites.data && sites.data.length === 0 && (
        <Card className="items-center py-14 text-center">
          <CardContent className="flex max-w-sm flex-col items-center gap-3">
            <span className="flex size-12 items-center justify-center rounded-full bg-accent text-accent-foreground">
              <Globe className="size-5" />
            </span>
            <h2 className="text-lg font-semibold">No sites yet</h2>
            <p className="text-sm text-muted-foreground">
              Add your first website to get started, then run an audit to see what to fix first.
            </p>
            {canCreate && (
              <Button onClick={() => setDialogOpen(true)}>
                <Plus /> Add your first site
              </Button>
            )}
          </CardContent>
        </Card>
      )}

      {sites.data && sites.data.length > 0 && (
        <Card className="py-0">
          <ul className="divide-y" aria-label="Sites">
            {sites.data.map((site) => (
              <SiteRow key={site.id} site={site} canRun={canCreate} />
            ))}
          </ul>
        </Card>
      )}

      <AddSiteDialog open={dialogOpen} onOpenChange={setDialogOpen} />
    </>
  );
}
