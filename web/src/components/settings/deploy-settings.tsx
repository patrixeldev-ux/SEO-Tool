import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Copy, ShieldCheck, TriangleAlert } from "lucide-react";
import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import type { DeployConnection, DeployMethod, GithubPathRule, Site } from "@/lib/api/types";
import { useAuth } from "@/lib/auth";
import { METHOD_HINTS, METHOD_LABELS } from "@/lib/deploy-utils";

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : "Something went wrong";
}

function CopyField({ label, value }: { label: string; value: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="space-y-1">
      <p className="text-xs font-medium text-muted-foreground">{label}</p>
      <div className="flex items-center gap-2">
        <code className="min-w-0 flex-1 rounded bg-muted px-2 py-1 text-xs break-all">{value}</code>
        <Button
          type="button"
          size="sm"
          variant="outline"
          aria-label={`Copy ${label}`}
          onClick={() => {
            void navigator.clipboard?.writeText(value);
            setCopied(true);
            setTimeout(() => setCopied(false), 1500);
          }}
        >
          {copied ? <Check /> : <Copy />}
        </Button>
      </div>
    </div>
  );
}

function Notice({ notice }: { notice: { kind: "ok" | "error"; text: string } | null }) {
  if (!notice) return null;
  return (
    <p role={notice.kind === "error" ? "alert" : "status"} className={`text-sm ${notice.kind === "error" ? "text-destructive" : "text-success"}`}>
      {notice.text}
    </p>
  );
}

function statusBadge(conn?: DeployConnection) {
  if (!conn) return <Badge variant="outline">Not set up</Badge>;
  if (conn.status === "connected") return <Badge variant="success">Connected</Badge>;
  return <Badge variant="warning">Needs attention</Badge>;
}

// ---- ownership ----------------------------------------------------------------------------------------------------
function OwnershipCard({ site }: { site: Site }) {
  const { client, activeOrg } = useAuth();
  const queryClient = useQueryClient();
  const key = ["deploy-ownership", activeOrg?.id, site.id];
  const query = useQuery({ queryKey: key, queryFn: () => client.deployOwnership(site.id) });
  const [notice, setNotice] = useState<{ kind: "ok" | "error"; text: string } | null>(null);
  const check = useMutation({
    mutationFn: () => client.checkDeployOwnership(site.id),
    onSuccess: (data) => {
      queryClient.setQueryData(key, data);
      void queryClient.invalidateQueries({ queryKey: ["deploy-connections", activeOrg?.id, site.id] });
      setNotice(
        data.verified
          ? { kind: "ok", text: "Verified. Search OS can now change this website." }
          : { kind: "error", text: data.error ?? "We could not find the proof yet. DNS changes can take a few minutes." },
      );
    },
    onError: (error) => setNotice({ kind: "error", text: errorText(error) }),
  });
  const own = query.data;
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <ShieldCheck className="size-4" /> 1. Prove you own this website
          {own?.verified && <Badge variant="success">Verified</Badge>}
        </CardTitle>
        <CardDescription>
          Search OS only changes websites you control. Do one of these two things, then press Check now. The proof is re-checked every 7 days.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {query.isPending && <Skeleton className="h-24 w-full" aria-label="Loading ownership" />}
        {query.isError && <p role="alert" className="text-sm text-destructive">Could not load this: {query.error.message}</p>}
        {own && (
          <>
            <div className="space-y-2">
              <p className="text-sm font-medium">Option A: add a DNS record</p>
              <p className="text-sm text-muted-foreground">At your domain provider, add a TXT record on {own.dns_host} with this value.</p>
              <CopyField label="TXT record value" value={own.dns_value} />
            </div>
            <div className="space-y-2">
              <p className="text-sm font-medium">Option B: upload a file</p>
              <p className="text-sm text-muted-foreground">
                Make this address return exactly the text below: <code className="text-xs">{own.file_url}</code>
              </p>
              <CopyField label="File text" value={own.file_content} />
            </div>
            <div className="flex flex-wrap items-center gap-3">
              <Button onClick={() => check.mutate()} disabled={check.isPending}>
                {check.isPending ? "Checking…" : "Check now"}
              </Button>
              {own.verified && own.verified_at && (
                <span className="text-sm text-muted-foreground">Verified {new Date(own.verified_at).toLocaleDateString()}.</span>
              )}
            </div>
            {!own.verified && own.error && !notice && <p className="text-sm text-muted-foreground">Last check: {own.error}</p>}
            <Notice notice={notice} />
          </>
        )}
      </CardContent>
    </Card>
  );
}

