export type Role = "owner" | "admin" | "member" | "client_viewer";

export interface User {
  id: string;
  email: string;
  full_name: string | null;
}

export interface Org {
  id: string;
  name: string;
  role: Role;
}

export interface TokenResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
  /** Only present for desktop clients; the web app relies on the httpOnly cookie. */
  refresh_token: string | null;
  user: User;
  orgs: Org[];
}

export interface MeResponse {
  user: User;
  orgs: Org[];
}

export interface Site {
  id: string;
  org_id: string;
  domain: string;
  uses_www: boolean;
  display_name: string;
  settings: Record<string, unknown>;
  autopilot_enabled: boolean;
  created_at: string;
}

export interface RegisterInput {
  email: string;
  password: string;
  full_name?: string;
  org_name: string;
}

// ---- audits -------------------------------------------------------------------------------------
export type Severity = "critical" | "high" | "medium" | "low" | "info";
export type Category = "indexability" | "on_page" | "structured_data" | "performance" | "ai_readiness";
export type AuditStatus = "queued" | "running" | "done" | "failed" | "cancelled";

export interface Audit {
  id: string;
  site_id: string;
  job_id: string | null;
  status: AuditStatus;
  progress: number;
  start_url: string;
  page_limit: number;
  pages_crawled: number;
  urls_discovered: number;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  error: string | null;
  warnings: string[];
  /** null = nothing could be scored. */
  score: number | null;
  subscores: Partial<Record<Category, number | null>>;
  issue_counts: {
    total?: number;
    by_severity?: Partial<Record<Severity, number>>;
    by_category?: Partial<Record<Category, number>>;
    by_check?: Record<string, number>;
  };
  stop_reason: string | null;
}

export interface IssueUrl {
  page_id: string | null;
  /** null = a site-wide issue */
  url: string | null;
  details: Record<string, unknown>;
}

export interface IssueGroup {
  check_id: string;
  title: string;
  severity: Severity;
  category: Category;
  why_it_matters: string;
  how_to_fix: string;
  count: number;
  items: IssueUrl[];
  truncated: boolean;
}

export interface PassedCheck {
  check_id: string;
  title: string;
  severity: Severity;
  category: Category;
  applicable: number;
}

export interface NotRunCheck {
  check_id: string;
  title: string;
  severity: Severity;
  category: Category;
  reason: string;
}

export interface AuditIssues {
  groups: IssueGroup[];
  passed: PassedCheck[];
  /** Checks that could not run (for example Core Web Vitals without PageSpeed data), each with the reason. */
  not_run?: NotRunCheck[];
}

/** Core Web Vitals from PageSpeed Insights; `*_source` is "field" (real visitors) or "lab" (one simulated load). */
export interface PsiResult {
  lcp_ms: number | null;
  lcp_source: "field" | "lab" | null;
  inp_ms: number | null;
  inp_source: "field" | null;
  cls: number | null;
  cls_source: "field" | "lab" | null;
  performance_score: number | null;
  error: string | null;
  fetched_at: string;
}

export interface PageRow {
  id: string;
  url: string;
  status_code: number | null;
  fetch_error: string | null;
  depth: number | null;
  title: string | null;
  word_count: number;
  inlinks_count: number;
  issue_count: number;
}

export interface AuditPages {
  total: number;
  items: PageRow[];
}

export interface LinkInfo {
  url: string;
  anchor: string;
  rel: string | null;
  page_id: string | null;
}

export interface PageIssue {
  check_id: string;
  title: string;
  severity: Severity;
  why_it_matters: string;
  how_to_fix: string;
  details: Record<string, unknown>;
}

export interface PageDetail {
  id: string;
  url: string;
  final_url: string | null;
  status_code: number | null;
  fetch_error: string | null;
  redirect_chain: { url: string; status: number; location?: string | null }[];
  depth: number | null;
  content_type: string | null;
  ttfb_ms: number | null;
  title: string | null;
  meta_description: string | null;
  h1: string[];
  canonical: string | null;
  robots_meta: string | null;
  x_robots_tag: string | null;
  lang: string | null;
  word_count: number;
  main_text_method: string | null;
  in_sitemap: boolean;
  inlinks_count: number;
  schema_types: string[];
  issues: PageIssue[];
  inlinks: LinkInfo[];
  outlinks: LinkInfo[];
  outlinks_total: number;
  psi?: PsiResult | null;
}

export interface ChangeItem {
  check_id: string;
  title: string;
  severity: Severity;
  url: string | null;
}

export interface AuditChanges {
  previous_audit_id: string | null;
  previous_score: number | null;
  score: number | null;
  score_delta: number | null;
  new: ChangeItem[];
  fixed: ChangeItem[];
  persisting_count: number;
  not_rechecked_count: number;
}

// ---- integrations ---------------------------------------------------------------------------------
export type ConnectionStatus = "pending" | "connected" | "needs_reauth" | "error";

export interface Connection {
  id: string;
  provider: "google" | "bing";
  status: ConnectionStatus;
  account_label: string | null;
  property_url: string | null;
  last_sync_at: string | null;
  last_error: string | null;
  meta: Record<string, unknown>;
}

