import type { Audit, AuditStatus, Category, IssueGroup, Severity } from "@/lib/api/types";

export const SEVERITIES: Severity[] = ["critical", "high", "medium", "low", "info"];

export const SEVERITY_LABEL: Record<Severity, string> = {
  critical: "Critical",
  high: "High",
  medium: "Medium",
  low: "Low",
  info: "Info",
};

export const CATEGORIES: Category[] = ["indexability", "on_page", "structured_data", "performance", "ai_readiness"];

export const CATEGORY_LABEL: Record<Category, string> = {
  indexability: "Indexability",
  on_page: "On-page",
  structured_data: "Structured data",
  performance: "Performance",
  ai_readiness: "AI readiness",
};

export type ScoreBand = "good" | "okay" | "poor" | "unscored";

/** The number shown for a score: a whole number, or an en dash when nothing could be scored. */
export function formatScore(score: number | null | undefined): string {
  if (score === null || score === undefined || Number.isNaN(score)) return "–";
  return String(Math.round(score));
}

export function scoreBand(score: number | null | undefined): ScoreBand {
  if (score === null || score === undefined || Number.isNaN(score)) return "unscored";
  if (score >= 80) return "good";
  if (score >= 50) return "okay";
  return "poor";
}

export const BAND_LABEL: Record<ScoreBand, string> = {
  good: "Healthy",
  okay: "Needs work",
  poor: "Needs attention",
  unscored: "Not scored",
};

/** "+3.5", "-2", "no change": how the score moved since the previous audit. */
export function formatDelta(delta: number | null | undefined): string {
  if (delta === null || delta === undefined) return "";
  if (delta === 0) return "no change";
  const rounded = Math.round(delta * 10) / 10;
  return `${rounded > 0 ? "+" : "-"}${Math.abs(rounded)}`;
}

export interface SeveritySection {
  severity: Severity;
  label: string;
  groups: IssueGroup[];
  /** Total affected URLs / site-wide findings in this section. */
  total: number;
}

export interface IssueFilter {
  category?: Category | "all";
  text?: string;
}

export function filterGroups(groups: IssueGroup[], filter: IssueFilter = {}): IssueGroup[] {
  const text = (filter.text ?? "").trim().toLowerCase();
  return groups.filter((g) => {
    if (filter.category && filter.category !== "all" && g.category !== filter.category) return false;
    if (!text) return true;
    return (
      g.title.toLowerCase().includes(text) ||
      g.check_id.toLowerCase().includes(text) ||
      g.items.some((i) => (i.url ?? "").toLowerCase().includes(text))
    );
  });
}

/** Issues grouped by severity (most severe first); inside a section the biggest problems come first. */
export function sectionsBySeverity(groups: IssueGroup[]): SeveritySection[] {
  const sections: SeveritySection[] = [];
  for (const severity of SEVERITIES) {
    const inSection = groups
      .filter((g) => g.severity === severity)
      .sort((a, b) => b.count - a.count || a.check_id.localeCompare(b.check_id));
    if (inSection.length === 0) continue;
    sections.push({
      severity,
      label: SEVERITY_LABEL[severity],
      groups: inSection,
      total: inSection.reduce((sum, g) => sum + g.count, 0),
    });
  }
  return sections;
}

export function isActive(status: AuditStatus): boolean {
  return status === "queued" || status === "running";
}

/** The audit to show by default: the one in progress, otherwise the latest. `audits` are newest first. */
export function pickCurrent(audits: Audit[], selectedId?: string | null): Audit | undefined {
  if (selectedId) {
    const chosen = audits.find((a) => a.id === selectedId);
    if (chosen) return chosen;
  }
  return audits.find((a) => isActive(a.status)) ?? audits[0];
}

export function progressText(audit: Audit): string {
  if (audit.status === "queued") return "Waiting for a worker to pick this up…";
  if (audit.progress >= 93) return "Scoring the results…";
  if (audit.progress >= 80) return "Checking which pages need JavaScript to show their content…";
  const discovered = Math.max(audit.urls_discovered, audit.pages_crawled);
  return discovered > 0
    ? `Crawled ${audit.pages_crawled} of ${discovered} pages found so far`
    : "Starting the crawl…";
}

/** A short plain-language description of what happened to the crawl. */
export function stopReasonText(reason: string | null): string | null {
  if (reason === "page_limit") return "Stopped at your page limit; the site has more pages than were audited.";
  if (reason === "cancelled") return "This audit was cancelled before it finished.";
  return null;
}