// ---- connections --------------------------------------------------------------------------------------------------
function parseRules(text: string): GithubPathRule[] {
  return text
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      const [url = "", file = ""] = line.split("->").map((part) => part.trim());
      return { url, file };
    });
}

function rulesText(rules: unknown): string {
  return Array.isArray(rules) ? (rules as GithubPathRule[]).map((r) => `${r.url} -> ${r.file}`).join("\n") : "";
}

function ConnectionShell({
  method,
  conn,
  siteId,
  children,
}: {
  method: DeployMethod;
  conn?: DeployConnection;
  siteId: string;
  children: React.ReactNode;
}) {
  const { client, activeOrg } = useAuth();
  const queryClient = useQueryClient();
  const [notice, setNotice] = useState<{ kind: "ok" | "error"; text: string } | null>(null);
  const done = () => void queryClient.invalidateQueries({ queryKey: ["deploy-connections", activeOrg?.id, siteId] });
  const test = useMutation({
    mutationFn: () => client.testDeployConnection(siteId, method),
    onSuccess: (r) => {
      setNotice({ kind: r.ok ? "ok" : "error", text: r.message });
      done();
    },
    onError: (error) => setNotice({ kind: "error", text: errorText(error) }),
  });
  const remove = useMutation({
    mutationFn: () => client.disconnectDeployer(siteId, method),
    onSuccess: () => {
      setNotice({ kind: "ok", text: `${METHOD_LABELS[method]} disconnected.` });
      done();
    },
    onError: (error) => setNotice({ kind: "error", text: errorText(error) }),
  });
  return (
    <Card aria-label={`${METHOD_LABELS[method]} connection`}>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          {METHOD_LABELS[method]} {statusBadge(conn)}
        </CardTitle>
        <CardDescription>{METHOD_HINTS[method]}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {conn?.last_error && <p className="flex items-start gap-2 text-sm text-destructive"><TriangleAlert className="mt-0.5 size-4 shrink-0" /> {conn.last_error}</p>}
        {children}
        {conn && (
          <div className="flex flex-wrap items-center gap-2 border-t pt-3">
            <Button size="sm" variant="outline" onClick={() => test.mutate()} disabled={test.isPending}>
              {test.isPending ? "Testing…" : "Test the connection"}
            </Button>
            <Button size="sm" variant="ghost" onClick={() => remove.mutate()} disabled={remove.isPending}>
              Disconnect
            </Button>
            {conn.last_tested_at && <span className="text-xs text-muted-foreground">Last tested {new Date(conn.last_tested_at).toLocaleString()}</span>}
          </div>
        )}
        <Notice notice={notice} />
      </CardContent>
    </Card>
  );
}

