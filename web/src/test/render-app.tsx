import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createMemoryHistory, createRouter, RouterProvider } from "@tanstack/react-router";
import { render } from "@testing-library/react";

import type { ApiClient } from "@/lib/api/client";
import { AuthProvider } from "@/lib/auth";
import { routeTree } from "@/router";
import { ThemeProvider } from "@/lib/theme";

/** Renders the real route tree against an injected API client, starting at `path`. */
export function renderApp(client: ApiClient, path = "/") {
  const router = createRouter({ routeTree, history: createMemoryHistory({ initialEntries: [path] }) });
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const utils = render(
    <ThemeProvider>
      <QueryClientProvider client={queryClient}>
        <AuthProvider client={client}>
          <RouterProvider router={router} />
        </AuthProvider>
      </QueryClientProvider>
    </ThemeProvider>,
  );
  return { ...utils, router };
}
