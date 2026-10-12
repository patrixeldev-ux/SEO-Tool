import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Audit, IssueGroup, Site } from "@/lib/api/types";
import { auditPoll } from "@/lib/audit-config";
import { createFakeApi } from "@/test/fake-api";
import { renderApp } from "@/test/render-app";

const SITE: Site = {
  id: "s1",
  org_id: "o1",
  domain: "shop.example.com",
  uses_www: false,
  display_name: "Shop",
  settings: {},
  autopilot_enabled: false,
  created_at: "2026-10-11T10:00:00Z",
};

function doneAudit(over: Partial<Audit> = {}): Audit {
  return {
    id: "a1",
    site_id: "s1",
    job_id: "j1",
    status: "done",
    progress: 100,
    start_url: "https://shop.example.com/",
    page_limit: 500,
    pages_crawled: 53,
    urls_discovered: 53,
    created_at: "2026-10-12T10:00:00Z",
    started_at: "2026-10-12T10:00:01Z",
    finished_at: "2026-10-12T10:01:00Z",
    error: null,
    warnings: ["JS rendering check unavailable (no browser could be started on the server); it was skipped."],
    score: 72.4,
    subscores: { indexability: 90, on_page: 55.5, structured_data: 30, performance: 100, ai_readiness: null },
    issue_counts: { total: 12 },
    stop_reason: null,
    ...over,
  };
}

const GROUPS: IssueGroup[] = [
  {
    check_id: "TITLE_MISSING",
    title: "Title tag missing",
    severity: "high",
    category: "on_page",
    why_it_matters: "The title is the headline people see in search results.",
    how_to_fix: "Add a unique <title> to every page.",
    count: 2,
    items: [
      { page_id: "p1", url: "https://shop.example.com/a.html", details: {} },
      { page_id: "p2", url: "https://shop.example.com/b.html", details: {} },
    ],
    truncated: false,
  },
  {
    check_id: "HTTP_5XX",
    title: "Server error (5xx)",
    severity: "critical",
    category: "indexability",
    why_it_matters: "Pages that error cannot be indexed.",
    how_to_fix: "Fix the server error.",
    count: 1,
    items: [{ page_id: "p3", url: "https://shop.example.com/boom", details: { status_code: 500 } }],
    truncated: false,
  },
  {
    check_id: "LLMS_TXT",
    title: "No llms.txt file",
    severity: "info",
    category: "ai_readiness",
    why_it_matters: "Optional summary for AI tools.",
    how_to_fix: "Publish /llms.txt if you want to.",
    count: 1,
    items: [{ page_id: null, url: null, details: {} }],
    truncated: false,
  },
];

function withFinishedAudit() {
  const api = createFakeApi({ signedIn: true });
  api.sitesByOrg.set("o1", [SITE]);
  api.audits.set("s1", [doneAudit()]);
  api.auditData.issues = { groups: GROUPS, passed: [{ check_id: "H1_MISSING", title: "Every page has an H1", severity: "medium", category: "on_page", applicable: 40 }] };
  api.auditData.changes = {
    previous_audit_id: "a0",
    previous_score: 70,
    score: 72.4,
    score_delta: 2.4,
    new: [{ check_id: "HTTP_5XX", title: "Server error (5xx)", severity: "critical", url: "https://shop.example.com/boom" }],
    fixed: [{ check_id: "TITLE_LENGTH", title: "Title length", severity: "low", url: "https://shop.example.com/c.html" }],
    persisting_count: 9,
    not_rechecked_count: 0,
  };
  api.auditData.pages = {
    total: 1,
    items: [{ id: "p1", url: "https://shop.example.com/a.html", status_code: 200, fetch_error: null, depth: 1, title: null, word_count: 310, inlinks_count: 4, issue_count: 1 }],
  };
  api.auditData.page = {
    id: "p1",
    url: "https://shop.example.com/a.html",
    final_url: null,
    status_code: 200,
    fetch_error: null,
    redirect_chain: [],
    depth: 1,
    content_type: "text/html",
    ttfb_ms: 120,
    title: null,
    meta_description: "A description",
    h1: ["Heading"],
    canonical: null,
    robots_meta: null,
    x_robots_tag: null,
    lang: "en",
    word_count: 310,
    main_text_method: "trafilatura",
    in_sitemap: true,
    inlinks_count: 4,
    schema_types: ["Organization"],
    issues: [
      { check_id: "TITLE_MISSING", title: "Title tag missing", severity: "high", why_it_matters: "", how_to_fix: "Add a unique <title> to every page.", details: {} },
    ],
    inlinks: [{ url: "https://shop.example.com/", anchor: "Home", rel: null, page_id: "p0" }],
    outlinks: [],
    outlinks_total: 0,
  };
  return api;
}

