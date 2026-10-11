import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { createFakeApi } from "@/test/fake-api";
import { renderApp } from "@/test/render-app";

describe("LoginPage", () => {
  it("shows the server's message and stays on the page when credentials are wrong", async () => {
    const api = createFakeApi();
    renderApp(api.client, "/login");
    const user = userEvent.setup();

    await user.type(await screen.findByLabelText("Email"), "founder@example.com");
    await user.type(screen.getByLabelText("Password"), "wrong-password");
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Incorrect email or password");
    expect(screen.getByRole("heading", { name: "Welcome back" })).toBeInTheDocument();
  });

  it("signs in and lands on the sites page", async () => {
    const api = createFakeApi();
    renderApp(api.client, "/login");
    const user = userEvent.setup();

    await user.type(await screen.findByLabelText("Email"), "founder@example.com");
    await user.type(screen.getByLabelText("Password"), "correct-password");
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    expect(await screen.findByRole("heading", { name: "Sites" })).toBeInTheDocument();
    expect(screen.getByText("Acme Agency")).toBeInTheDocument();
  });

  it("redirects anonymous visitors from a protected page to /login", async () => {
    const api = createFakeApi();
    const { router } = renderApp(api.client, "/sites");
    await waitFor(() => expect(router.state.location.pathname).toBe("/login"));
    expect(await screen.findByRole("heading", { name: "Welcome back" })).toBeInTheDocument();
  });
});
