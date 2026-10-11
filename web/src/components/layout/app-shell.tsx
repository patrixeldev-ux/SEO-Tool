import { Link, Navigate, Outlet } from "@tanstack/react-router";
import { LogOut, Menu, Search } from "lucide-react";
import { useState } from "react";

import { NAV_SECTIONS, SETTINGS_ITEM, type NavItem } from "@/components/layout/nav";
import { OrgSwitcher } from "@/components/layout/org-switcher";
import { ThemeToggle } from "@/components/layout/theme-toggle";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useAuth } from "@/lib/auth";

function Brand() {
  return (
    <div className="flex items-center gap-2.5 px-1">
      <span className="flex size-8 items-center justify-center rounded-lg bg-primary text-primary-foreground">
        <Search className="size-4" strokeWidth={2.5} />
      </span>
      <span className="text-[0.95rem] font-semibold tracking-tight">Search OS</span>
    </div>
  );
}

function NavLink({ item, onNavigate }: { item: NavItem; onNavigate: () => void }) {
  const Icon = item.icon;
  return (
    <Link
      to={item.to}
      onClick={onNavigate}
      className="flex items-center gap-2.5 rounded-md px-2.5 py-1.5 text-sm font-medium text-muted-foreground transition-colors hover:bg-accent hover:text-accent-foreground"
      activeProps={{ className: "bg-accent text-accent-foreground", "aria-current": "page" }}
    >
      <Icon className="size-4" />
      {item.label}
    </Link>
  );
}

function Sidebar({ onNavigate }: { onNavigate: () => void }) {
  const { session, logout } = useAuth();
  const email = session.status === "authenticated" ? session.user.email : "";
  return (
    <div className="flex h-full flex-col gap-4 bg-sidebar p-3 text-sidebar-foreground">
      <div className="pt-1">
        <Brand />
      </div>
      <OrgSwitcher />
      <nav aria-label="Main" className="flex-1 space-y-4 overflow-y-auto">
        {NAV_SECTIONS.map((section) => (
          <div key={section.title} className="space-y-0.5">
            <p className="px-2.5 pb-1 text-[0.6875rem] font-semibold tracking-wider text-muted-foreground uppercase">
              {section.title}
            </p>
            {section.items.map((item) => (
              <NavLink key={item.to} item={item} onNavigate={onNavigate} />
            ))}
          </div>
        ))}
      </nav>
      <div className="space-y-1 border-t border-sidebar-border pt-3">
        <NavLink item={SETTINGS_ITEM} onNavigate={onNavigate} />
        <div className="flex items-center gap-1 px-1 pt-1">
          <p className="min-w-0 flex-1 truncate px-1.5 text-xs text-muted-foreground" title={email}>
            {email}
          </p>
          <ThemeToggle />
          <Button variant="ghost" size="icon" aria-label="Sign out" title="Sign out" onClick={() => void logout()}>
            <LogOut />
          </Button>
        </div>
      </div>
    </div>
  );
}

function FullScreenSkeleton() {
  return (
    <div className="flex h-screen" aria-busy="true" aria-label="Loading">
      <div className="hidden w-64 space-y-4 border-r bg-sidebar p-4 md:block">
        <Skeleton className="h-8 w-32" />
        <Skeleton className="h-12 w-full" />
        <Skeleton className="h-6 w-full" />
        <Skeleton className="h-6 w-full" />
      </div>
      <div className="flex-1 space-y-4 p-8">
        <Skeleton className="h-8 w-48" />
        <Skeleton className="h-40 w-full max-w-3xl" />
      </div>
    </div>
  );
}

/** Authenticated layout: redirects anonymous visitors to /login. */
export function AppShell() {
  const { session } = useAuth();
  const [mobileOpen, setMobileOpen] = useState(false);

  if (session.status === "loading") return <FullScreenSkeleton />;
  if (session.status === "anonymous") return <Navigate to="/login" />;

  return (
    <div className="flex h-screen overflow-hidden">
      <aside className="hidden w-64 shrink-0 border-r border-sidebar-border md:block">
        <Sidebar onNavigate={() => undefined} />
      </aside>

      {/* Mobile drawer: only mounted while open, so there is never a second (hidden) copy of the sidebar. */}
      {mobileOpen && (
        <div className="fixed inset-0 z-40 md:hidden">
          <button
            type="button"
            aria-label="Close menu"
            className="absolute inset-0 bg-black/50"
            onClick={() => setMobileOpen(false)}
          />
          <aside className="absolute inset-y-0 left-0 w-64 border-r border-sidebar-border shadow-xl">
            <Sidebar onNavigate={() => setMobileOpen(false)} />
          </aside>
        </div>
      )}

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-14 items-center gap-2 border-b bg-card px-4 md:hidden">
          <Button variant="ghost" size="icon" aria-label="Open menu" onClick={() => setMobileOpen(true)}>
            <Menu />
          </Button>
          <Brand />
        </header>
        <main className="flex-1 overflow-y-auto">
          <div className="mx-auto w-full max-w-6xl px-4 py-6 md:px-8 md:py-8">
            <Outlet />
          </div>
        </main>
      </div>
    </div>
  );
}