export interface PsiStatus {
  enabled: boolean;
  key_configured: boolean;
}

export interface SiteIntegrations {
  google: Connection | null;
  bing: Connection | null;
  psi: PsiStatus;
}

export interface ProviderStatus {
  configured: boolean;
  message: string;
}

export interface IntegrationStatus {
  google: ProviderStatus;
  encryption: ProviderStatus;
  psi: PsiStatus;
  redirect_uri: string;
  setup_guide: string;
}

export interface GoogleProperty {
  site_url: string;
  permission_level: string;
  usable: boolean;
}

export interface GoogleProperties {
  properties: GoogleProperty[];
  suggested: string | null;
  selected: string | null;
}

export interface SyncJob {
  id: string;
  type: string;
  status: "queued" | "running" | "done" | "failed" | "cancelled";
  progress: number;
  error: string | null;
  payload: Record<string, unknown>;
  result: Record<string, unknown> | null;
}

export interface SearchCoverage {
  connected: boolean;
  property_url: string | null;
  first_day: string | null;
  last_day: string | null;
  days_stored: number;
  days_expected: number;
  active_job: SyncJob | null;
  last_job: SyncJob | null;
  last_sync_at: string | null;
  last_error: string | null;
}

// ---- search data ----------------------------------------------------------------------------------
export type SearchRange = "28d" | "3m" | "16m";
export type SearchSegment = "real" | "all" | "conversational";

export interface TotalsDay {
  date: string;
  clicks: number;
  impressions: number;
  ctr: number;
  position: number | null;
}

export interface TotalsSummary {
  clicks: number;
  impressions: number;
  ctr: number;
  position: number | null;
  days: number;
}

export interface SearchTotals {
  range: SearchRange;
  start: string | null;
  end: string | null;
  series: TotalsDay[];
  summary: TotalsSummary | null;
  previous: TotalsSummary | null;
  note: string;
}

export interface SearchTableRow {
  key: string;
  clicks: number;
  impressions: number;
  ctr: number;
  position: number;
  is_conversational: boolean;
}

export interface SearchTable {
  range: SearchRange;
  segment: SearchSegment;
  start: string | null;
  end: string | null;
  total: number;
  rows: SearchTableRow[];
  conversational_impressions: number;
  all_impressions: number;
  note: string;
}

export type OpportunityType = "STRIKING_DISTANCE" | "CTR_GAP" | "DECAY" | "CANNIBALISATION";

export interface Opportunity {
  id: string;
  type: OpportunityType;
  title: string;
  page: string | null;
  query: string | null;
  score: number;
  explanation: string;
  evidence: Record<string, unknown>;
}

export interface Opportunities {
  computed: boolean;
  computed_at: string | null;
  window_start: string | null;
  window_end: string | null;
  counts: Partial<Record<OpportunityType, number>>;
  skipped: Partial<Record<OpportunityType, string>>;
  curve_summary: "site" | "pooled" | "default" | null;
  total: number;
  items: Opportunity[];
}

export interface CtrCurve {
  positions: number[];
  values: number[];
  confidence: string[];
  impressions: number[];
  summary: string;
  computed_at: string | null;
  window_start: string | null;
  window_end: string | null;
}

export interface PageQueryRow {
  query: string;
  clicks: number;
  impressions: number;
  ctr: number;
  position: number;
  expected_ctr: number;
  vs_curve: number;
  is_conversational: boolean;
}

export interface SearchPageDetail {
  page: string;
  range: SearchRange;
  segment: SearchSegment;
  start: string | null;
  end: string | null;
  summary: TotalsSummary | null;
  expected_ctr: number | null;
  curve_confidence: string | null;
  series: { date: string; clicks: number; impressions: number; position: number | null }[];
  queries: PageQueryRow[];
  opportunities: Opportunity[];
  curve: CtrCurve;
}

// ---- AI reports (CSV imports) -----------------------------------------------------------------------
export type AiReportSource = "gsc_genai" | "bing_ai";

export interface AiReportImport {
  id: string;
  source: string;
  filename: string;
  shape: string;
  row_count: number;
  date_min: string | null;
  date_max: string | null;
  columns: string[];
  created_at: string;
  metrics?: string[];
  extra_metrics?: string[];
  skipped_rows?: number;
  warnings?: string[];
  replaced_rows?: number;
}

export interface AiReport {
  source: AiReportSource;
  imports: AiReportImport[];
  metrics: string[];
  primary_metric: string | null;
  series_shape: string | null;
  series: { date: string; values: Record<string, number> }[];
  top_pages: { key: string; values: Record<string, number> }[];
  top_queries: { key: string; values: Record<string, number> }[];
}

// ---- Actions (the plan) -----------------------------------------------------------------------------
export type ActionStatus =
  | "proposed"
  | "needs_ai"
  | "no_suggestion"
  | "approved"
  | "deployed"
  | "rejected"
  | "rolled_back"
  | "manual_done"
  | "stale"
  | "needs_manual"
  | "obsolete";
export type RiskClass = "auto" | "approve" | "manual";