beforeEach(() => {
  auditPoll.ms = 30;
});
afterEach(() => {
  auditPoll.ms = 2000;
});

describe("audit page", () => {
  it("shows a plain empty state and starts an audit with the chosen page limit, then follows its progress to the score", async () => {
    const api = createFakeApi({ signedIn: true });
    api.sitesByOrg.set("o1", [SITE]);
    let polls = 0;
    api.auditData.onPoll = (list) => {
      const a = list[0];
      if (!a) return;
      polls += 1;
      if (polls === 2) Object.assign(a, { status: "running", progress: 40, pages_crawled: 20, urls_discovered: 50 });
      if (polls === 4) Object.assign(a, doneAudit({ id: a.id, page_limit: a.page_limit }));
    };
    renderApp(api.client, "/audit/s1");
    const user = userEvent.setup();

    expect(await screen.findByRole("heading", { name: "No audit yet" })).toBeInTheDocument();
    expect(screen.getByText(/click Run audit/i)).toBeInTheDocument();

    const limit = screen.getByLabelText("Page limit");
    expect(limit).toHaveValue(500);
    await user.clear(limit);
    await user.type(limit, "75");
    await user.click(screen.getByRole("button", { name: /Run audit for Shop/ }));

    const post = api.calls.find((c) => c.method === "POST" && c.path === "/sites/s1/audits");
    expect(post?.body).toEqual({ page_limit: 75 });

    const bar = await screen.findByRole("progressbar", { name: "Audit progress" });
    expect(bar).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("progressbar", { name: "Audit progress" })).toHaveAttribute("aria-valuenow", "40"));
    expect(screen.getByText(/Crawled 20 of 50 pages/)).toBeInTheDocument();
    // can not start a second one while this runs
    expect(screen.getByRole("button", { name: /Run audit for Shop/ })).toBeDisabled();

    expect(await screen.findByTestId("audit-score")).toHaveTextContent("72");
    expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
  });

  it("does not start with an invalid page limit", async () => {
    const api = createFakeApi({ signedIn: true });
    api.sitesByOrg.set("o1", [SITE]);
    renderApp(api.client, "/audit/s1");
    const user = userEvent.setup();
    const limit = await screen.findByLabelText("Page limit");
    await user.clear(limit);
    await user.type(limit, "0");
    expect(screen.getByRole("button", { name: /Run audit for Shop/ })).toBeDisabled();
    await user.clear(limit);
    await user.type(limit, "10001");
    expect(screen.getByRole("button", { name: /Run audit for Shop/ })).toBeDisabled();
  });

  it("shows the score, the five subscores, issues grouped by severity and notes", async () => {
    const api = withFinishedAudit();
    renderApp(api.client, "/audit/s1");

    expect(await screen.findByTestId("audit-score")).toHaveTextContent("72");
    expect(await screen.findByText(/\+2\.4 since last audit/)).toBeInTheDocument();
    expect(screen.getByTestId("subscore-indexability")).toHaveTextContent("90");
    expect(screen.getByTestId("subscore-on_page")).toHaveTextContent("56");
    expect(screen.getByTestId("subscore-structured_data")).toHaveTextContent("30");
    expect(screen.getByTestId("subscore-performance")).toHaveTextContent("100");
    expect(screen.getByTestId("subscore-ai_readiness")).toHaveTextContent("–");
    expect(screen.getByText(/JS rendering check unavailable/)).toBeInTheDocument();

    const critical = await screen.findByRole("region", { name: "Critical issues" });
    expect(within(critical).getByText("Server error (5xx)")).toBeInTheDocument();
    const order = screen.getAllByRole("region").map((r) => r.getAttribute("aria-label"));
    expect(order).toEqual(["Critical issues", "High issues", "Info issues"]);
    expect(screen.getByText("Passed checks (1)")).toBeInTheDocument();
  });

  it("expands an issue to show why it matters, how to fix it and the affected URLs, and opens a page drawer", async () => {
    const api = withFinishedAudit();
    renderApp(api.client, "/audit/s1");
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: /Title tag missing/ }));
    expect(screen.getByText("The title is the headline people see in search results.")).toBeInTheDocument();
    expect(screen.getAllByText("Add a unique <title> to every page.").length).toBeGreaterThan(0);
    const affected = screen.getByRole("list", { name: "Affected by Title tag missing" });
    expect(within(affected).getAllByRole("button")).toHaveLength(2);

    await user.click(within(affected).getByRole("button", { name: "https://shop.example.com/a.html" }));
    const drawer = await screen.findByRole("dialog");
    expect(await within(drawer).findByText("Issues on this page")).toBeInTheDocument();
    expect(within(drawer).getByText("Organization")).toBeInTheDocument();
    expect(within(drawer).getByText("Linked from (4)")).toBeInTheDocument();
    await user.click(within(drawer).getByRole("button", { name: "Close" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("filters issues by category and by text", async () => {
    const api = withFinishedAudit();
    renderApp(api.client, "/audit/s1");
    const user = userEvent.setup();
    await screen.findByRole("region", { name: "Critical issues" });

    await user.click(screen.getByRole("button", { name: "On-page" }));
    expect(screen.queryByRole("region", { name: "Critical issues" })).not.toBeInTheDocument();
    expect(screen.getByRole("region", { name: "High issues" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "All" }));
    await user.type(screen.getByLabelText("Filter issues"), "nothing matches this");
    expect(screen.getByText("No issues match this filter.")).toBeInTheDocument();
  });

  it("shows what changed since the last audit", async () => {
    const api = withFinishedAudit();
    renderApp(api.client, "/audit/s1");
    expect(await screen.findByText("Changes since the last audit")).toBeInTheDocument();
    expect(screen.getByText(/1 fixed, 1 new, 9 still open/)).toBeInTheDocument();
    expect(screen.getByRole("list", { name: "Fixed" })).toHaveTextContent("Title length");
    expect(screen.getByRole("list", { name: "New" })).toHaveTextContent("Server error (5xx)");
  });

  it("downloads the CSV through the authenticated client", async () => {
    const api = withFinishedAudit();
    const createObjectURL = vi.fn((_blob: Blob) => "blob:csv");
    const revokeObjectURL = vi.fn();
    Object.assign(URL, { createObjectURL, revokeObjectURL });
    renderApp(api.client, "/audit/s1");
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: /Export CSV/ }));
    await waitFor(() => expect(createObjectURL).toHaveBeenCalled());
    const call = api.calls.find((c) => c.path === "/audits/a1/export.csv");
    expect(call?.headers["Authorization"]).toBe("Bearer access-1");
    const blob = createObjectURL.mock.calls[0]![0];
    expect(await blob.text()).toContain("TITLE_MISSING");
  });

  it("lists crawled pages on the Pages tab", async () => {
    const api = withFinishedAudit();
    renderApp(api.client, "/audit/s1");
    const user = userEvent.setup();
    await user.click(await screen.findByRole("tab", { name: "Pages" }));
    const table = await screen.findByRole("table");
    expect(within(table).getByRole("button", { name: "https://shop.example.com/a.html" })).toBeInTheDocument();
    expect(within(table).getByText("200")).toBeInTheDocument();
  });

  it("says plainly when an audit failed", async () => {
    const api = createFakeApi({ signedIn: true });
    api.sitesByOrg.set("o1", [SITE]);
    api.audits.set("s1", [doneAudit({ status: "failed", score: null, error: "We could not connect to the site (tried https://shop.example.com/)." })]);
    renderApp(api.client, "/audit/s1");
    expect(await screen.findByText("The audit could not finish")).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("We could not connect to the site");
    expect(screen.queryByTestId("audit-score")).not.toBeInTheDocument();
  });

  it("lets a viewer read the audit but not start one", async () => {
    const api = withFinishedAudit();
    api.orgs[0]!.role = "client_viewer";
    renderApp(api.client, "/audit/s1");
    expect(await screen.findByTestId("audit-score")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Run audit/ })).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Page limit")).not.toBeInTheDocument();
  });

  it("tells a viewer when there is nothing yet, without a Run button", async () => {
    const api = createFakeApi({ signedIn: true, role: "client_viewer" });
    api.sitesByOrg.set("o1", [SITE]);
    renderApp(api.client, "/audit/s1");
    expect(await screen.findByRole("heading", { name: "No audit yet" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Run audit/ })).not.toBeInTheDocument();
  });
});

