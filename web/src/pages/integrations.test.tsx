import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { Site } from "@/lib/api/types";
import { createFakeApi } from "@/test/fake-api";
import { connection, COVERAGE_EMPTY, EMPTY_INTEGRATIONS, STATUS_NOT_CONFIGURED } from "@/test/fake-search";
import { renderApp } from "@/test/render-app";

vi.mock("@/lib/navigate", () => ({ redirectTo: vi.fn() }));
import { redirectTo } from "@/lib/navigate";

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

function setup(role: "owner" | "admin" | "member" | "client_viewer" = "owner") {
  const api = createFakeApi({ signedIn: true, role });
  api.sitesByOrg.set("o1", [SITE]);
  return api;
}

async function card() {
  return within(await screen.findByLabelText("Integrations for Shop"));
}

beforeEach(() => {
  vi.mocked(redirectTo).mockClear();
});

describe("Settings > Integrations", () => {
  it("says Google isn't set up yet and links to the in-app setup guide, without a Connect button", async () => {
    const api = setup();
    api.search.status = STATUS_NOT_CONFIGURED;
    renderApp(api.client, "/settings");
    const user = userEvent.setup();

    const shop = await card();
    expect(await shop.findByText("Google isn't set up yet")).toBeInTheDocument();
    expect(shop.queryByRole("button", { name: /Connect Google/ })).not.toBeInTheDocument();
    await user.click(shop.getByRole("link", { name: "Open the setup guide" }));
    expect(await screen.findByRole("heading", { name: /Connecting Google Search Console/ })).toBeInTheDocument();
    expect(screen.getAllByText(/http:\/\/localhost:8000\/integrations\/google\/callback/).length).toBeGreaterThan(0);
  });

  it("offers Connect Google Search Console and sends the browser to the consent screen", async () => {
    const api = setup();
    renderApp(api.client, "/settings");
    const user = userEvent.setup();

    const shop = await card();
    const google = within(await shop.findByRole("region", { name: "Google Search Console" }));
    expect(google.getByText("Not connected")).toBeInTheDocument();
    await user.click(shop.getByRole("button", { name: "Connect Google Search Console" }));
    await waitFor(() => expect(redirectTo).toHaveBeenCalledWith(api.search.connectUrl));
    expect(api.calls.some((c) => c.method === "POST" && c.path === "/sites/s1/integrations/google/connect")).toBe(true);
  });

  it("explains why connecting failed after coming back from Google", async () => {
    const api = setup();
    renderApp(api.client, "/settings?google=error&reason=denied&site=s1");
    expect(await screen.findByText("Google access was not granted, so nothing was connected.")).toBeInTheDocument();
  });

  it("confirms a successful return from Google", async () => {
    const api = setup();
    renderApp(api.client, "/settings?google=connected&site=s1");
    expect(await screen.findByText(/Google is connected\. Now choose/)).toBeInTheDocument();
  });

  it("lets the user pick a property (suggested first, unusable ones hidden) and then shows connected", async () => {
    const api = setup();
    api.search.integrations.set("s1", { ...EMPTY_INTEGRATIONS, google: connection({ status: "pending", property_url: null }) });
    renderApp(api.client, "/settings");
    const user = userEvent.setup();

    const shop = await card();
    expect(await shop.findByText("Choose a property")).toBeInTheDocument();
    const select = await shop.findByLabelText(/Which Search Console property is shop\.example\.com/);
    expect(select).toHaveValue("sc-domain:shop.example.com");
    expect(within(select).queryByText("https://other.example/")).not.toBeInTheDocument();

    api.search.coverage.set("s1", { ...COVERAGE_EMPTY, connected: true, property_url: "sc-domain:shop.example.com" });
    await user.click(shop.getByRole("button", { name: "Use this property" }));
    const put = api.calls.find((c) => c.method === "PUT" && c.path === "/sites/s1/integrations/google/property");
    expect(put?.body).toEqual({ site_url: "sc-domain:shop.example.com" });
    expect(await shop.findByText("Connected", { selector: "span" })).toBeInTheDocument();
  });

  it("shows backfill progress while the history is importing", async () => {
    const api = setup();
    api.search.integrations.set("s1", { ...EMPTY_INTEGRATIONS, google: connection() });
    api.search.coverage.set("s1", {
      ...COVERAGE_EMPTY,
      connected: true,
      property_url: "sc-domain:shop.example.com",
      days_stored: 120,
      days_expected: 485,
      active_job: { id: "j1", type: "gsc_sync", status: "running", progress: 25, error: null, payload: { mode: "backfill" }, result: null },
    });
    renderApp(api.client, "/settings");

    const shop = await card();
    const bar = await shop.findByRole("progressbar", { name: "Search Console import progress" });
    expect(bar).toHaveAttribute("aria-valuenow", "25");
    expect(shop.getByText("Importing history: 120 of about 485 days stored")).toBeInTheDocument();
    expect(shop.getByRole("button", { name: "Update now" })).toBeDisabled();
  });

  it("asks to reconnect when Google access was removed", async () => {
    const api = setup();
    api.search.integrations.set("s1", {
      ...EMPTY_INTEGRATIONS,
      google: connection({ status: "needs_reauth", last_error: "Google access was removed or expired." }),
    });
    renderApp(api.client, "/settings");
    const shop = await card();
    expect(await shop.findByText("Google access needs to be renewed")).toBeInTheDocument();
    expect(shop.getByText(/Data already imported is still available/)).toBeInTheDocument();
    expect(shop.getByRole("button", { name: "Reconnect Google" })).toBeInTheDocument();
  });

  it("disconnects only after a confirmation", async () => {
    const api = setup();
    api.search.integrations.set("s1", { ...EMPTY_INTEGRATIONS, google: connection() });
    api.search.coverage.set("s1", { ...COVERAGE_EMPTY, connected: true, property_url: "sc-domain:shop.example.com", days_stored: 485 });
    renderApp(api.client, "/settings");
    const user = userEvent.setup();

    const shop = await card();
    const google = within(await shop.findByRole("region", { name: "Google Search Console" }));
    await user.click(google.getByRole("button", { name: "Disconnect" }));
    expect(api.calls.some((c) => c.method === "DELETE")).toBe(false);
    await user.click(google.getByRole("button", { name: "Cancel" }));
    expect(api.calls.some((c) => c.method === "DELETE")).toBe(false);

    await user.click(google.getByRole("button", { name: "Disconnect" }));
    await user.click(google.getByRole("button", { name: "Yes, disconnect" }));
    await waitFor(() => expect(api.calls.some((c) => c.method === "DELETE" && c.path === "/sites/s1/integrations/google")).toBe(true));
    expect(await shop.findByRole("button", { name: "Connect Google Search Console" })).toBeInTheDocument();
  });

  it("checks a Bing key: a rejected key shows Bing's message, a good key connects", async () => {
    const api = setup();
    renderApp(api.client, "/settings");
    const user = userEvent.setup();

    const shop = await card();
    const field = await shop.findByLabelText("Bing Webmaster API key");
    await user.type(field, "bad-key-123456");
    await user.click(shop.getByRole("button", { name: "Save and check key" }));
    expect(await shop.findByRole("alert")).toHaveTextContent("InvalidApiKey");
    expect(shop.queryByText("bad-key-123456")).not.toBeInTheDocument();

    await user.clear(field);
    await user.type(field, "good-key-123456");
    await user.click(shop.getByRole("button", { name: "Save and check key" }));
    const bing = within(await shop.findByRole("region", { name: "Bing Webmaster Tools" }));
    expect(await bing.findByText("Connected")).toBeInTheDocument();
    expect(bing.queryByLabelText("Bing Webmaster API key")).not.toBeInTheDocument();
  });

  it("reports the PageSpeed Insights key status without showing a key", async () => {
    const api = setup();
    renderApp(api.client, "/settings");
    const shop = await card();
    const psi = within(await shop.findByRole("region", { name: "PageSpeed Insights" }));
    expect(psi.getByText("On")).toBeInTheDocument();
    expect(psi.getByText("No API key")).toBeInTheDocument();
    expect(psi.getByText(/PSI_API_KEY/)).toBeInTheDocument();
  });

  it("does not offer connect buttons to members", async () => {
    const api = setup("member");
    renderApp(api.client, "/settings");
    const shop = await card();
    expect(await shop.findByText("Ask an owner or admin to connect Google.")).toBeInTheDocument();
    expect(shop.queryByRole("button", { name: /Connect Google/ })).not.toBeInTheDocument();
    expect(shop.getByText("Ask an owner or admin to add the Bing key.")).toBeInTheDocument();
  });

  it("shows a friendly empty state when the organisation has no sites", async () => {
    const api = createFakeApi({ signedIn: true });
    renderApp(api.client, "/settings");
    expect(await screen.findByText("Add a website first, then connect its search data here.")).toBeInTheDocument();
  });
});
