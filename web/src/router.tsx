import { createRootRoute, createRoute, createRouter, Outlet, redirect } from "@tanstack/react-router";

import { AppShell } from "@/components/layout/app-shell";
import { AuditIndexPage, SiteAuditPage } from "@/pages/audit";
import { ComingSoonPage } from "@/pages/coming-soon";
import { GoogleSetupPage } from "@/pages/help";
import { LoginPage } from "@/pages/login";
import { RegisterPage } from "@/pages/register";
import { SearchConsoleIndexPage, SiteSearchPage } from "@/pages/search-console";
import { SettingsPage } from "@/pages/settings";
import { SitesPage } from "@/pages/sites";

const rootRoute = createRootRoute({ component: Outlet });

const indexRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/",
  beforeLoad: () => {
    throw redirect({ to: "/sites" });
  },
});
const loginRoute = createRoute({ getParentRoute: () => rootRoute, path: "/login", component: LoginPage });
const registerRoute = createRoute({ getParentRoute: () => rootRoute, path: "/register", component: RegisterPage });

// Pathless layout route: everything inside requires a session.
const appRoute = createRoute({ getParentRoute: () => rootRoute, id: "app", component: AppShell });

const sitesRoute = createRoute({ getParentRoute: () => appRoute, path: "/sites", component: SitesPage });
const auditIndexRoute = createRoute({ getParentRoute: () => appRoute, path: "/audit", component: AuditIndexPage });
const siteAuditRoute = createRoute({ getParentRoute: () => appRoute, path: "/audit/$siteId", component: SiteAuditPage });
const searchIndexRoute = createRoute({ getParentRoute: () => appRoute, path: "/search-console", component: SearchConsoleIndexPage });
const siteSearchRoute = createRoute({ getParentRoute: () => appRoute, path: "/search-console/$siteId", component: SiteSearchPage });
const googleSetupRoute = createRoute({ getParentRoute: () => appRoute, path: "/help/google-setup", component: GoogleSetupPage });
const settingsRoute = createRoute({ getParentRoute: () => appRoute, path: "/settings", component: SettingsPage });

function placeholder(path: string, title: string, description: string, phase: string) {
  return createRoute({
    getParentRoute: () => appRoute,
    path,
    component: () => <ComingSoonPage title={title} description={description} phase={phase} />,
  });
}

const placeholderRoutes = [
  placeholder("/ai-visibility", "AI Visibility", "How often AI answer engines mention and cite your brand, with confidence intervals.", "Phase 6"),
  placeholder("/actions", "Actions", "The prioritised plan of fixes, with approve, edit and reject.", "Phase 3"),
  placeholder("/experiments", "Experiments", "Did each change actually help? Treated pages versus matched controls.", "Phase 5"),
  placeholder("/content", "Content", "Briefs, drafts and refreshes backed by SERP research.", "Phase 7"),
  placeholder("/reports", "Reports", "White-label monthly reports for you and your clients.", "Phase 8"),
];

export const routeTree = rootRoute.addChildren([
  indexRoute,
  loginRoute,
  registerRoute,
  appRoute.addChildren([
    sitesRoute,
    auditIndexRoute,
    siteAuditRoute,
    searchIndexRoute,
    siteSearchRoute,
    googleSetupRoute,
    settingsRoute,
    ...placeholderRoutes,
  ]),
]);

export const router = createRouter({ routeTree });

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}
