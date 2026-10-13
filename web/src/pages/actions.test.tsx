import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { Site } from "@/lib/api/types";
import { createFakeApi } from "@/test/fake-api";
import { manualTask, planAction } from "@/test/fake-actions";
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

function withPlan(role: "owner" | "member" | "client_viewer" = "owner") {
  const api = createFakeApi({ signedIn: true, role });
  api.sitesByOrg.set("o1", [SITE]);
  api.actions.actions = [
    planAction(),
    planAction({
      id: "a2",
      type: "rewrite_meta",
      type_label: "Rewrite the meta description",
      field: "meta_description",
      current_value: "A description.",
      proposed_value: "Freshly roasted coffee beans, shipped within two days of roasting, with tasting notes for every bag.",
      priority: 1.0,
    }),
    planAction({
      id: "a3",
      type: "fix_canonical",
      type_label: "Fix the canonical tag",
      risk_class: "approve",
      field: "canonical",
      target_url: "https://shop.example.com/old",
      current_value: "https://shop.example.com/missing",
      proposed_value: "https://shop.example.com/old",
      priority: 2.0,
      expected_gain: 4,
    }),
    manualTask(),
  ];
  return api;
}

const cards = () => screen.getAllByTestId("action-card");

describe("Actions page", () => {
  it("lists the sites and opens the plan of one", async () => {
    const api = withPlan();
    renderApp(api.client, "/actions");
    const user = userEvent.setup();
    await user.click(await screen.findByRole("link", { name: /Shop/ }));
    expect(await screen.findByRole("heading", { name: "Actions: Shop" })).toBeInTheDocument();
  });

  it("shows the summary, sorts by priority and labels estimates honestly", async () => {
    const api = withPlan();
    renderApp(api.client, "/actions/s1");
    await screen.findByRole("heading", { name: "Actions: Shop" });
    const summary = await screen.findByRole("region", { name: "Plan summary" });
    expect(within(summary).getByText("On the board")).toBeInTheDocument();
    expect(within(summary).getByText(/prior, not measured/)).toBeInTheDocument();

    await waitFor(() => expect(cards()).toHaveLength(3));
    // priority order: canonical (2.0), title (1.5), meta (1.0); the manual task is on its own tab
    expect(cards().map((c) => within(c).getAllByText(/Rewrite|Fix the/)[0]!.textContent)).toEqual([
      "Fix the canonical tag",
      "Rewrite the page title",
      "Rewrite the meta description",
    ]);
    expect(within(cards()[1]!).getByText(/prior, not measured/)).toBeInTheDocument();
    expect(screen.queryByText("Earn links and mentions")).not.toBeInTheDocument();
  });

  it("filters by risk class and type", async () => {
    const api = withPlan();
    renderApp(api.client, "/actions/s1");
    const user = userEvent.setup();
    await waitFor(() => expect(cards()).toHaveLength(3));

    await user.click(screen.getByRole("button", { name: "Needs approval" }));
    await waitFor(() => expect(cards()).toHaveLength(1));
    expect(within(cards()[0]!).getByText("Fix the canonical tag")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "All" }));
    await waitFor(() => expect(cards()).toHaveLength(3));
    await user.selectOptions(screen.getByLabelText("Type"), "rewrite_meta");
    await waitFor(() => expect(cards()).toHaveLength(1));
    expect(within(cards()[0]!).getByText("Rewrite the meta description")).toBeInTheDocument();
  });

  it("shows the current value against the proposed one with the difference marked", async () => {
    const api = withPlan();
    renderApp(api.client, "/actions/s1");
    await waitFor(() => expect(cards()).toHaveLength(3));
    const title = cards().find((c) => within(c).queryByText("Rewrite the page title"))!;
    const view = within(title).getByTestId("diff-view");
    expect(view.querySelector("del")?.textContent).toBe("Beans");
    expect(view.querySelector("ins")?.textContent).toContain("Fresh roasted coffee beans");
  });

  it("approves several actions at once and reports each result", async () => {
    const api = withPlan();
    renderApp(api.client, "/actions/s1");
    const user = userEvent.setup();
    await waitFor(() => expect(cards()).toHaveLength(3));

    await user.click(screen.getByRole("checkbox", { name: /Select Rewrite the page title/ }));
    await user.click(screen.getByRole("checkbox", { name: /Select Fix the canonical tag/ }));
    expect(screen.getByText("2 selected")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Approve selected" }));

    await waitFor(() => expect(screen.getByRole("status", { name: "" })).toBeTruthy());
    const call = api.calls.find((c) => c.path === "/sites/s1/actions/bulk");
    expect(call?.body).toEqual({ ids: expect.arrayContaining(["a1", "a3"]), op: "approve" });
    expect((call?.body as { ids: string[] }).ids).toHaveLength(2);
    expect(await screen.findByText("Approved 2.")).toBeInTheDocument();
    await waitFor(() => expect(screen.getAllByText("Approved").length).toBeGreaterThanOrEqual(2));
    expect(screen.getByText("0 selected")).toBeInTheDocument();
  });

  it("selects everything shown with one checkbox", async () => {
    const api = withPlan();
    renderApp(api.client, "/actions/s1");
    const user = userEvent.setup();
    await waitFor(() => expect(cards()).toHaveLength(3));
    await user.click(screen.getByRole("checkbox", { name: "Select all shown" }));
    expect(screen.getByText("3 selected")).toBeInTheDocument();
    await user.click(screen.getByRole("checkbox", { name: "Select all shown" }));
    expect(screen.getByText("0 selected")).toBeInTheDocument();
  });

  it("rejects in bulk only with a reason", async () => {
    const api = withPlan();
    renderApp(api.client, "/actions/s1");
    const user = userEvent.setup();
    await waitFor(() => expect(cards()).toHaveLength(3));
    await user.click(screen.getByRole("checkbox", { name: /Select Rewrite the meta description/ }));
    await user.click(screen.getByRole("button", { name: "Reject selected…" }));
    const submit = screen.getByRole("button", { name: "Reject selected" });
    expect(submit).toBeDisabled();
    await user.type(screen.getByLabelText("Why are you rejecting these?"), "Fine as it is");
    await user.click(submit);
    await waitFor(() =>
      expect(api.calls.find((c) => c.path === "/sites/s1/actions/bulk")?.body).toEqual({
        ids: ["a2"],
        op: "reject",
        reason: "Fine as it is",
      }),
    );
  });

  it("rejects one action with a reason, kept for 90 days", async () => {
    const api = withPlan();
    renderApp(api.client, "/actions/s1");
    const user = userEvent.setup();
    await waitFor(() => expect(cards()).toHaveLength(3));
    const card = cards().find((c) => within(c).queryByText("Fix the canonical tag"))!;
    await user.click(within(card).getByRole("button", { name: "Reject" }));
    const reject = within(card).getByRole("button", { name: "Reject" });
    expect(reject).toBeDisabled();
    await user.type(within(card).getByLabelText("Why are you rejecting this?"), "We changed it on purpose");
    await user.click(within(card).getByRole("button", { name: "Reject" }));
    await waitFor(() => expect(api.actions.actions.find((a) => a.id === "a3")?.status).toBe("rejected"));
    expect(api.actions.actions.find((a) => a.id === "a3")?.reject_reason).toBe("We changed it on purpose");
    await waitFor(() => expect(cards()).toHaveLength(2));
  });

  it("shows the validator's messages when an edit does not pass, and keeps the old value", async () => {
    const api = withPlan();
    renderApp(api.client, "/actions/s1");
    const user = userEvent.setup();
    await waitFor(() => expect(cards()).toHaveLength(3));
    const card = cards().find((c) => within(c).queryByText("Rewrite the page title"))!;

    await user.click(within(card).getByRole("button", { name: "Edit" }));
    const box = within(card).getByLabelText("Your version");
    await user.clear(box);
    await user.type(box, "Too short");
    await user.click(within(card).getByRole("button", { name: "Save" }));

    const alert = await within(card).findByRole("alert", { name: "The new value does not pass these checks" });
    expect(alert).toHaveTextContent("The title is 9 characters; it must be at least 30.");
    expect(api.actions.actions.find((a) => a.id === "a1")?.proposed_value).toBe("Fresh roasted coffee beans for home brewing");

    await user.clear(box);
    await user.type(box, "Hand-written title that says it plainly");
    await user.click(within(card).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(within(card).queryByLabelText("Your version")).not.toBeInTheDocument());
    expect(await within(card).findByText("Edited by you")).toBeInTheDocument();
    expect(within(card).getByRole("button", { name: "Restore AI version" })).toBeInTheDocument();
  });

  it("shows several validator problems at once", async () => {
    const api = withPlan();
    api.actions.rejectEdit = ["The title is 120 characters; it must be at most 60.", "The title repeats the word \"coffee\" 4 times."];
    renderApp(api.client, "/actions/s1");
    const user = userEvent.setup();
    await waitFor(() => expect(cards()).toHaveLength(3));
    const card = cards().find((c) => within(c).queryByText("Rewrite the page title"))!;
    await user.click(within(card).getByRole("button", { name: "Edit" }));
    await user.click(within(card).getByRole("button", { name: "Save" }));
    const alert = await within(card).findByRole("alert");
    expect(within(alert).getAllByRole("listitem")).toHaveLength(2);
    expect(alert).toHaveTextContent("repeats the word");
  });

  it("explains invalid JSON in the editor before calling the server", async () => {
    const api = withPlan();
    api.actions.actions.push(
      planAction({
        id: "a4",
        type: "collapse_redirect",
        type_label: "Collapse a redirect chain",
        risk_class: "approve",
        field: "redirect",
        priority: 0.2,
        current_value: null,
        proposed_value: { from: "https://shop.example.com/a", to: "https://shop.example.com/b", status: 301 },
      }),
    );
    renderApp(api.client, "/actions/s1");
    const user = userEvent.setup();
    await waitFor(() => expect(cards()).toHaveLength(4));
    const redirect = cards().find((c) => within(c).queryByText("Collapse a redirect chain"))!;
    await user.click(within(redirect).getByRole("button", { name: "Edit" }));
    const box = within(redirect).getByLabelText("Your version");
    await user.clear(box);
    await user.type(box, "{{oops");
    await user.click(within(redirect).getByRole("button", { name: "Save" }));
    expect(await within(redirect).findByRole("alert")).toHaveTextContent("not valid JSON");
    expect(api.calls.some((c) => c.method === "PATCH" && c.path === "/sites/s1/actions/a4")).toBe(false);
  });

  it("shows the needs-AI banner with a link to Settings > AI, and hides it when nothing waits", async () => {
    const api = withPlan();
    api.actions.actions[0] = planAction({ status: "needs_ai", proposed_value: null, original_value: null, generation: { state: "no_key" } });
    renderApp(api.client, "/actions/s1");
    const banner = await screen.findByText(/1 suggestion is waiting for an AI/);
    const region = banner.closest("[role=status]") as HTMLElement;
    expect(region).toHaveTextContent("No AI key is set");
    const link = within(region).getByRole("link", { name: /Settings > AI/ });
    expect(link).toHaveAttribute("href", "/settings?tab=ai");
    const waiting = cards().find((c) => within(c).queryByText("Needs AI"))!;
    expect(within(waiting).getByText(/Waiting for an AI suggestion/)).toBeInTheDocument();
    expect(within(waiting).queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
    expect(within(waiting).getByRole("button", { name: "Write it yourself" })).toBeInTheDocument();
  });

  it("does not show the banner when every suggestion has a value", async () => {
    const api = withPlan();
    renderApp(api.client, "/actions/s1");
    await waitFor(() => expect(cards()).toHaveLength(3));
    expect(screen.queryByText(/waiting for an AI\./)).not.toBeInTheDocument();
  });

  it("explains a misconfigured AI instead of 'no key'", async () => {
    const api = withPlan();
    api.actions.actions[0] = planAction({ status: "needs_ai", proposed_value: null });
    api.actions.ai = { ...api.actions.ai, problem: "GEMINI_MODEL is required when using Gemini; set it in .env." };
    renderApp(api.client, "/actions/s1");
    expect(await screen.findByText(/GEMINI_MODEL is required/)).toBeInTheDocument();
  });

  it("ticks off manual task steps and marks the task done", async () => {
    const api = withPlan();
    renderApp(api.client, "/actions/s1");
    const user = userEvent.setup();
    await user.click(await screen.findByRole("tab", { name: /Manual tasks/ }));
    const task = await screen.findByTestId("manual-task");
    expect(within(task).getByText("0 of 3 done")).toBeInTheDocument();
    expect(within(task).getByText(/Buy links/)).toBeInTheDocument();

    await user.click(within(task).getByRole("checkbox", { name: /Choose the one to three pages/ }));
    await waitFor(() =>
      expect(api.calls.find((c) => c.method === "PATCH" && c.path === "/sites/s1/actions/m1")?.body).toEqual({
        checklist_done: ["pick-pages"],
      }),
    );
    expect(await within(task).findByText("1 of 3 done")).toBeInTheDocument();
    expect(within(task).getByRole("checkbox", { name: /Choose the one to three pages/ })).toBeChecked();

    await user.click(within(task).getByRole("button", { name: "Mark task done" }));
    await waitFor(() => expect(api.actions.actions.find((a) => a.id === "m1")?.status).toBe("manual_done"));
    expect(await within(await screen.findByTestId("manual-task")).findByText("Done")).toBeInTheDocument();
  });

  it("saves the autopilot choice for admins and explains the safety checks", async () => {
    const api = withPlan();
    renderApp(api.client, "/actions/s1");
    const user = userEvent.setup();
    const toggle = await screen.findByRole("switch", { name: "Autopilot" });
    expect(screen.getByText(/undone automatically if it does\s+not show up/)).toBeInTheDocument();
    expect(toggle).toHaveAttribute("aria-checked", "false");
    await user.click(toggle);
    await waitFor(() => expect(api.sitesByOrg.get("o1")![0]!.autopilot_enabled).toBe(true));
    expect(api.calls.find((c) => c.method === "PATCH" && c.path === "/sites/s1")?.body).toEqual({ autopilot_enabled: true });
    await waitFor(() => expect(screen.getByRole("switch", { name: "Autopilot" })).toHaveAttribute("aria-checked", "true"));
  });

  it("leaves autopilot and decisions to those who may use them", async () => {
    const api = withPlan("member");
    renderApp(api.client, "/actions/s1");
    expect(await screen.findByRole("switch", { name: "Autopilot" })).toBeDisabled();
    expect(screen.getByText(/Only owners and admins can change this/)).toBeInTheDocument();

  });

  it("is read-only for viewers", async () => {
    const api = withPlan("client_viewer");
    renderApp(api.client, "/actions/s1");
    expect(await screen.findByText(/view-only access/)).toBeInTheDocument();
    await waitFor(() => expect(cards()).toHaveLength(3));
    expect(screen.getByRole("button", { name: "Re-plan" })).toBeDisabled();
    expect(screen.getAllByRole("button", { name: "Approve" })[0]).toBeDisabled();
    expect(screen.getByRole("button", { name: "Approve selected" })).toBeDisabled();
  });

  it("re-plans from the button", async () => {
    const api = withPlan();
    renderApp(api.client, "/actions/s1");
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Re-plan" }));
    expect(await screen.findByText(/Planning started/)).toBeInTheDocument();
    expect(api.calls.some((c) => c.method === "POST" && c.path === "/sites/s1/actions/replan")).toBe(true);
    expect(await screen.findByRole("button", { name: "Planning…" })).toBeDisabled();
  });

  it("downloads the CSV through the authenticated client", async () => {
    const api = withPlan();
    const createObjectURL = vi.fn((_blob: Blob) => "blob:plan");
    Object.assign(URL, { createObjectURL, revokeObjectURL: vi.fn() });
    renderApp(api.client, "/actions/s1");
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: /Export CSV/ }));
    await waitFor(() => expect(createObjectURL).toHaveBeenCalled());
    const call = api.calls.find((c) => c.path === "/sites/s1/actions/export.csv");
    expect(call?.headers["Authorization"]).toBe("Bearer access-1");
    expect(await createObjectURL.mock.calls[0]![0].text()).toContain("priority,status");
  });

  it("opens the developer handoff, offers the unapproved ones, and copies the text", async () => {
    const api = withPlan();
    renderApp(api.client, "/actions/s1");
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: /Developer handoff/ }));
    const dialog = await screen.findByRole("dialog");
    expect(await within(dialog).findByLabelText("Developer handoff text")).toHaveTextContent("Developer hand-off for Shop");
    await user.click(within(dialog).getByRole("checkbox", { name: /not approved yet/ }));
    await waitFor(() => expect(api.calls.some((c) => c.path === "/sites/s1/actions/handoff" && c.method === "GET")).toBe(true));
    await user.click(within(dialog).getByRole("button", { name: "Copy to clipboard" }));
    expect(await within(dialog).findByRole("button", { name: "Copied" })).toBeInTheDocument();
    expect(await navigator.clipboard.readText()).toContain("Developer hand-off for Shop");
  });

  it("shows an empty state that points to the audit when there is no plan", async () => {
    const api = createFakeApi({ signedIn: true });
    api.sitesByOrg.set("o1", [SITE]);
    renderApp(api.client, "/actions/s1");
    expect(await screen.findByRole("heading", { name: "No plan yet" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Go to Audit" })).toBeInTheDocument();
  });

  it("says when a site does not exist", async () => {
    const api = createFakeApi({ signedIn: true });
    renderApp(api.client, "/actions/nope");
    expect(await screen.findByText(/site was not found/)).toBeInTheDocument();
  });
});
