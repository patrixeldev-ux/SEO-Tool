import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "@tanstack/react-router";
import { ArrowLeft, Download, FileText, RefreshCw, Zap } from "lucide-react";
import { useMemo, useState } from "react";

import { ActionCard, type ActionHandlers } from "@/components/actions/action-card";
import { DeployPanel } from "@/components/actions/deploy-panel";
import { AutopilotCard } from "@/components/actions/autopilot-card";
import { NeedsAiBanner } from "@/components/actions/ai-banner";
import { HandoffDialog } from "@/components/actions/handoff-dialog";
import { ManualTaskCard } from "@/components/actions/manual-task-card";
import { SummaryCards } from "@/components/actions/summary-cards";
import { PageHeader } from "@/components/page-header";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { OPEN_BOARD, STATUS_LABELS } from "@/lib/action-utils";
import type { ActionStatus, BulkOp, PlanAction, RiskClass, Site } from "@/lib/api/types";
import { useAuth } from "@/lib/auth";

const PAGE_SIZE = 50;
const NO_REASON = "Not relevant for this site";

function siteLabel(site: Site): string {
  return `${site.uses_www ? "www." : ""}${site.domain}`;
}

// ---- /actions: pick a site --------------------------------------------------------------------------
export function ActionsIndexPage() {
  const { client, activeOrg } = useAuth();
  const sites = useQuery({
    queryKey: ["sites", activeOrg?.id],
    queryFn: () => client.listSites(),
    enabled: !!activeOrg,
  });
  return (
    <>
      <PageHeader
        title="Actions"
        description="The prioritised plan of fixes for each site, with approve, edit and reject. Pick a site."
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
            <p className="text-sm text-muted-foreground">Add a website first, then run an audit to get a plan.</p>
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
                  to="/actions/$siteId"
                  params={{ siteId: site.id }}
                  className="flex items-center gap-3 px-5 py-4 transition-colors hover:bg-accent/50"
                >
                  <Zap className="size-4 text-muted-foreground" />
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

// ---- /actions/$siteId ---------------------------------------------------------------------------------
type Tab = "plan" | "manual";
type StatusFilter = "open" | ActionStatus;
const STATUS_FILTERS: { value: StatusFilter; label: string }[] = [
  { value: "open", label: "On the board" },
  { value: "proposed", label: STATUS_LABELS.proposed },
  { value: "needs_ai", label: STATUS_LABELS.needs_ai },
  { value: "approved", label: STATUS_LABELS.approved },
  { value: "deployed", label: STATUS_LABELS.deployed },
  { value: "needs_manual", label: STATUS_LABELS.needs_manual },
  { value: "stale", label: STATUS_LABELS.stale },
  { value: "rolled_back", label: STATUS_LABELS.rolled_back },
  { value: "rejected", label: STATUS_LABELS.rejected },
];

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : "Something went wrong";
}

export function SiteActionsPage() {
  const { siteId } = useParams({ from: "/app/actions/$siteId" });
  const { client, activeOrg } = useAuth();
  const queryClient = useQueryClient();
  const [tab, setTab] = useState<Tab>("plan");
  const [risk, setRisk] = useState<RiskClass | "all">("all");
  const [type, setType] = useState<string>("all");
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("open");
  const [shown, setShown] = useState(PAGE_SIZE);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [bulkRejecting, setBulkRejecting] = useState(false);
  const [bulkReason, setBulkReason] = useState("");
  const [notice, setNotice] = useState<{ kind: "ok" | "error"; text: string } | null>(null);
  const [handoffOpen, setHandoffOpen] = useState(false);

  const orgId = activeOrg?.id;
  const canWrite = activeOrg?.role !== "client_viewer";
  const canAdmin = activeOrg?.role === "owner" || activeOrg?.role === "admin";

  const sites = useQuery({
    queryKey: ["sites", orgId],
    queryFn: () => client.listSites(),
    enabled: !!activeOrg,
  });
  const site = sites.data?.find((s) => s.id === siteId);

  const summary = useQuery({
    queryKey: ["action-summary", orgId, siteId],
    queryFn: () => client.actionSummary(siteId),
    refetchInterval: (query) => (query.state.data?.plan_running ? 3000 : false),
  });

  const planStatuses: ActionStatus[] = statusFilter === "open" ? OPEN_BOARD : [statusFilter];
  const plan = useQuery({
    queryKey: ["actions", orgId, siteId, "plan", statusFilter, risk, type],
    queryFn: () =>
      client.listActions(siteId, {
        manual: false,
        status: planStatuses,
        risk_class: risk === "all" ? undefined : [risk],
        type: type === "all" ? undefined : [type],
      }),
    placeholderData: (previous) => previous,
  });
  const manual = useQuery({
    queryKey: ["actions", orgId, siteId, "manual"],
    queryFn: () => client.listActions(siteId, { manual: true, status: ["proposed", "manual_done", "rejected"] }),
  });

  function refresh() {
    void queryClient.invalidateQueries({ queryKey: ["actions", orgId, siteId] });
    void queryClient.invalidateQueries({ queryKey: ["action-summary", orgId, siteId] });
    void queryClient.invalidateQueries({ queryKey: ["handoff", orgId, siteId] });
  }

  const decide = useMutation({
    mutationFn: ({ action, op }: { action: PlanAction; op: "approve" | "reopen" | "done" | "restore" }) =>
      client.decideAction(siteId, action.id, op),
    onSuccess: () => {
      setNotice(null);
      refresh();
    },
    onError: (error) => setNotice({ kind: "error", text: errorText(error) }),
  });
  const reject = useMutation({
    mutationFn: ({ action, reason }: { action: PlanAction; reason: string }) => client.rejectAction(siteId, action.id, reason),
    onSuccess: () => {
      setNotice(null);
      refresh();
    },
    onError: (error) => setNotice({ kind: "error", text: errorText(error) }),
  });
  const tick = useMutation({
    mutationFn: ({ action, done }: { action: PlanAction; done: string[] }) =>
      client.editAction(siteId, action.id, { checklist_done: done }),
    onSuccess: refresh,
    onError: (error) => setNotice({ kind: "error", text: errorText(error) }),
  });
  const bulk = useMutation({
    mutationFn: ({ op, reason }: { op: BulkOp; reason?: string }) => client.bulkActions(siteId, [...selected], op, reason),
    onSuccess: (result, { op }) => {
      const verb = { approve: "Approved", reject: "Rejected", reopen: "Reopened", done: "Marked done" }[op];
      const failed = result.failed.length;
      setNotice({
        kind: failed ? "error" : "ok",
        text:
          `${verb} ${result.updated.length}.` +
          (failed ? ` ${failed} could not be changed: ${result.failed[0]!.error}` : ""),
      });
      setSelected(new Set());
      setBulkRejecting(false);
      setBulkReason("");
      refresh();
    },
    onError: (error) => setNotice({ kind: "error", text: errorText(error) }),
  });
  const replan = useMutation({
    mutationFn: () => client.replan(siteId),
    onSuccess: () => {
      setNotice({ kind: "ok", text: "Planning started. This page updates when it finishes." });
      void queryClient.invalidateQueries({ queryKey: ["action-summary", orgId, siteId] });
    },
    onError: (error) => setNotice({ kind: "error", text: errorText(error) }),
  });
  const autopilotSettings = useQuery({
    queryKey: ["autopilot", orgId, siteId],
    queryFn: () => client.autopilotSettings(siteId),
  });
  const saveCap = useMutation({
    mutationFn: (cap: number) => client.saveAutopilot(siteId, { weekly_cap: cap }),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["autopilot", orgId, siteId] }),
    onError: (error) => setNotice({ kind: "error", text: errorText(error) }),
  });
  const approvedCount = useQuery({
    queryKey: ["actions", orgId, siteId, "approved-count"],
    queryFn: async () => (await client.listActions(siteId, { manual: false, status: ["approved"] })).length,
  });
  const autopilot = useMutation({
    mutationFn: (enabled: boolean) => client.updateSite(siteId, { autopilot_enabled: enabled }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["autopilot", orgId, siteId] });
      void queryClient.invalidateQueries({ queryKey: ["sites", orgId] });
      void queryClient.invalidateQueries({ queryKey: ["action-summary", orgId, siteId] });
    },
    onError: (error) => setNotice({ kind: "error", text: errorText(error) }),
  });

  const handlers: ActionHandlers = {
    onApprove: (action) => decide.mutate({ action, op: "approve" }),
    onReopen: (action) => decide.mutate({ action, op: "reopen" }),
    onRestore: (action) => decide.mutate({ action, op: "restore" }),
    onReject: (action, reason) => reject.mutate({ action, reason }),
    onSave: async (action, value) => {
      await client.editAction(siteId, action.id, { proposed_value: value });
      refresh();
    },
  };

  async function downloadCsv() {
    try {
      const blob = await client.actionsCsv(siteId);
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `${site?.domain ?? "site"}-action-plan.csv`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
    } catch (error) {
      setNotice({ kind: "error", text: `Download failed: ${errorText(error)}` });
    }
  }

  const rows = plan.data ?? [];
  const visible = rows.slice(0, shown);
  const typeOptions = useMemo(
    () => Object.keys(summary.data?.by_type ?? {}).filter((t) => !(manual.data ?? []).some((m) => m.type === t)).sort(),
    [summary.data, manual.data],
  );
  const typeLabels = useMemo(() => {
    const labels: Record<string, string> = {};
    for (const a of rows) labels[a.type] = a.type_label;
    return labels;
  }, [rows]);
  const selectable = visible.filter((a) => OPEN_BOARD.includes(a.status));
  const allSelected = selectable.length > 0 && selectable.every((a) => selected.has(a.id));
  const busy = decide.isPending || reject.isPending || bulk.isPending || !canWrite;
  const openManual = (manual.data ?? []).filter((a) => a.status === "proposed").length;
  const data = summary.data;
  const lastResult = data?.last_plan?.result as { created?: number; updated?: number; obsolete?: number } | null | undefined;

  if (sites.isSuccess && !site) {
    return (
      <>
        <PageHeader title="Actions" />
        <p role="alert" className="text-sm text-destructive">
          That site was not found in this organisation.
        </p>
        <Button asChild variant="outline" className="mt-3">
          <Link to="/actions">Back to sites</Link>
        </Button>
      </>
    );
  }

  return (
    <>
      <PageHeader
        title={site ? `Actions: ${site.display_name}` : "Actions"}
        description={
          site
            ? `${siteLabel(site)}. Every suggested change, most valuable first. Nothing changes on your website until you deploy it.`
            : undefined
        }
        actions={
          <div className="flex flex-wrap gap-2">
            <Button asChild variant="ghost" size="sm">
              <Link to="/actions">
                <ArrowLeft /> All sites
              </Link>
            </Button>
            <Button variant="outline" size="sm" onClick={downloadCsv}>
              <Download /> Export CSV
            </Button>
            <Button variant="outline" size="sm" onClick={() => setHandoffOpen(true)}>
              <FileText /> Developer handoff
            </Button>
            <Button
              size="sm"
              onClick={() => replan.mutate()}
              disabled={!canWrite || replan.isPending || data?.plan_running}
            >
              <RefreshCw className={data?.plan_running ? "animate-spin" : undefined} />
              {data?.plan_running ? "Planning…" : "Re-plan"}
            </Button>
          </div>
        }
      />

      {summary.isPending && <Skeleton className="h-32 w-full" aria-label="Loading the plan" />}
      {summary.isError && (
        <p role="alert" className="text-sm text-destructive">
          Could not load the plan: {summary.error.message}
        </p>
      )}

      {notice && (
        <p
          role={notice.kind === "error" ? "alert" : "status"}
          className={`mb-4 text-sm ${notice.kind === "error" ? "text-destructive" : "text-success"}`}
        >
          {notice.text}
        </p>
      )}
      {!canWrite && <p className="mb-4 text-sm text-muted-foreground">You have view-only access to this organisation.</p>}

      {data && (
        <div className="space-y-4">
          <NeedsAiBanner summary={data} />
          <SummaryCards summary={data} />
          <AutopilotCard
            enabled={data.autopilot_enabled}
            canChange={canAdmin}
            busy={autopilot.isPending || saveCap.isPending}
            onChange={(next) => autopilot.mutate(next)}
            settings={autopilotSettings.data}
            onSaveCap={(cap) => saveCap.mutate(cap)}
          />
          <DeployPanel siteId={siteId} approvedCount={approvedCount.data ?? 0} canDeploy={canAdmin} onChanged={refresh} />
          <p className="text-xs text-muted-foreground">
            {data.last_plan
              ? `Last planned ${new Date(data.last_plan.finished_at ?? data.last_plan.created_at).toLocaleString()}${
                  lastResult ? `: ${lastResult.created ?? 0} new, ${lastResult.updated ?? 0} updated, ${lastResult.obsolete ?? 0} closed.` : "."
                }`
              : "The plan updates itself after every audit and every Search Console sync. You can also press Re-plan."}
          </p>

          {data.total === 0 && !data.plan_running ? (
            <Card className="items-center py-14 text-center">
              <CardContent className="flex max-w-md flex-col items-center gap-3">
                <Zap className="size-8 text-muted-foreground" />
                <h2 className="text-lg font-semibold">No plan yet</h2>
                <p className="text-sm text-muted-foreground">
                  The plan is built from an audit and from Search Console data. Run an audit first, then it appears here.
                </p>
                <Button asChild>
                  <Link to="/audit/$siteId" params={{ siteId }}>
                    Go to Audit
                  </Link>
                </Button>
              </CardContent>
            </Card>
          ) : (
            <>
              <div role="tablist" aria-label="Action plan sections" className="flex gap-1 border-b">
                {(
                  [
                    ["plan", "Plan"],
                    ["manual", `Manual tasks${openManual ? ` (${openManual})` : ""}`],
                  ] as [Tab, string][]
                ).map(([key, label]) => (
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

              {tab === "plan" && (
                <section aria-label="Plan" className="space-y-3">
                  <div className="flex flex-wrap items-center gap-3">
                    <div role="group" aria-label="Risk class" className="flex gap-1">
                      {(
                        [
                          ["all", "All"],
                          ["auto", "Low risk"],
                          ["approve", "Needs approval"],
                        ] as [RiskClass | "all", string][]
                      ).map(([value, label]) => (
                        <Button
                          key={value}
                          size="sm"
                          variant={risk === value ? "default" : "outline"}
                          aria-pressed={risk === value}
                          onClick={() => {
                            setRisk(value);
                            setShown(PAGE_SIZE);
                          }}
                        >
                          {label}
                        </Button>
                      ))}
                    </div>
                    <label className="flex items-center gap-2 text-sm">
                      <span className="text-muted-foreground">Type</span>
                      <select
                        value={type}
                        onChange={(e) => {
                          setType(e.target.value);
                          setShown(PAGE_SIZE);
                        }}
                        className="h-8 rounded-md border border-input bg-card px-2 text-sm"
                      >
                        <option value="all">All types</option>
                        {typeOptions.map((t) => (
                          <option key={t} value={t}>
                            {typeLabels[t] ?? t.replaceAll("_", " ")} ({data.by_type[t]})
                          </option>
                        ))}
                      </select>
                    </label>
                    <label className="flex items-center gap-2 text-sm">
                      <span className="text-muted-foreground">Status</span>
                      <select
                        value={statusFilter}
                        onChange={(e) => {
                          setStatusFilter(e.target.value as StatusFilter);
                          setShown(PAGE_SIZE);
                          setSelected(new Set());
                        }}
                        className="h-8 rounded-md border border-input bg-card px-2 text-sm"
                      >
                        {STATUS_FILTERS.map((f) => (
                          <option key={f.value} value={f.value}>
                            {f.label}
                          </option>
                        ))}
                      </select>
                    </label>
                  </div>

                  <div className="flex flex-wrap items-center gap-2 rounded-md border bg-card px-3 py-2">
                    <label className="flex items-center gap-2 text-sm">
                      <input
                        type="checkbox"
                        checked={allSelected}
                        disabled={selectable.length === 0}
                        onChange={(e) => setSelected(e.target.checked ? new Set(selectable.map((a) => a.id)) : new Set())}
                        className="size-4 accent-primary"
                      />
                      Select all shown
                    </label>
                    <span className="text-sm text-muted-foreground" aria-live="polite">
                      {selected.size} selected
                    </span>
                    <div className="ml-auto flex flex-wrap items-center gap-2">
                      {bulkRejecting ? (
                        <form
                          className="flex items-center gap-2"
                          onSubmit={(e) => {
                            e.preventDefault();
                            if (bulkReason.trim()) bulk.mutate({ op: "reject", reason: bulkReason.trim() });
                          }}
                        >
                          <label htmlFor="bulk-reason" className="sr-only">
                            Why are you rejecting these?
                          </label>
                          <Input
                            id="bulk-reason"
                            value={bulkReason}
                            onChange={(e) => setBulkReason(e.target.value)}
                            placeholder="Reason for all selected"
                            className="h-8 w-56"
                            autoFocus
                          />
                          <Button size="sm" variant="destructive" type="submit" disabled={!bulkReason.trim() || bulk.isPending}>
                            Reject selected
                          </Button>
                          <Button size="sm" variant="ghost" type="button" onClick={() => setBulkRejecting(false)}>
                            Cancel
                          </Button>
                        </form>
                      ) : (
                        <>
                          <Button size="sm" disabled={busy || selected.size === 0} onClick={() => bulk.mutate({ op: "approve" })}>
                            Approve selected
                          </Button>
                          <Button
                            size="sm"
                            variant="outline"
                            disabled={busy || selected.size === 0}
                            onClick={() => setBulkRejecting(true)}
                          >
                            Reject selected…
                          </Button>
                        </>
                      )}
                    </div>
                  </div>

                  {plan.isPending && <Skeleton className="h-40 w-full" aria-label="Loading actions" />}
                  {plan.isError && (
                    <p role="alert" className="text-sm text-destructive">
                      Could not load actions: {plan.error.message}
                    </p>
                  )}
                  {plan.data && rows.length === 0 && (
                    <Card className="py-8 text-center text-sm text-muted-foreground">
                      <CardContent>Nothing matches these filters.</CardContent>
                    </Card>
                  )}
                  {rows.length > 0 && (
                    <Card className="py-0">
                      <ul className="divide-y" aria-label="Actions, most valuable first">
                        {visible.map((action) => (
                          <ActionCard
                            key={action.id}
                            action={action}
                            selected={selected.has(action.id)}
                            onSelect={(checked) =>
                              setSelected((previous) => {
                                const next = new Set(previous);
                                if (checked) next.add(action.id);
                                else next.delete(action.id);
                                return next;
                              })
                            }
                            busy={busy}
                            handlers={handlers}
                          />
                        ))}
                      </ul>
                    </Card>
                  )}
                  {rows.length > shown && (
                    <Button variant="outline" onClick={() => setShown((n) => n + PAGE_SIZE)}>
                      Show {Math.min(PAGE_SIZE, rows.length - shown)} more ({rows.length - shown} left)
                    </Button>
                  )}
                </section>
              )}

              {tab === "manual" && (
                <section aria-label="Manual tasks" className="space-y-3">
                  <p className="text-sm text-muted-foreground">
                    Work that only a person can do: earning links, showing who writes your content, collecting real reviews,
                    and technical jobs for a developer. Search OS never does these for you. Tick the steps as you go.
                  </p>
                  {manual.isPending && <Skeleton className="h-32 w-full" aria-label="Loading tasks" />}
                  {manual.data && manual.data.length === 0 && (
                    <Card className="py-8 text-center text-sm text-muted-foreground">
                      <CardContent>No manual tasks yet.</CardContent>
                    </Card>
                  )}
                  {manual.data && manual.data.length > 0 && (
                    <Card className="py-0">
                      <ul className="divide-y" aria-label="Manual tasks">
                        {manual.data.map((action) => (
                          <ManualTaskCard
                            key={action.id}
                            action={action}
                            busy={busy || tick.isPending}
                            onTick={(a, done) => tick.mutate({ action: a, done })}
                            onDone={(a) => decide.mutate({ action: a, op: "done" })}
                            onReopen={(a) => decide.mutate({ action: a, op: "reopen" })}
                            onDismiss={(a) => reject.mutate({ action: a, reason: NO_REASON })}
                          />
                        ))}
                      </ul>
                    </Card>
                  )}
                </section>
              )}
            </>
          )}
        </div>
      )}
      <HandoffDialog siteId={siteId} open={handoffOpen} onOpenChange={setHandoffOpen} />
    </>
  );
}
