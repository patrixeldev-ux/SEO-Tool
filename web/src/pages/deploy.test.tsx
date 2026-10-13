import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import type { Site } from "@/lib/api/types";
import { createFakeApi } from "@/test/fake-api";
import { batch, connection, deployment } from "@/test/fake-deploy";
import { planAction } from "@/test/fake-actions";
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

function setup(role: "owner" | "member" = "owner") {
  const api = createFakeApi({ signedIn: true, role });
  api.sitesByOrg.set("o1", [SITE]);
  return api;
}

function ready(api: ReturnType<typeof setup>) {
  api.deploy.ownership = { ...api.deploy.ownership, verified: true };
  api.deploy.connections = [connection("cloudflare", { meta: { kill_switch: false } })];
  api.deploy.deployMethod = "cloudflare";
}

describe("Settings > Deploy", () => {
  it("tells the owner how to prove ownership and reports a failed check in plain words", async () => {
    const api = setup();
    renderApp(api.client, "/settings?tab=deploy");
    expect(await screen.findByText("searchos-verify=tok123")).toBeInTheDocument();
    expect(screen.getByText(/\.well-known\/searchos-tok123\.txt/)).toBeInTheDocument();
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Check now" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("The DNS record was not found yet.");
    expect(screen.queryByText("Verified")).not.toBeInTheDocument();

    api.deploy.proofPresent = true;
    await user.click(screen.getByRole("button", { name: "Check now" }));
    expect(await screen.findByText(/Search OS can now change this website/)).toBeInTheDocument();
    expect(screen.getByText("Verified")).toBeInTheDocument();
  });

  it("sets up Cloudflare, shows the values for the Worker and can switch everything off", async () => {
    const api = setup();
    renderApp(api.client, "/settings?tab=deploy");
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Set up Cloudflare" }));
    expect(await screen.findByText("secret-abcdef")).toBeInTheDocument();
    expect(screen.getByText("https://api.example.com/edge/rules/site-token-1")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Switch all changes off" }));
    expect(await screen.findByText(/pass through unchanged/)).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("button", { name: "Switch changes back on" })).toBeInTheDocument());
  });

  it("shows why a WordPress connection failed and refuses to choose a method that is not connected", async () => {
    const api = setup();
    api.deploy.testOk = false;
    renderApp(api.client, "/settings?tab=deploy");
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText("WordPress username"), "editor");
    await user.type(screen.getByLabelText("Application password"), "abcd efgh ijkl mnop");
    await user.click(screen.getByRole("button", { name: "Connect WordPress" }));
    expect(await screen.findByText("WordPress refused the username or password.")).toBeInTheDocument();
    expect(await screen.findByText("Needs attention")).toBeInTheDocument();
    await user.click(screen.getByRole("radio", { name: "WordPress" }));
    expect(await screen.findByText("Connect and test that deployer first.")).toBeInTheDocument();
  });

  it("chooses a connected method and sends the GitHub path rules", async () => {
    const api = setup();
    api.deploy.connections = [connection("cloudflare")];
    renderApp(api.client, "/settings?tab=deploy");
    const user = userEvent.setup();
    await user.click(await screen.findByRole("radio", { name: "Cloudflare" }));
    await waitFor(() => expect(api.deploy.deployMethod).toBe("cloudflare"));
    await user.click(screen.getByRole("radio", { name: "None" }));
    await waitFor(() => expect(api.deploy.deployMethod).toBeNull());

    await user.type(screen.getByLabelText("Repository (owner/name)"), "acme/website");
    await user.type(screen.getByLabelText("Fine-grained token"), "github_pat_0123456789abcdef");
    await user.type(screen.getByLabelText(/Where pages live/), "/blog/* -> content/{{1}.html");
    await user.click(screen.getByRole("button", { name: "Connect GitHub" }));
    expect(await screen.findByText("Connected to GitHub.")).toBeInTheDocument();
    const call = api.calls.find((c) => c.path === "/sites/s1/deploy/connections/github");
    expect(call?.body).toMatchObject({ repo: "acme/website", path_rules: [{ url: "/blog/*", file: "content/{1}.html" }] });
  });

  it("is for owners and admins only", async () => {
    renderApp(setup("member").client, "/settings?tab=deploy");
    expect(await screen.findByText(/Only owners and admins can set up deploying/)).toBeInTheDocument();
  });
});

