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

export interface AuditIssues {
  groups: IssueGroup[];
  passed: PassedCheck[];
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
