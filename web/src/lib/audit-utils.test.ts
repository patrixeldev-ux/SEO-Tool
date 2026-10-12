import { describe, expect, it } from "vitest";

import type { Audit, IssueGroup } from "@/lib/api/types";
import {
  filterGroups,
  formatDelta,
  formatScore,
  pickCurrent,
  progressText,
  scoreBand,
  sectionsBySeverity,
} from "@/lib/audit-utils";

function group(over: Partial<IssueGroup> & Pick<IssueGroup, "check_id" | "severity">): IssueGroup {
  return {
    title: over.check_id,
    category: "on_page",
    why_it_matters: "",
    how_to_fix: "",
    count: 1,
    items: [],
    truncated: false,
    ...over,
  };
}

function audit(over: Partial<Audit>): Audit {
  return {
    id: "a1",
    site_id: "s1",
    job_id: null,
    status: "done",
    progress: 100,
    start_url: "https://example.com/",
    page_limit: 500,
    pages_crawled: 10,
    urls_discovered: 10,
    created_at: "2026-10-12T10:00:00Z",
    started_at: null,
    finished_at: null,
    error: null,
    warnings: [],
    score: 80,
    subscores: {},
    issue_counts: {},
    stop_reason: null,
    ...over,
  };
}

describe("score display", () => {
  it("rounds to a whole number and shows a dash when nothing could be scored", () => {
    expect(formatScore(87.4)).toBe("87");
    expect(formatScore(87.5)).toBe("88");
    expect(formatScore(0)).toBe("0");
    expect(formatScore(100)).toBe("100");
    expect(formatScore(null)).toBe("–");
    expect(formatScore(undefined)).toBe("–");
  });

  it("bands scores at 80 and 50 and never calls a missing score healthy", () => {
    expect(scoreBand(100)).toBe("good");
    expect(scoreBand(80)).toBe("good");
    expect(scoreBand(79.9)).toBe("okay");
    expect(scoreBand(50)).toBe("okay");
    expect(scoreBand(49.9)).toBe("poor");
    expect(scoreBand(0)).toBe("poor");
    expect(scoreBand(null)).toBe("unscored");
  });

  it("describes the movement since the previous audit", () => {
    expect(formatDelta(3.54)).toBe("+3.5");
    expect(formatDelta(-2)).toBe("-2");
    expect(formatDelta(0)).toBe("no change");
    expect(formatDelta(null)).toBe("");
  });
});

describe("issue grouping", () => {
  const groups = [
    group({ check_id: "TITLE_LENGTH", severity: "low", count: 4 }),
    group({ check_id: "HTTP_5XX", severity: "critical", count: 1, category: "indexability" }),
    group({ check_id: "TITLE_MISSING", severity: "high", count: 2 }),
    group({ check_id: "HTTP_4XX", severity: "high", count: 7, category: "indexability" }),
    group({ check_id: "LLMS_TXT", severity: "info", count: 1, category: "ai_readiness" }),
  ];

  it("orders sections from most to least severe and skips empty ones", () => {
    const sections = sectionsBySeverity(groups);
    expect(sections.map((s) => s.severity)).toEqual(["critical", "high", "low", "info"]);
    expect(sections.map((s) => s.label)).toEqual(["Critical", "High", "Low", "Info"]);
  });

  it("puts the biggest problem first inside a section and totals the affected items", () => {
    const high = sectionsBySeverity(groups).find((s) => s.severity === "high")!;
    expect(high.groups.map((g) => g.check_id)).toEqual(["HTTP_4XX", "TITLE_MISSING"]);
    expect(high.total).toBe(9);
  });

  it("returns nothing for no issues", () => {
    expect(sectionsBySeverity([])).toEqual([]);
  });

  it("filters by category and by text in the title, id or an affected URL", () => {
    expect(filterGroups(groups, { category: "indexability" }).map((g) => g.check_id)).toEqual(["HTTP_5XX", "HTTP_4XX"]);
    expect(filterGroups(groups, { category: "all" })).toHaveLength(5);
    expect(filterGroups(groups, { text: "  llms " }).map((g) => g.check_id)).toEqual(["LLMS_TXT"]);
    const withUrl = [group({ check_id: "A", severity: "low", items: [{ page_id: "p", url: "https://x.test/pricing", details: {} }] }), group({ check_id: "B", severity: "low" })];
    expect(filterGroups(withUrl, { text: "pricing" }).map((g) => g.check_id)).toEqual(["A"]);
  });
});

describe("choosing which audit to show", () => {
  it("prefers the running audit, then the newest", () => {
    const newest = audit({ id: "new" });
    const old = audit({ id: "old" });
    const running = audit({ id: "run", status: "running", progress: 30 });
    expect(pickCurrent([newest, old])?.id).toBe("new");
    expect(pickCurrent([newest, running, old])?.id).toBe("run");
    expect(pickCurrent([newest, old], "old")?.id).toBe("old");
    expect(pickCurrent([newest, old], "gone")?.id).toBe("new");
    expect(pickCurrent([])).toBeUndefined();
  });

  it("explains progress in plain words", () => {
    expect(progressText(audit({ status: "queued", progress: 0 }))).toMatch(/waiting/i);
    expect(progressText(audit({ status: "running", progress: 20, pages_crawled: 12, urls_discovered: 40 }))).toBe(
      "Crawled 12 of 40 pages found so far",
    );
    expect(progressText(audit({ status: "running", progress: 85 }))).toMatch(/JavaScript/);
    expect(progressText(audit({ status: "running", progress: 95 }))).toMatch(/scoring/i);
  });
});