export interface PlanAction {
  id: string;
  site_id: string;
  type: string;
  type_label: string;
  risk_class: RiskClass;
  target_url: string | null;
  field: string;
  current_value: unknown;
  proposed_value: unknown;
  original_value: unknown;
  edited_by_user: boolean;
  evidence: Record<string, unknown>;
  expected_gain: number;
  confidence: number;
  effort: number;
  priority: number;
  status: ActionStatus;
  created_by: string;
  source_kind: string;
  sources: string[];
  generation: Record<string, unknown>;
  checklist_done: string[];
  reject_reason: string | null;
  decided_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface AiStatus {
  configured: boolean;
  provider: string | null;
  model: string | null;
  selected_by: string | null;
  anthropic_key_present: boolean;
  gemini_key_present: boolean;
  gemini_model_set: boolean;
  problem: string | null;
}

export interface AiSettings extends AiStatus {
  monthly_token_budget: number;
  budget_is_custom: boolean;
  tokens_used_this_month: number;
  month_starts: string;
  usage_by_purpose: { purpose: string; calls: number; tokens: number }[];
}

export interface PlanRun {
  id: string;
  status: string;
  created_at: string;
  finished_at: string | null;
  trigger: string | null;
  result: Record<string, unknown> | null;
  error: string | null;
}

export interface ActionSummary {
  total: number;
  by_status: Partial<Record<ActionStatus, number>>;
  by_risk: Partial<Record<RiskClass, number>>;
  by_type: Record<string, number>;
  needs_ai: number;
  gain_measured: number;
  gain_prior: number;
  gain_approved: number;
  last_plan: PlanRun | null;
  plan_running: boolean;
  ai: AiStatus;
  autopilot_enabled: boolean;
}

export type BulkOp = "approve" | "reject" | "reopen" | "done";
export interface BulkResult {
  updated: PlanAction[];
  failed: { id: string; error: string }[];
}

export interface Handoff {
  text: string;
  count: number;
  generated_on: string;
  includes_unapproved: boolean;
}

export interface ActionFilters {
  status?: ActionStatus[];
  risk_class?: RiskClass[];
  type?: string[];
  manual?: boolean;
}

// ---- Phase 4: deploying ---------------------------------------------------------------------------------
export type DeployMethod = "cloudflare" | "wordpress" | "github";
export type DeploymentStatus = "pending" | "applied" | "pending_merge" | "verified" | "failed" | "reverting" | "reverted";
export type BatchStatus = "pending" | "applied" | "verified" | "partial" | "failed" | "reverted";

export interface Ownership {
  verified: boolean;
  verified_at: string | null;
  method: string | null;
  checked_at: string | null;
  error: string | null;
  token: string;
  dns_host: string;
  dns_value: string;
  file_path: string;
  file_url: string;
  file_content: string;
}

export interface Deployment {
  id: string;
  action_id: string;
  batch_id: string;
  method: DeployMethod;
  url: string;
  action_type: string;
  field: string;
  status: DeploymentStatus;
  error: string | null;
  ops: Record<string, unknown>[];
  before_snapshot: { value?: unknown } | null;
  after_snapshot: { value?: unknown; after_rollback?: unknown; restored?: boolean } | null;
  pr_url: string | null;
  can_rollback: boolean;
  verify_attempts: number;
  created_at: string;
  deployed_at: string | null;
  verified_at: string | null;
  reverted_at: string | null;
}

export interface DeployBatch {
  id: string;
  method: DeployMethod;
  trigger: "manual" | "autopilot";
  status: BatchStatus;
  created_by: string | null;
  created_at: string;
  finished_at: string | null;
  summary: Record<string, unknown>;
  counts: Record<string, number>;
}

export interface DeploySkip {
  action_id: string;
  reason: string;
}

export interface DeployResult {
  batch: DeployBatch | null;
  deployments: Deployment[];
  skipped: DeploySkip[];
}

export interface RollbackResult {
  reverted: Deployment[];
  skipped: DeploySkip[];
}

export interface DeployConnection {
  method: DeployMethod;
  status: "connected" | "needs_attention" | "disconnected" | string;
  label: string | null;
  last_error: string | null;
  last_tested_at: string | null;
  meta: Record<string, unknown>;
  chosen: boolean;
}

export interface DeployConnections {
  deploy_method: DeployMethod | null;
  connections: DeployConnection[];
  verified: boolean;
}

export interface DeployTest {
  ok: boolean;
  message: string;
  connection: DeployConnection;
}

export interface CloudflareSetup {
  site_token: string;
  signing_secret: string;
  rules_url: string;
  host: string;
  route: string;
  connection: DeployConnection;
}

export interface GithubPathRule {
  url: string;
  file: string;
}

export interface AutopilotSettings {
  enabled: boolean;
  weekly_cap: number;
  used_this_week: number;
  max_cap: number;
  ready: boolean;
  note: string;
}

export interface AppNotification {
  id: string;
  site_id: string | null;
  batch_id: string | null;
  kind: string;
  title: string;
  body: string;
  read: boolean;
  created_at: string;
}

export interface Notifications {
  unread: number;
  items: AppNotification[];
}
