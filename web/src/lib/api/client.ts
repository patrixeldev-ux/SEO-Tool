import type {
  ActionFilters,
  ActionSummary,
  AiReport,
  AiReportImport,
  AiReportSource,
  AiSettings,
  Audit,
  AuditChanges,
  AuditIssues,
  AuditPages,
  Connection,
  CtrCurve,
  BulkOp,
  BulkResult,
  GoogleProperties,
  Handoff,
  IntegrationStatus,
  MeResponse,
  Opportunities,
  Org,
  PageDetail,
  PlanAction,
  RegisterInput,
  SearchCoverage,
  SearchPageDetail,
  SearchRange,
  SearchSegment,
  SearchTable,
  SearchTotals,
  Site,
  SiteIntegrations,
  SyncJob,
  TokenResponse,
} from "./types";

export class ApiError extends Error {
  readonly status: number;
  readonly detail: unknown;

  constructor(status: number, message: string, detail?: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

export interface ApiClientOptions {
  baseUrl: string;
  /** Injectable for tests. Defaults to the global fetch. */
  fetch?: typeof fetch;
  /** Called once when the session cannot be recovered (refresh rejected). The UI should show the login page. */
  onAuthLost?: () => void;
}

interface RequestOptions {
  method?: string;
  body?: unknown;
  /** A multipart upload (CSV files). Sent without a JSON content type so the browser adds the boundary. */
  form?: FormData;
  /** false = do not send the bearer token and never try a silent refresh (login, register, refresh itself). */
  auth?: boolean;
  /** "blob" for file downloads (CSV export). */
  as?: "json" | "blob";
}

const REFRESH_LOCK = "searchos-refresh";

/**
 * Thin fetch wrapper that
 *  - keeps the short-lived access token in memory only (never localStorage),
 *  - relies on the httpOnly refresh cookie for session continuity,
 *  - on a 401 refreshes ONCE (single-flight across concurrent requests) and replays the request,
 *  - sends the active organisation in the `X-Org-Id` header.
 */
export class ApiClient {
  private accessToken: string | null = null;
  private orgId: string | null = null;
  private refreshInFlight: Promise<TokenResponse> | null = null;
  private readonly baseUrl: string;
  private readonly fetchImpl: typeof fetch;
  onAuthLost: (() => void) | undefined;

  constructor(options: ApiClientOptions) {
    this.baseUrl = options.baseUrl.replace(/\/+$/, "");
    this.fetchImpl = options.fetch ?? ((...args) => fetch(...args));
    this.onAuthLost = options.onAuthLost;
  }

  setAccessToken(token: string | null): void {
    this.accessToken = token;
  }

  getAccessToken(): string | null {
    return this.accessToken;
  }

  setOrgId(orgId: string | null): void {
    this.orgId = orgId;
  }

  // ---- generic request with silent refresh ------------------------------------------------------
  async request<T>(path: string, options: RequestOptions = {}): Promise<T> {
    const useAuth = options.auth !== false;
    let response = await this.send(path, options, useAuth);

    if (response.status === 401 && useAuth) {
      // Throws if the session is gone (ApiError 401, after reporting it) or the network failed (session kept).
      await this.refresh();
      response = await this.send(path, options, useAuth); // replay exactly once
      if (response.status === 401) {
        this.accessToken = null;
        this.onAuthLost?.();
      }
    }
    return this.parse<T>(response, options.as);
  }

  private send(path: string, options: RequestOptions, useAuth: boolean): Promise<Response> {
    const headers: Record<string, string> = { Accept: "application/json" };
    if (options.body !== undefined && !options.form) headers["Content-Type"] = "application/json";
    if (useAuth && this.accessToken) headers.Authorization = `Bearer ${this.accessToken}`;
    if (useAuth && this.orgId) headers["X-Org-Id"] = this.orgId;
    return this.fetchImpl(`${this.baseUrl}${path}`, {
      method: options.method ?? "GET",
      headers,
      body: options.form ?? (options.body === undefined ? undefined : JSON.stringify(options.body)),
      credentials: "include", // the refresh cookie is httpOnly and scoped to /auth
    });
  }

  private async parse<T>(response: Response, as: "json" | "blob" = "json"): Promise<T> {
    if (response.ok) {
      if (response.status === 204) return undefined as T;
      if (as === "blob") return (await response.blob()) as T;
      return (await response.json()) as T;
    }
    let detail: unknown;
    try {
      detail = (await response.json())?.detail;
    } catch {
      detail = undefined;
    }
    throw new ApiError(response.status, describeError(response.status, detail), detail);
  }

  // ---- refresh (single flight) ------------------------------------------------------------------
  /**
   * Exchange the refresh cookie for a new access token. Concurrent callers share one network call: refresh tokens
   * are single-use, so a second parallel call would look like token theft to the server and end the session.
   * Across browser tabs the same guarantee comes from the Web Locks API where available.
   */
  refresh(): Promise<TokenResponse> {
    this.refreshInFlight ??= this.refreshLocked().finally(() => {
      this.refreshInFlight = null;
    });
    return this.refreshInFlight;
  }

  private refreshLocked(): Promise<TokenResponse> {
    const locks = typeof navigator !== "undefined" ? navigator.locks : undefined;
    if (!locks) return this.doRefresh();
    return locks.request(REFRESH_LOCK, () => this.doRefresh());
  }

  private async doRefresh(): Promise<TokenResponse> {
    let response: Response;
    try {
      response = await this.send("/auth/refresh", { method: "POST" }, false);
    } catch (error) {
      // Network failure: the session may still be fine, so do NOT treat it as logged out.
      throw error instanceof Error ? error : new Error("Network error");
    }
    if (!response.ok) {
      this.accessToken = null;
      this.onAuthLost?.();
      throw new ApiError(response.status, "Session expired");
    }
    const tokens = (await response.json()) as TokenResponse;
    this.accessToken = tokens.access_token;
    return tokens;
  }

  // ---- endpoints ---------------------------------------------------------------------------------
  async login(email: string, password: string): Promise<TokenResponse> {
    const tokens = await this.request<TokenResponse>("/auth/login", {
      method: "POST",
      body: { email, password },
      auth: false,
    });
    this.accessToken = tokens.access_token;
    return tokens;
  }

  async register(input: RegisterInput): Promise<TokenResponse> {
    const tokens = await this.request<TokenResponse>("/auth/register", {
      method: "POST",
      body: input,
      auth: false,
    });
    this.accessToken = tokens.access_token;
    return tokens;
  }

  async logout(): Promise<void> {
    try {
      await this.request<void>("/auth/logout", { method: "POST", auth: false });
    } finally {
      this.accessToken = null;
    }
  }

  me(): Promise<MeResponse> {
    return this.request<MeResponse>("/auth/me");
  }

  listSites(): Promise<Site[]> {
    return this.request<Site[]>("/sites");
  }

  createSite(input: { domain: string; display_name?: string }): Promise<Site> {
    return this.request<Site>("/sites", { method: "POST", body: input });
  }

  // ---- audits ----
  listAudits(siteId: string): Promise<Audit[]> {
    return this.request<Audit[]>(`/sites/${siteId}/audits`);
  }

  startAudit(siteId: string, pageLimit?: number): Promise<Audit> {
    return this.request<Audit>(`/sites/${siteId}/audits`, {
      method: "POST",
      body: pageLimit === undefined ? {} : { page_limit: pageLimit },
    });
  }

  getAudit(auditId: string): Promise<Audit> {
    return this.request<Audit>(`/audits/${auditId}`);
  }

  cancelAudit(auditId: string): Promise<Audit> {
    return this.request<Audit>(`/audits/${auditId}/cancel`, { method: "POST" });
  }

  auditIssues(auditId: string): Promise<AuditIssues> {
    return this.request<AuditIssues>(`/audits/${auditId}/issues`);
  }

  auditChanges(auditId: string): Promise<AuditChanges> {
    return this.request<AuditChanges>(`/audits/${auditId}/changes`);
  }

  auditPages(
    auditId: string,
    params: { q?: string; withIssues?: boolean; limit?: number; offset?: number } = {},
  ): Promise<AuditPages> {
    const query = new URLSearchParams();
    if (params.q) query.set("q", params.q);
    if (params.withIssues) query.set("with_issues", "true");
    query.set("limit", String(params.limit ?? 50));
    query.set("offset", String(params.offset ?? 0));
    return this.request<AuditPages>(`/audits/${auditId}/pages?${query.toString()}`);
  }

  auditPage(auditId: string, pageId: string): Promise<PageDetail> {
    return this.request<PageDetail>(`/audits/${auditId}/pages/${pageId}`);
  }

  /** The CSV needs the bearer token, so it is fetched here and saved from a blob instead of a plain link. */
  auditCsv(auditId: string): Promise<Blob> {
    return this.request<Blob>(`/audits/${auditId}/export.csv`, { as: "blob" });
  }

  // ---- integrations ----
  integrationStatus(): Promise<IntegrationStatus> {
    return this.request<IntegrationStatus>("/integrations/status", { auth: false });
  }

  siteIntegrations(siteId: string): Promise<SiteIntegrations> {
    return this.request<SiteIntegrations>(`/sites/${siteId}/integrations`);
  }

  /** Returns the Google consent-screen URL; the browser then navigates there. */
  googleConnect(siteId: string): Promise<{ authorization_url: string }> {
    return this.request(`/sites/${siteId}/integrations/google/connect`, { method: "POST" });
  }

  googleProperties(siteId: string): Promise<GoogleProperties> {
    return this.request<GoogleProperties>(`/sites/${siteId}/integrations/google/properties`);
  }

  selectGoogleProperty(siteId: string, siteUrl: string): Promise<Connection> {
    return this.request<Connection>(`/sites/${siteId}/integrations/google/property`, {
      method: "PUT",
      body: { site_url: siteUrl },
    });
  }

  googleSync(siteId: string, mode: "backfill" | "incremental" = "incremental"): Promise<SyncJob> {
    return this.request<SyncJob>(`/sites/${siteId}/integrations/google/sync`, { method: "POST", body: { mode } });
  }

  googleDisconnect(siteId: string): Promise<void> {
    return this.request<void>(`/sites/${siteId}/integrations/google`, { method: "DELETE" });
  }

  saveBingKey(siteId: string, apiKey: string, siteUrl?: string): Promise<Connection> {
    return this.request<Connection>(`/sites/${siteId}/integrations/bing`, {
      method: "PUT",
      body: siteUrl ? { api_key: apiKey, site_url: siteUrl } : { api_key: apiKey },
    });
  }

  bingDisconnect(siteId: string): Promise<void> {
    return this.request<void>(`/sites/${siteId}/integrations/bing`, { method: "DELETE" });
  }

  // ---- search data ----
  searchCoverage(siteId: string): Promise<SearchCoverage> {
    return this.request<SearchCoverage>(`/sites/${siteId}/search/coverage`);
  }

  searchTotals(siteId: string, range: SearchRange): Promise<SearchTotals> {
    return this.request<SearchTotals>(`/sites/${siteId}/search/totals?range=${range}`);
  }

  searchTable(
    siteId: string,
    kind: "queries" | "pages",
    params: { range: SearchRange; segment: SearchSegment; limit?: number; offset?: number },
  ): Promise<SearchTable> {
    const query = new URLSearchParams({
      range: params.range,
      segment: params.segment,
      limit: String(params.limit ?? 25),
      offset: String(params.offset ?? 0),
    });
    return this.request<SearchTable>(`/sites/${siteId}/search/${kind}?${query.toString()}`);
  }

  opportunities(
    siteId: string,
    params: { type?: string; limit?: number; offset?: number } = {},
  ): Promise<Opportunities> {
    const query = new URLSearchParams({ limit: String(params.limit ?? 50), offset: String(params.offset ?? 0) });
    if (params.type) query.set("type", params.type);
    return this.request<Opportunities>(`/sites/${siteId}/search/opportunities?${query.toString()}`);
  }

  searchPage(siteId: string, url: string, range: SearchRange, segment: SearchSegment): Promise<SearchPageDetail> {
    const query = new URLSearchParams({ url, range, segment });
    return this.request<SearchPageDetail>(`/sites/${siteId}/search/page?${query.toString()}`);
  }

  ctrCurve(siteId: string): Promise<CtrCurve> {
    return this.request<CtrCurve>(`/sites/${siteId}/search/curve`);
  }

  // ---- AI reports ----
  aiReport(siteId: string, source: AiReportSource): Promise<AiReport> {
    return this.request<AiReport>(`/sites/${siteId}/ai-reports/${source}`);
  }

  uploadAiReport(siteId: string, source: AiReportSource, file: File): Promise<AiReportImport> {
    const form = new FormData();
    form.append("file", file);
    return this.request<AiReportImport>(`/sites/${siteId}/ai-reports/${source}/import`, {
      method: "POST",
      form,
    });
  }

  deleteAiReportImport(siteId: string, importId: string): Promise<void> {
    return this.request<void>(`/sites/${siteId}/ai-reports/imports/${importId}`, { method: "DELETE" });
  }

  // ---- sites (settings) ----
  updateSite(siteId: string, changes: { display_name?: string; autopilot_enabled?: boolean }): Promise<Site> {
    return this.request<Site>(`/sites/${siteId}`, { method: "PATCH", body: changes });
  }

  // ---- the action plan ----
  listActions(siteId: string, filters: ActionFilters = {}): Promise<PlanAction[]> {
    const query = new URLSearchParams({ limit: "2000" });
    for (const s of filters.status ?? []) query.append("status", s);
    for (const r of filters.risk_class ?? []) query.append("risk_class", r);
    for (const t of filters.type ?? []) query.append("type", t);
    if (filters.manual !== undefined) query.set("manual", String(filters.manual));
    return this.request<PlanAction[]>(`/sites/${siteId}/actions?${query.toString()}`);
  }

  actionSummary(siteId: string): Promise<ActionSummary> {
    return this.request<ActionSummary>(`/sites/${siteId}/actions/summary`);
  }

  replan(siteId: string): Promise<SyncJob> {
    return this.request<SyncJob>(`/sites/${siteId}/actions/replan`, { method: "POST" });
  }

  decideAction(siteId: string, actionId: string, op: "approve" | "reopen" | "done" | "restore"): Promise<PlanAction> {
    return this.request<PlanAction>(`/sites/${siteId}/actions/${actionId}/${op}`, { method: "POST" });
  }

  rejectAction(siteId: string, actionId: string, reason: string): Promise<PlanAction> {
    return this.request<PlanAction>(`/sites/${siteId}/actions/${actionId}/reject`, {
      method: "POST",
      body: { reason },
    });
  }

  editAction(
    siteId: string,
    actionId: string,
    changes: { proposed_value?: unknown; checklist_done?: string[] },
  ): Promise<PlanAction> {
    return this.request<PlanAction>(`/sites/${siteId}/actions/${actionId}`, { method: "PATCH", body: changes });
  }

  bulkActions(siteId: string, ids: string[], op: BulkOp, reason?: string): Promise<BulkResult> {
    return this.request<BulkResult>(`/sites/${siteId}/actions/bulk`, {
      method: "POST",
      body: reason === undefined ? { ids, op } : { ids, op, reason },
    });
  }

  actionsCsv(siteId: string): Promise<Blob> {
    return this.request<Blob>(`/sites/${siteId}/actions/export.csv`, { as: "blob" });
  }

  handoff(siteId: string, includeProposed: boolean): Promise<Handoff> {
    return this.request<Handoff>(`/sites/${siteId}/actions/handoff?include_proposed=${includeProposed}`);
  }

  // ---- AI settings ----
  aiSettings(): Promise<AiSettings> {
    return this.request<AiSettings>("/ai/status");
  }

  setAiBudget(monthlyTokenBudget: number | null): Promise<AiSettings> {
    return this.request<AiSettings>("/ai/budget", { method: "PUT", body: { monthly_token_budget: monthlyTokenBudget } });
  }

  createOrg(name: string): Promise<Org> {
    return this.request<Org>("/orgs", { method: "POST", body: { name } });
  }
}

function describeError(status: number, detail: unknown): string {
  if (typeof detail === "string") return detail;
  if (detail && typeof detail === "object" && !Array.isArray(detail)) {
    const message = (detail as { message?: unknown }).message;
    if (typeof message === "string") return message;
  }
  if (Array.isArray(detail) && detail.length > 0) {
    // FastAPI validation errors: [{ loc: [...], msg: "..." }]
    const first = detail[0] as { msg?: string; loc?: unknown[] };
    const field = Array.isArray(first.loc) ? first.loc[first.loc.length - 1] : undefined;
    const msg = first.msg ?? "Invalid input";
    return typeof field === "string" ? `${field}: ${msg}` : msg;
  }
  if (status >= 500) return "Something went wrong on our side. Please try again.";
  return `Request failed (${status})`;
}

const baseUrl = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

/** App-wide client instance. */
export const api = new ApiClient({ baseUrl });