describe("Actions: deploying", () => {
  function withApproved() {
    const api = setup();
    api.actions.actions = [planAction({ status: "approved" })];
    return api;
  }

  it("keeps Deploy approved off until the site is proven and a method is connected", async () => {
    const api = withApproved();
    renderApp(api.client, "/actions/s1");
    const button = await screen.findByRole("button", { name: /Deploy approved/ });
    await waitFor(() => expect(screen.getByText("Prove you own this site first.")).toBeInTheDocument());
    expect(button).toBeDisabled();
    expect(screen.getByRole("link", { name: "Open Settings, Deploy" })).toBeInTheDocument();
  });

  it("deploys the approved changes and shows each one's status, with a way back", async () => {
    const api = withApproved();
    ready(api);
    renderApp(api.client, "/actions/s1");
    const user = userEvent.setup();
    const button = await screen.findByRole("button", { name: "Deploy approved (1)" });
    await waitFor(() => expect(button).toBeEnabled());
    await user.click(button);
    expect(await screen.findByText(/Sent 1 change to your site/)).toBeInTheDocument();
    expect(api.calls.find((c) => c.method === "POST" && c.path === "/sites/s1/deploy")?.body).toEqual({ all_approved: true });
    const row = await screen.findByTestId("deployment-row");
    expect(within(row).getByTestId("deployment-status")).toHaveTextContent("Deploying");
    expect(within(row).getByText(/Beans to Fresh roasted coffee beans/)).toBeInTheDocument();

    await user.click(within(row).getByRole("button", { name: "Roll back" }));
    expect(await screen.findByText(/The original value is back on the page/)).toBeInTheDocument();
    await waitFor(() => expect(within(screen.getByTestId("deployment-row")).getByTestId("deployment-status")).toHaveTextContent("Rolled back"));
    expect(screen.queryByRole("button", { name: "Roll back" })).not.toBeInTheDocument();
  });

  it.each([
    ["verified", "Verified live"],
    ["failed", "Failed"],
    ["pending_merge", "Waiting for merge"],
  ] as const)("shows a %s deployment as '%s'", async (status, label) => {
    const api = withApproved();
    ready(api);
    api.deploy.deployments = [
      deployment({ status, error: status === "failed" ? "After 4 checks the new value is not in the page's HTML." : null, pr_url: status === "pending_merge" ? "https://github.com/acme/website/pull/7" : null }),
    ];
    renderApp(api.client, "/actions/s1");
    const row = await screen.findByTestId("deployment-row");
    expect(within(row).getByTestId("deployment-status")).toHaveTextContent(label);
    if (status === "failed") expect(within(row).getByText(/not in the page's HTML/)).toBeInTheDocument();
    if (status === "pending_merge") expect(within(row).getByRole("link", { name: /Pull request/ })).toHaveAttribute("href", "https://github.com/acme/website/pull/7");
  });

  it("explains skipped actions and shows the developer hand-off when a file cannot be found", async () => {
    const api = withApproved();
    ready(api);
    api.deploy.skipped = [{ action_id: "a9", reason: "The page changed since the audit." }];
    api.actions.actions = [
      planAction({ status: "approved" }),
      planAction({
        id: "a2",
        status: "needs_manual",
        generation: { state: "ok", deploy_manual: { reason: "Two files could match this page.", handoff: "Change the title in blog/post.html to: Fresh beans" } },
      }),
    ];
    renderApp(api.client, "/actions/s1");
    const user = userEvent.setup();
    const button = await screen.findByRole("button", { name: "Deploy approved (1)" });
    await waitFor(() => expect(button).toBeEnabled());
    await user.click(button);
    expect(await screen.findByText(/1 skipped: The page changed since the audit/)).toBeInTheDocument();

    await user.selectOptions(screen.getByLabelText("Status"), "needs_manual");
    expect(await screen.findByText("A developer needs to make this change.")).toBeInTheDocument();
    expect(screen.getByLabelText("Instructions for your developer")).toHaveTextContent("blog/post.html");
  });

  it("only lets owners and admins deploy", async () => {
    const api = createFakeApi({ signedIn: true, role: "member" });
    api.sitesByOrg.set("o1", [SITE]);
    api.actions.actions = [planAction({ status: "approved" })];
    renderApp(api.client, "/actions/s1");
    expect(await screen.findByText("Only owners and admins can deploy.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Deploy approved/ })).toBeDisabled();
  });

  it("lets an admin change the autopilot weekly limit", async () => {
    const api = withApproved();
    renderApp(api.client, "/actions/s1");
    const user = userEvent.setup();
    const input = await screen.findByLabelText("Most changes per week");
    expect(input).toHaveValue(20);
    expect(screen.getByText(/3 used in the last 7 days/)).toBeInTheDocument();
    await user.clear(input);
    await user.type(input, "5");
    await user.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(api.deploy.autopilot.weekly_cap).toBe(5));
  });
});

describe("Deployments history", () => {
  function withHistory() {
    const api = setup();
    ready(api);
    api.deploy.batches = [
      batch({ id: "b1", status: "verified", trigger: "autopilot" }),
      batch({ id: "b2", status: "failed", counts: { failed: 1 }, created_at: "2026-10-12T10:00:00Z" }),
    ];
    api.deploy.deployments = [
      deployment({ id: "d1", batch_id: "b1" }),
      deployment({ id: "d2", batch_id: "b2", status: "failed", can_rollback: false, error: "The change did not show up.", after_snapshot: null }),
    ];
    api.deploy.notifications = [
      { id: "n1", site_id: "s1", batch_id: "b1", kind: "autopilot_batch", title: "Autopilot made 1 change on Shop", body: "Each change is checked.", read: false, created_at: "2026-10-13T06:20:00Z" },
    ];
    return api;
  }

  it("lists batches with before and after for each change", async () => {
    const api = withHistory();
    renderApp(api.client, "/deployments/s1");
    const batches = await screen.findAllByTestId("batch");
    expect(batches).toHaveLength(2);
    expect(within(batches[0]!).getByText("Autopilot")).toBeInTheDocument();
    expect(within(batches[1]!).getByText("1 failed")).toBeInTheDocument();
    const user = userEvent.setup();
    await user.click(within(batches[0]!).getByRole("button", { name: "Show changes" }));
    const dep = await within(batches[0]!).findByTestId("batch-deployment");
    expect(within(dep).getByText("Before").parentElement).toHaveTextContent("Beans");
    expect(within(dep).getByText("After").parentElement).toHaveTextContent("Fresh roasted coffee beans for home brewing");
    await user.click(within(batches[1]!).getByRole("button", { name: "Show changes" }));
    expect(await within(batches[1]!).findByText("The change did not show up.")).toBeInTheDocument();
  });

  it("rolls back a whole batch", async () => {
    const api = withHistory();
    renderApp(api.client, "/deployments/s1");
    const user = userEvent.setup();
    const first = (await screen.findAllByTestId("batch"))[0]!;
    await user.click(within(first).getByRole("button", { name: "Roll back batch" }));
    expect(await screen.findByText("Rolled back 1 change.")).toBeInTheDocument();
    expect(api.calls.some((c) => c.method === "POST" && c.path === "/sites/s1/deploy/batches/b1/rollback")).toBe(true);
    await waitFor(() => expect(within(screen.getAllByTestId("batch")[0]!).getByText("Rolled back")).toBeInTheDocument());
  });

  it("says so when nothing was deployed yet", async () => {
    const api = setup();
    renderApp(api.client, "/deployments/s1");
    expect(await screen.findByText("Nothing deployed yet")).toBeInTheDocument();
  });

  it("shows what autopilot did and marks it read", async () => {
    const api = withHistory();
    renderApp(api.client, "/deployments");
    expect(await screen.findByText("Autopilot made 1 change on Shop")).toBeInTheDocument();
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Mark all as read" }));
    await waitFor(() => expect(screen.queryByText("1 new")).not.toBeInTheDocument());
  });
});