function CloudflareCard({ site, conn }: { site: Site; conn?: DeployConnection }) {
  const { client, activeOrg } = useAuth();
  const queryClient = useQueryClient();
  const [notice, setNotice] = useState<{ kind: "ok" | "error"; text: string } | null>(null);
  const setupKey = ["cloudflare-setup", activeOrg?.id, site.id];
  const setup = useQuery({ queryKey: setupKey, queryFn: () => client.cloudflareSetup(site.id), enabled: !!conn });
  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ["deploy-connections", activeOrg?.id, site.id] });
    void queryClient.invalidateQueries({ queryKey: setupKey });
  };
  const create = useMutation({
    mutationFn: (rotate: boolean) => client.connectCloudflare(site.id, rotate),
    onSuccess: (data, rotate) => {
      queryClient.setQueryData(setupKey, data);
      setNotice({ kind: "ok", text: rotate ? "New secret created. Give the Worker the new values." : "Created. Now put these values into your Worker (docs/DEPLOY_CLOUDFLARE.md)." });
      refresh();
    },
    onError: (error) => setNotice({ kind: "error", text: errorText(error) }),
  });
  const kill = useMutation({
    mutationFn: (off: boolean) => client.cloudflareKillSwitch(site.id, off),
    onSuccess: (_, off) => {
      setNotice({ kind: "ok", text: off ? "Switched off. Within about 5 minutes your pages pass through unchanged." : "Switched back on." });
      refresh();
    },
    onError: (error) => setNotice({ kind: "error", text: errorText(error) }),
  });
  const killed = conn?.meta.kill_switch === true;
  return (
    <ConnectionShell method="cloudflare" conn={conn} siteId={site.id}>
      {!conn ? (
        <Button onClick={() => create.mutate(false)} disabled={create.isPending}>
          {create.isPending ? "Creating…" : "Set up Cloudflare"}
        </Button>
      ) : (
        <div className="space-y-3">
          <p className="text-sm text-muted-foreground">
            Add the Worker to your Cloudflare site with these values. Search OS never gets a Cloudflare password.
          </p>
          {setup.data && (
            <div className="space-y-2">
              <CopyField label="Rules address (RULES_URL)" value={setup.data.rules_url} />
              <CopyField label="Signing secret (SIGNING_SECRET)" value={setup.data.signing_secret} />
              <CopyField label="Route" value={setup.data.route} />
            </div>
          )}
          <div className="flex flex-wrap items-center gap-2">
            <Button size="sm" variant="outline" onClick={() => create.mutate(true)} disabled={create.isPending}>
              Make a new secret
            </Button>
            <Button size="sm" variant={killed ? "default" : "destructive"} onClick={() => kill.mutate(!killed)} disabled={kill.isPending}>
              {killed ? "Switch changes back on" : "Switch all changes off"}
            </Button>
            {killed && <Badge variant="warning">Changes are switched off</Badge>}
          </div>
        </div>
      )}
      <Notice notice={notice} />
    </ConnectionShell>
  );
}

function WordPressCard({ site, conn }: { site: Site; conn?: DeployConnection }) {
  const { client, activeOrg } = useAuth();
  const queryClient = useQueryClient();
  const [url, setUrl] = useState(String(conn?.meta.site_url ?? `https://${site.domain}`));
  const [user, setUser] = useState(String(conn?.meta.username ?? ""));
  const [password, setPassword] = useState("");
  const [notice, setNotice] = useState<{ kind: "ok" | "error"; text: string } | null>(null);
  const save = useMutation({
    mutationFn: () => client.connectWordpress(site.id, { site_url: url.trim(), username: user.trim(), application_password: password }),
    onSuccess: (r) => {
      setNotice({ kind: r.ok ? "ok" : "error", text: r.message });
      setPassword("");
      void queryClient.invalidateQueries({ queryKey: ["deploy-connections", activeOrg?.id, site.id] });
    },
    onError: (error) => setNotice({ kind: "error", text: errorText(error) }),
  });
  const ready = url.trim() && user.trim() && password.replace(/\s/g, "").length >= 8;
  return (
    <ConnectionShell method="wordpress" conn={conn} siteId={site.id}>
      <p className="text-sm text-muted-foreground">
        Install the Search OS Connector plugin, then in WordPress open Users, Profile, Application Passwords and create one. Paste it here.
      </p>
      <form
        className="grid gap-3 sm:grid-cols-2"
        onSubmit={(e) => {
          e.preventDefault();
          if (ready) save.mutate();
        }}
      >
        <label className="space-y-1 text-sm sm:col-span-2">
          <span className="text-muted-foreground">WordPress address</span>
          <Input value={url} onChange={(e) => setUrl(e.target.value)} />
        </label>
        <label className="space-y-1 text-sm">
          <span className="text-muted-foreground">WordPress username</span>
          <Input value={user} onChange={(e) => setUser(e.target.value)} autoComplete="off" />
        </label>
        <label className="space-y-1 text-sm">
          <span className="text-muted-foreground">Application password</span>
          <Input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="off" placeholder={conn ? "Enter again to replace" : ""} />
        </label>
        <div className="sm:col-span-2">
          <Button type="submit" disabled={!ready || save.isPending}>
            {save.isPending ? "Connecting…" : conn ? "Save and test" : "Connect WordPress"}
          </Button>
        </div>
      </form>
      <Notice notice={notice} />
    </ConnectionShell>
  );
}

