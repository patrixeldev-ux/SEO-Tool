import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import type { Site } from "@/lib/api/types";
import { createFakeApi } from "@/test/fake-api";
import { COVERAGE_EMPTY, STATUS_NOT_CONFIGURED } from "@/test/fake-search";
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

function withData() {
  const api = createFakeApi({ signedIn: true });
  api.sitesByOrg.set("o1", [SITE]);
  api.search.coverage.set("s1", {
    ...COVERAGE_EMPTY,
    connected: true,
    property_url: "sc-domain:shop.example.com",
    first_day: "2025-06-01",
    last_day: "2026-10-10",
    days_stored: 485,
  });
  return api;
}

describe("Search Console page", () => {
  it("lists the sites and opens one", async () => {
    const api = withData();
    renderApp(api.client, "/search-console");
    const user = userEvent.setup();
    await user.click(await screen.findByRole("link", { name: /Shop/ }));
    expect(await screen.findByRole("heading", { name: "Search Console: Shop" })).toBeInTheDocument();
  });

  it("shows 'Connect Google Search Console to see this' when nothing is connected", async () => {
    const api = createFakeApi({ signedIn: true });
    api.sitesByOrg.set("o1", [SITE]);
    renderApp(api.client, "/search-console/s1");
    expect(await screen.findByRole("heading", { name: "Connect Google Search Console to see this" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Go to Settings" })).toBeInTheDocument();
  });

  it("points to the setup guide when Google isn't set up yet", async () => {
    const api = createFakeApi({ signedIn: true });
    api.sitesByOrg.set("o1", [SITE]);
    api.search.status = STATUS_NOT_CONFIGURED;
    renderApp(api.client, "/search-console/s1");
    expect(await screen.findByText(/Google isn't set up yet/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open the setup guide" })).toBeInTheDocument();
  });

  it("shows a progress message while the first import is still running with nothing stored", async () => {
    const api = createFakeApi({ signedIn: true });
    api.sitesByOrg.set("o1", [SITE]);
    api.search.coverage.set("s1", {
      ...COVERAGE_EMPTY,
      connected: true,
      active_job: { id: "j1", type: "gsc_sync", status: "queued", progress: 0, error: null, payload: { mode: "backfill" }, result: null },
    });
    renderApp(api.client, "/search-console/s1");
    expect(await screen.findByRole("heading", { name: "Importing your Search Console history" })).toBeInTheDocument();
  });

  it("charts clicks, impressions, CTR and position from the daily totals and explains the anonymised-query gap", async () => {
    const api = withData();
    renderApp(api.client, "/search-console/s1");
    const user = userEvent.setup();

    expect(await screen.findByRole("img", { name: /^Clicks per day/ })).toBeInTheDocument();
    for (const name of [/^Impressions per day/, /^Click-through rate per day/, /^Average position per day/]) {
      expect(screen.getByRole("img", { name })).toBeInTheDocument();
    }
    expect(screen.getByText("3,500")).toBeInTheDocument(); // clicks summary
    expect(screen.getByText("+16.7% vs previous")).toBeInTheDocument();
    expect(screen.getByText(/Totals include queries Google anonymises/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "16 months" }));
    await waitFor(() => expect(api.calls.some((c) => c.path === "/sites/s1/search/totals" && c.method === "GET")).toBe(true));
    expect(screen.getByRole("button", { name: "16 months" })).toHaveAttribute("aria-pressed", "true");
    expect(api.fetchMock.mock.calls.some(([u]) => String(u).includes("/search/totals?range=16m"))).toBe(true);
  });

  it("separates conversational queries behind a toggle, labelled as a heuristic", async () => {
    const api = withData();
    renderApp(api.client, "/search-console/s1");
    const user = userEvent.setup();

    const queries = within(await screen.findByRole("table", { name: "Top queries" }));
    expect(queries.getByText("espresso beans")).toBeInTheDocument();
    expect(queries.queryByText("tell me more")).not.toBeInTheDocument();
    expect(screen.getByText(/This is a/)).toHaveTextContent("heuristic");

    await user.click(screen.getByRole("button", { name: "All" }));
    expect(await screen.findByText("tell me more")).toBeInTheDocument();
    expect(screen.getByText("conversational", { selector: "span" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "All" })).toHaveAttribute("aria-pressed", "true");
    expect(api.fetchMock.mock.calls.some(([u]) => String(u).includes("/search/queries") && String(u).includes("segment=all"))).toBe(true);
  });

  it("lists opportunities of all four types with a plain explanation and a filter", async () => {
    const api = withData();
    renderApp(api.client, "/search-console/s1");
    const user = userEvent.setup();

    await user.click(await screen.findByRole("tab", { name: "Opportunities" }));
    const list = within(await screen.findByRole("list", { name: "Opportunities list" }));
    for (const title of ["Striking distance", "Click-through gap", "Losing clicks", "Pages competing"]) {
      expect(list.getByText(title, { selector: "span" })).toBeInTheDocument();
    }
    expect(list.getByText(/shows this page on average at position 6\.2/)).toBeInTheDocument();
    expect(list.getByText("Score 413")).toBeInTheDocument();
    expect(list.getAllByText("Evidence")).toHaveLength(4);

    await user.click(screen.getByRole("button", { name: /Losing clicks/ }));
    await waitFor(() => expect(within(screen.getByRole("list", { name: "Opportunities list" })).getAllByRole("listitem")).toHaveLength(1));
    expect(screen.getByText(/320 clicks in the last 28 days/)).toBeInTheDocument();
    expect(screen.getByText(/Based on .* Compared with your own click-through curve/)).toBeInTheDocument();
  });

  it("notes when an opportunity type could not be checked", async () => {
    const api = withData();
    api.search.opportunities = { ...api.search.opportunities, skipped: { DECAY: "Needs 56 days of history; 20 days are stored so far." } };
    renderApp(api.client, "/search-console/s1");
    const user = userEvent.setup();
    await user.click(await screen.findByRole("tab", { name: "Opportunities" }));
    expect(await screen.findByText(/Losing clicks was not checked: Needs 56 days/)).toBeInTheDocument();
  });

  it("opens a page drill-down with its queries and its CTR against the curve", async () => {
    const api = withData();
    renderApp(api.client, "/search-console/s1");
    const user = userEvent.setup();

    await user.click(await screen.findByRole("tab", { name: "Top pages" }));
    await user.click(await screen.findByRole("button", { name: "/beans" }));
    const sheet = within(await screen.findByRole("dialog"));
    expect(await sheet.findByText("Click-through rate against your curve")).toBeInTheDocument();
    expect(sheet.getByText("below the curve")).toBeInTheDocument();
    expect(sheet.getByText(/At position 3\.0 your pages usually get 10\.00%; this page gets 1\.00%/)).toBeInTheDocument();
    const queries = within(sheet.getByRole("table", { name: "Queries for this page" }));
    expect(queries.getByText("french press ratio")).toBeInTheDocument();
    expect(queries.getByText("10.00%")).toBeInTheDocument(); // expected CTR
    expect(sheet.getByRole("img", { name: /Click-through rate by position/ })).toBeInTheDocument();
    expect(sheet.getByLabelText("Opportunities for this page")).toBeInTheDocument();
  });

  it("shows a friendly message when a request fails", async () => {
    const api = withData();
    const original = api.fetchMock.getMockImplementation()!;
    api.fetchMock.mockImplementation(async (input, init) =>
      String(input).includes("/search/totals")
        ? new Response(JSON.stringify({ detail: "boom" }), { status: 500 })
        : original(input, init),
    );
    renderApp(api.client, "/search-console/s1");
    expect(await screen.findByText(/Could not load the charts/)).toBeInTheDocument();
    // the rest of the page still works
    expect(await screen.findByRole("table", { name: "Top queries" })).toBeInTheDocument();
  });
});

describe("AI reports", () => {
  async function openAi() {
    const api = withData();
    renderApp(api.client, "/search-console/s1");
    const user = userEvent.setup();
    await user.click(await screen.findByRole("tab", { name: "AI reports" }));
    return { api, user };
  }

  it("shows an empty state for each source", async () => {
    await openAi();
    expect(await screen.findAllByText("No file uploaded yet. Upload an export to see it here.")).toHaveLength(2);
  });

  it("shows the exact error, including the columns found, when a CSV is not understood", async () => {
    const { api, user } = await openAi();
    api.search.uploadError =
      "Could not find a date, page or query column. Columns found: Foo, Bar. Expected something like Date, Page, Query, Impressions.";
    const input = await screen.findByLabelText("Upload CSV for Google Search Console: generative AI report");
    await user.upload(input, new File(["Foo,Bar\n1,2\n"], "weird.csv", { type: "text/csv" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("This file could not be imported.");
    expect(alert).toHaveTextContent("Columns found: Foo, Bar");
    // nothing was added
    expect(screen.getAllByText("No file uploaded yet. Upload an export to see it here.")).toHaveLength(2);
  });

  it("imports a CSV, then charts it and lists the top pages", async () => {
    const { api, user } = await openAi();
    const input = await screen.findByLabelText("Upload CSV for Bing Webmaster Tools: AI Performance");
    await user.upload(input, new File(["Date,Impressions\n2026-09-01,10\n"], "bing.csv", { type: "text/csv" }));
    expect(await screen.findByText(/Imported bing\.csv: 14 rows/)).toBeInTheDocument();
    expect(await screen.findByRole("img", { name: "impressions per day" })).toBeInTheDocument();
    expect(screen.getByRole("table", { name: "Top pages" })).toHaveTextContent("/beans");
    const post = api.calls.find((c) => c.method === "POST" && c.path === "/sites/s1/ai-reports/bing_ai/import");
    expect(post).toBeDefined();
    expect(post?.headers["Content-Type"]).toBeUndefined(); // the browser sets the multipart boundary
  });
});
