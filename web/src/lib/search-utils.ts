import type { Connection, OpportunityType, SearchCoverage, SearchRange } from "@/lib/api/types";

export const RANGES: { value: SearchRange; label: string }[] = [
  { value: "28d", label: "28 days" },
  { value: "3m", label: "3 months" },
  { value: "16m", label: "16 months" },
];

export const OPPORTUNITY_LABELS: Record<OpportunityType, { title: string; short: string }> = {
  STRIKING_DISTANCE: {
    title: "Striking distance",
    short: "Ranking just below the top results for a lot of searches.",
  },
  CTR_GAP: {
    title: "Click-through gap",
    short: "Well placed, but far fewer people click than usual.",
  },
  DECAY: { title: "Losing clicks", short: "A page whose clicks dropped clearly against the previous 28 days." },
  CANNIBALISATION: { title: "Pages competing", short: "Two of your pages share one search." },
};

export const OPPORTUNITY_TYPES = Object.keys(OPPORTUNITY_LABELS) as OpportunityType[];

export function fmtInt(value: number): string {
  return Math.round(value).toLocaleString("en-US");
}

export function fmtPct(value: number, digits = 1): string {
  return `${(value * 100).toFixed(digits)}%`;
}

export function fmtPosition(value: number | null): string {
  return value === null ? "–" : value.toFixed(1);
}

export function fmtScore(value: number): string {
  return value >= 100 ? fmtInt(value) : value.toFixed(1);
}

/** "+12.3%" / "-4.0%", or null when there is nothing to compare with. */
export function changePct(current: number, previous: number | null | undefined): string | null {
  if (previous === undefined || previous === null || previous === 0) return null;
  const change = ((current - previous) / previous) * 100;
  return `${change >= 0 ? "+" : ""}${change.toFixed(1)}%`;
}

/** The path (and query) of a URL, which is what people recognise a page by. */
export function shortUrl(url: string): string {
  try {
    const parsed = new URL(url);
    const path = `${parsed.pathname}${parsed.search}`;
    return path === "/" || path === "" ? parsed.host : path;
  } catch {
    return url;
  }
}

export function formatDay(day: string): string {
  const [year, month, date] = day.split("-").map(Number);
  if (!year || !month || !date) return day;
  return new Date(Date.UTC(year, month - 1, date)).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    timeZone: "UTC",
  });
}

export function connectionLabel(conn: Connection | null): { text: string; tone: "success" | "warning" | "destructive" | "secondary" } {
  if (!conn) return { text: "Not connected", tone: "secondary" };
  switch (conn.status) {
    case "connected":
      return { text: "Connected", tone: "success" };
    case "pending":
      return { text: "Choose a property", tone: "warning" };
    case "needs_reauth":
      return { text: "Needs reconnecting", tone: "destructive" };
    default:
      return { text: "Problem", tone: "destructive" };
  }
}

/** One line on how much history has arrived, for the backfill progress display. */
export function backfillText(cov: SearchCoverage): string {
  if (cov.active_job) {
    const kind = cov.active_job.payload["mode"] === "backfill" ? "Importing history" : "Updating";
    const queued = cov.active_job.status === "queued" ? " (waiting to start)" : "";
    return `${kind}: ${cov.days_stored.toLocaleString("en-US")} of about ${cov.days_expected.toLocaleString("en-US")} days stored${queued}`;
  }
  if (cov.days_stored === 0) return "No Search Console data stored yet.";
  const done = cov.days_stored >= cov.days_expected - 3;
  return done
    ? `${cov.days_stored.toLocaleString("en-US")} days stored (${cov.first_day} to ${cov.last_day}).`
    : `${cov.days_stored.toLocaleString("en-US")} of about ${cov.days_expected.toLocaleString("en-US")} days stored. Some days are missing and will be filled in by the next update.`;
}