describe("reaching the audit", () => {
  it("each site row has a Run audit button that starts the audit and opens its page", async () => {
    const api = createFakeApi({ signedIn: true });
    api.sitesByOrg.set("o1", [SITE]);
    renderApp(api.client, "/sites");
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: "Run audit for Shop" }));
    expect(await screen.findByRole("progressbar", { name: "Audit progress" })).toBeInTheDocument();
    expect(api.calls.some((c) => c.method === "POST" && c.path === "/sites/s1/audits")).toBe(true);
  });

  it("opens the running audit instead of failing when one is already in progress", async () => {
    const api = createFakeApi({ signedIn: true });
    api.sitesByOrg.set("o1", [SITE]);
    api.audits.set("s1", [doneAudit({ status: "running", progress: 10, score: null })]);
    renderApp(api.client, "/sites");
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Run audit for Shop" }));
    expect(await screen.findByRole("progressbar", { name: "Audit progress" })).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("viewers get a View audit link instead of a Run audit button", async () => {
    const api = createFakeApi({ signedIn: true, role: "client_viewer" });
    api.sitesByOrg.set("o1", [SITE]);
    renderApp(api.client, "/sites");
    expect(await screen.findByRole("link", { name: "View audit for Shop" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Run audit for Shop" })).not.toBeInTheDocument();
  });

  it("the Audit nav item lists sites and opens one", async () => {
    const api = createFakeApi({ signedIn: true });
    api.sitesByOrg.set("o1", [SITE]);
    renderApp(api.client, "/sites");
    const user = userEvent.setup();
    await user.click(await screen.findByRole("link", { name: "Audit" }));
    const list = await screen.findByRole("list", { name: "Choose a site" });
    await user.click(within(list).getByRole("link", { name: /Shop/ }));
    expect(await screen.findByRole("heading", { name: "Audit: Shop" })).toBeInTheDocument();
  });

  it("the Audit page asks for a site first when there are none", async () => {
    const api = createFakeApi({ signedIn: true });
    renderApp(api.client, "/audit");
    expect(await screen.findByRole("heading", { name: "No sites to audit yet" })).toBeInTheDocument();
  });

  it("sections that are not built yet say which phase brings them", async () => {
    const api = createFakeApi({ signedIn: true });
    renderApp(api.client, "/experiments");
    expect(await screen.findByRole("heading", { name: "Coming in Phase 5" })).toBeInTheDocument();
  });

  it("shows Core Web Vitals per page (field preferred, INP only from real visitors) and lists checks that did not run", async () => {
    const api = withFinishedAudit();
    api.auditData.issues.not_run = [
      {
        check_id: "CWV_INP_POOR",
        title: "Poor responsiveness (INP)",
        severity: "high",
        category: "performance",
        reason: "No PageSpeed Insights data: the PageSpeed step did not run for this audit.",
      },
    ];
    api.auditData.page = {
      ...api.auditData.page!,
      psi: {
        lcp_ms: 4600,
        lcp_source: "field",
        inp_ms: null,
        inp_source: null,
        cls: 0.04,
        cls_source: "lab",
        performance_score: 61,
        error: null,
        fetched_at: "2026-10-12T10:00:00Z",
      },
    };
    renderApp(api.client, "/audit/s1");
    const user = userEvent.setup();

    expect(await screen.findByText("Checks that did not run (1)")).toBeInTheDocument();
    expect(screen.getByText(/the PageSpeed step did not run/)).toBeInTheDocument();

    await user.click(await screen.findByRole("tab", { name: "Pages" }));
    await user.click(await screen.findByRole("button", { name: "https://shop.example.com/a.html" }));
    const vitals = within(await screen.findByLabelText("Core Web Vitals"));
    expect(vitals.getByText("4600 ms")).toBeInTheDocument();
    expect(vitals.getByText("poor · real visitors")).toBeInTheDocument();
    expect(vitals.getByText("No data")).toBeInTheDocument();
    expect(vitals.getByText("Only measured on real visitors")).toBeInTheDocument(); // INP is never estimated
    expect(vitals.getByText("0.040")).toBeInTheDocument();
    expect(vitals.getByText("good · lab test")).toBeInTheDocument();
  });
});
