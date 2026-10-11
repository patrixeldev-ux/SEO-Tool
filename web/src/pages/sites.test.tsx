import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { createFakeApi } from "@/test/fake-api";
import { renderApp } from "@/test/render-app";

describe("register -> create site flow", () => {
  it("registers, sees the empty state, adds a site and sees it listed with a normalised domain", async () => {
    const api = createFakeApi();
    renderApp(api.client, "/register");
    const user = userEvent.setup();

    await user.type(await screen.findByLabelText("Organisation name"), "Patrixel");
    await user.type(screen.getByLabelText("Email"), "founder@example.com");
    await user.type(screen.getByLabelText("Password"), "a-long-password");
    await user.click(screen.getByRole("button", { name: "Create account" }));

    expect(await screen.findByRole("heading", { name: "No sites yet" })).toBeInTheDocument();
    expect(screen.getAllByText("Patrixel").length).toBeGreaterThan(0);

    await user.click(screen.getByRole("button", { name: "Add your first site" }));
    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText("Domain"), "https://www.Example.com/blog");
    await user.click(within(dialog).getByRole("button", { name: "Add site" }));

    const list = await screen.findByRole("list", { name: "Sites" });
    expect(within(list).getByText("www.example.com")).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("shows API validation errors inside the dialog", async () => {
    const api = createFakeApi({ signedIn: true });
    renderApp(api.client, "/sites");
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: "Add your first site" }));
    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText("Domain"), "localhost");
    await user.click(within(dialog).getByRole("button", { name: "Add site" }));

    expect(await within(dialog).findByRole("alert")).toHaveTextContent("top-level domain");
  });

  it("sends the active organisation with every request and refetches after switching", async () => {
    const api = createFakeApi({ signedIn: true });
    api.sitesByOrg.set("o1", [
      { id: "s1", org_id: "o1", domain: "first.example.com", uses_www: false, display_name: "First", settings: {}, autopilot_enabled: false, created_at: "2026-10-11T10:00:00Z" },
    ]);
    renderApp(api.client, "/sites");
    const user = userEvent.setup();

    expect(await screen.findByText("First")).toBeInTheDocument();
    const firstListCall = api.calls.find((c) => c.path === "/sites");
    expect(firstListCall?.headers["X-Org-Id"]).toBe("o1");

    // create a second org through the switcher
    await user.click(screen.getByRole("button", { name: "Switch organisation" }));
    await user.click(await screen.findByRole("menuitem", { name: /New organisation/ }));
    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText("Name"), "Client Co");
    await user.click(within(dialog).getByRole("button", { name: "Create organisation" }));

    expect(await screen.findByRole("heading", { name: "No sites yet" })).toBeInTheDocument();
    expect(screen.queryByText("First")).not.toBeInTheDocument();
    const lastListCall = [...api.calls].reverse().find((c) => c.path === "/sites" && c.method === "GET");
    expect(lastListCall?.headers["X-Org-Id"]).toBe("o2");
  });
});