function GithubCard({ site, conn }: { site: Site; conn?: DeployConnection }) {
  const { client, activeOrg } = useAuth();
  const queryClient = useQueryClient();
  const [repo, setRepo] = useState(String(conn?.meta.repo ?? ""));
  const [token, setToken] = useState("");
  const [branch, setBranch] = useState(String(conn?.meta.base_branch ?? ""));
  const [root, setRoot] = useState(String(conn?.meta.root ?? ""));
  const [rules, setRules] = useState(rulesText(conn?.meta.path_rules));
  const [notice, setNotice] = useState<{ kind: "ok" | "error"; text: string } | null>(null);
  const refresh = () => void queryClient.invalidateQueries({ queryKey: ["deploy-connections", activeOrg?.id, site.id] });
  const connect = useMutation({
    mutationFn: () => client.connectGithub(site.id, { repo: repo.trim(), token: token.trim(), base_branch: branch.trim() || undefined, root: root.trim(), path_rules: parseRules(rules) }),
    onSuccess: (r) => {
      setNotice({ kind: r.ok ? "ok" : "error", text: r.message });
      setToken("");
      refresh();
    },
    onError: (error) => setNotice({ kind: "error", text: errorText(error) }),
  });
  const saveWhere = useMutation({
    mutationFn: () => client.githubSettings(site.id, { base_branch: branch.trim() || undefined, root: root.trim(), path_rules: parseRules(rules) }),
    onSuccess: () => {
      setNotice({ kind: "ok", text: "Saved." });
      refresh();
    },
    onError: (error) => setNotice({ kind: "error", text: errorText(error) }),
  });
  return (
    <ConnectionShell method="github" conn={conn} siteId={site.id}>
      <p className="text-sm text-muted-foreground">
        Create a fine-grained token for just this repository with Contents and Pull requests set to Read and write. Search OS never
        pushes to your main branch: it opens a pull request and you merge it.
      </p>
      <form
        className="grid gap-3 sm:grid-cols-2"
        onSubmit={(e) => {
          e.preventDefault();
          if (repo.trim() && token.trim().length >= 20) connect.mutate();
        }}
      >
        <label className="space-y-1 text-sm">
          <span className="text-muted-foreground">Repository (owner/name)</span>
          <Input value={repo} onChange={(e) => setRepo(e.target.value)} placeholder="acme/website" disabled={!!conn} />
        </label>
        <label className="space-y-1 text-sm">
          <span className="text-muted-foreground">Fine-grained token</span>
          <Input type="password" value={token} onChange={(e) => setToken(e.target.value)} autoComplete="off" placeholder={conn ? "Enter again to replace" : ""} />
        </label>
        <label className="space-y-1 text-sm">
          <span className="text-muted-foreground">Main branch (optional)</span>
          <Input value={branch} onChange={(e) => setBranch(e.target.value)} placeholder="main" />
        </label>
        <label className="space-y-1 text-sm">
          <span className="text-muted-foreground">Folder with the built HTML (optional)</span>
          <Input value={root} onChange={(e) => setRoot(e.target.value)} placeholder="public" />
        </label>
        <label className="space-y-1 text-sm sm:col-span-2">
          <span className="text-muted-foreground">Where pages live (optional, one per line: address pattern -&gt; file)</span>
          <textarea
            value={rules}
            onChange={(e) => setRules(e.target.value)}
            rows={3}
            placeholder="/blog/* -> content/{1}.html"
            className="w-full rounded-md border border-input bg-card px-3 py-2 text-sm"
          />
        </label>
        <div className="flex flex-wrap gap-2 sm:col-span-2">
          <Button type="submit" disabled={!repo.trim() || token.trim().length < 20 || connect.isPending}>
            {connect.isPending ? "Connecting…" : conn ? "Replace token and test" : "Connect GitHub"}
          </Button>
          {conn && (
            <Button type="button" variant="outline" onClick={() => saveWhere.mutate()} disabled={saveWhere.isPending}>
              Save where pages live
            </Button>
          )}
        </div>
      </form>
      <Notice notice={notice} />
    </ConnectionShell>
  );
}

