import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { createFakeApi } from "@/test/fake-api";
import { renderApp } from "@/test/render-app";

describe("Settings > AI", () => {
  it("opens from the tab and says where keys belong when none is set", async () => {
    const api = createFakeApi({ signedIn: true });
    renderApp(api.client, "/settings");
    const user = userEvent.setup();
    await user.click(await screen.findByRole("tab", { name: "AI" }));
    expect(await screen.findByText("Not set up")).toBeInTheDocument();
    expect(screen.getByText(/Keys are set in the/)).toBeInTheDocument();
    const keys = screen.getByRole("list", { name: "Keys found in the server settings" });
    expect(within(keys).getAllByText("not set")).toHaveLength(3);
  });

  it("opens directly from the banner link", async () => {
    const api = createFakeApi({ signedIn: true });
    renderApp(api.client, "/settings?tab=ai");
    expect(await screen.findByText("AI provider")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "AI" })).toHaveAttribute("aria-selected", "true");
  });

  it("shows which provider is in use and only whether keys are present", async () => {
    const api = createFakeApi({ signedIn: true });
    api.actions.ai = { ...api.actions.ai, configured: true, provider: "anthropic", model: "claude-sonnet-5-5", selected_by: "key present", anthropic_key_present: true, tokens_used_this_month: 500_000, usage_by_purpose: [{ purpose: "rewrite_title", calls: 12, tokens: 500_000 }] };
    const { container } = renderApp(api.client, "/settings?tab=ai");
    expect(await screen.findByText("Ready")).toBeInTheDocument();
    expect(screen.getByText("claude-sonnet-5-5")).toBeInTheDocument();
    expect(screen.getByText("present")).toBeInTheDocument();
    expect(screen.getByRole("progressbar", { name: "Tokens used this month" })).toHaveAttribute("aria-valuenow", "25");
    expect(screen.getByText("rewrite title")).toBeInTheDocument();
    expect(container.querySelector("input[type=password]")).toBeNull();
    expect(container.textContent).not.toMatch(/sk-/);
  });

  it("lets an admin change the monthly budget and go back to the default", async () => {
    const api = createFakeApi({ signedIn: true });
    renderApp(api.client, "/settings?tab=ai");
    const user = userEvent.setup();
    const input = await screen.findByLabelText("Monthly token budget");
    await user.clear(input);
    await user.type(input, "50000");
    await user.click(screen.getByRole("button", { name: "Save budget" }));
    await waitFor(() => expect(api.calls.find((c) => c.method === "PUT" && c.path === "/ai/budget")?.body).toEqual({ monthly_token_budget: 50000 }));
    await user.click(await screen.findByRole("button", { name: "Use the default" }));
    await waitFor(() => expect(api.actions.ai.budget_is_custom).toBe(false));
  });

  it("rejects a budget that is not a whole number", async () => {
    const api = createFakeApi({ signedIn: true });
    renderApp(api.client, "/settings?tab=ai");
    const user = userEvent.setup();
    const input = await screen.findByLabelText("Monthly token budget");
    await user.clear(input);
    await user.type(input, "lots");
    expect(screen.getByRole("button", { name: "Save budget" })).toBeDisabled();
  });

  it("hides the budget form from members", async () => {
    const api = createFakeApi({ signedIn: true, role: "member" });
    renderApp(api.client, "/settings?tab=ai");
    expect(await screen.findByText(/Only owners and admins can change the budget/)).toBeInTheDocument();
    expect(screen.queryByLabelText("Monthly token budget")).not.toBeInTheDocument();
  });

  it("keeps the Google integration on the General tab", async () => {
    const api = createFakeApi({ signedIn: true });
    renderApp(api.client, "/settings");
    expect(await screen.findByText("Account")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "General" })).toHaveAttribute("aria-selected", "true");
  });
});