// ---- the whole panel ----------------------------------------------------------------------------------------------
function SiteDeploy({ site }: { site: Site }) {
  const { client, activeOrg } = useAuth();
  const queryClient = useQueryClient();
  const query = useQuery({ queryKey: ["deploy-connections", activeOrg?.id, site.id], queryFn: () => client.deployConnections(site.id) });
  const [notice, setNotice] = useState<{ kind: "ok" | "error"; text: string } | null>(null);
  const choose = useMutation({
    mutationFn: (method: DeployMethod | null) => client.chooseDeployMethod(site.id, method),
    onSuccess: (data) => {
      queryClient.setQueryData(["deploy-connections", activeOrg?.id, site.id], data);
      setNotice(null);
    },
    onError: (error) => setNotice({ kind: "error", text: errorText(error) }),
  });
  const byMethod = new Map((query.data?.connections ?? []).map((c) => [c.method, c]));
  const current = query.data?.deploy_method ?? null;
  const options: { value: DeployMethod | null; label: string }[] = [
    { value: "cloudflare", label: "Cloudflare" },
    { value: "wordpress", label: "WordPress" },
    { value: "github", label: "GitHub" },
    { value: null, label: "None" },
  ];
  return (
    <div className="space-y-4">
      <OwnershipCard site={site} />
      <Card>
        <CardHeader>
          <CardTitle>2. How should changes reach your website?</CardTitle>
          <CardDescription>
            Pick one. Choosing None keeps everything as a hand-off for your developer: Search OS will not touch the site.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          {query.isPending && <Skeleton className="h-10 w-full" aria-label="Loading connections" />}
          {query.data && (
            <div role="radiogroup" aria-label="Deploy method" className="flex flex-wrap gap-2">
              {options.map((o) => (
                <Button
                  key={o.label}
                  role="radio"
                  aria-checked={current === o.value}
                  variant={current === o.value ? "default" : "outline"}
                  size="sm"
                  disabled={choose.isPending}
                  onClick={() => choose.mutate(o.value)}
                >
                  {o.label}
                </Button>
              ))}
            </div>
          )}
          <Notice notice={notice} />
        </CardContent>
      </Card>
      {query.data && (
        <>
          <CloudflareCard site={site} conn={byMethod.get("cloudflare")} />
          <WordPressCard site={site} conn={byMethod.get("wordpress")} />
          <GithubCard site={site} conn={byMethod.get("github")} />
        </>
      )}
    </div>
  );
}

export function DeploySettingsPanel() {
  const { client, activeOrg } = useAuth();
  const canAdmin = activeOrg?.role === "owner" || activeOrg?.role === "admin";
  const sites = useQuery({ queryKey: ["sites", activeOrg?.id], queryFn: () => client.listSites(), enabled: !!activeOrg });
  const [chosen, setChosen] = useState<string | null>(null);
  if (!canAdmin) return <p className="text-sm text-muted-foreground">Only owners and admins can set up deploying.</p>;
  if (sites.isPending) return <Skeleton className="h-32 w-full" aria-label="Loading sites" />;
  if (sites.isError) return <p role="alert" className="text-sm text-destructive">Could not load sites: {sites.error.message}</p>;
  if (sites.data.length === 0) return <p className="text-sm text-muted-foreground">Add a website first (Sites), then come back here.</p>;
  const site = sites.data.find((s) => s.id === chosen) ?? sites.data[0]!;
  return (
    <div className="space-y-4">
      {sites.data.length > 1 && (
        <label className="flex items-center gap-2 text-sm">
          <span className="text-muted-foreground">Website</span>
          <select value={site.id} onChange={(e) => setChosen(e.target.value)} className="h-8 rounded-md border border-input bg-card px-2 text-sm">
            {sites.data.map((s) => (
              <option key={s.id} value={s.id}>
                {s.display_name} ({s.domain})
              </option>
            ))}
          </select>
        </label>
      )}
      <SiteDeploy key={site.id} site={site} />
    </div>
  );
}
