import { useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";

import { ErrorBoundary } from "@/components/ErrorBoundary";
import { Badge, Button } from "@/components/ui";
import { cx } from "@/lib/cx";
import { useAuth } from "@/hooks/useAuth";
import { useTheme } from "@/hooks/useTheme";
import type { Theme } from "@/hooks/useTheme";

const NAV = [
  { to: "/", label: "Overview", end: true },
  { to: "/batches", label: "Pipelines & Batches" },
  { to: "/incidents", label: "Incidents" },
  { to: "/quarantine", label: "Quarantine" },
  { to: "/recovery", label: "Recovery Center" },
  { to: "/lineage", label: "Lineage Explorer" },
  { to: "/failure-lab", label: "Failure Lab" },
  { to: "/rules", label: "Quality Rules" },
  { to: "/audit", label: "Audit & Settings" },
];

const THEMES: Theme[] = ["system", "light", "dark"];

export function Layout() {
  const { user, logout } = useAuth();
  const [theme, setTheme] = useTheme();
  const [menuOpen, setMenuOpen] = useState(false);
  const location = useLocation();

  const nav = (
    <nav aria-label="Main" className="flex flex-col gap-0.5 p-3">
      {NAV.map((item) => (
        <NavLink
          key={item.to}
          to={item.to}
          end={item.end}
          onClick={() => setMenuOpen(false)}
          className={({ isActive }) =>
            cx(
              "rounded-md px-3 py-2 text-sm font-medium",
              isActive
                ? "bg-teal-50 text-teal-800 dark:bg-teal-950 dark:text-teal-200"
                : "text-slate-600 hover:bg-slate-100 hover:text-slate-900 dark:text-slate-400 dark:hover:bg-slate-800 dark:hover:text-slate-100",
            )
          }
        >
          {item.label}
        </NavLink>
      ))}
    </nav>
  );

  return (
    <div className="min-h-screen lg:flex">
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:absolute focus:left-2 focus:top-2 focus:z-50 focus:rounded focus:bg-white focus:p-2"
      >
        Skip to content
      </a>
      <aside className="hidden w-60 shrink-0 border-r border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900 lg:block">
        <div className="flex h-14 items-center gap-2 border-b border-slate-200 px-4 dark:border-slate-800">
          <img src="/favicon.svg" alt="" className="h-6 w-6" />
          <span className="font-semibold tracking-tight">DataRescue</span>
        </div>
        {nav}
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-14 items-center justify-between gap-3 border-b border-slate-200 bg-white px-4 dark:border-slate-800 dark:bg-slate-900">
          <Button
            variant="ghost"
            size="sm"
            className="lg:hidden"
            aria-expanded={menuOpen}
            aria-controls="mobile-nav"
            onClick={() => setMenuOpen((v) => !v)}
          >
            Menu
          </Button>
          <div className="hidden text-sm text-slate-500 lg:block">
            Data reliability control plane · times in UTC
          </div>
          <div className="flex items-center gap-2">
            <label className="sr-only" htmlFor="theme">
              Theme
            </label>
            <select
              id="theme"
              value={theme}
              onChange={(e) => setTheme(e.target.value as Theme)}
              className="rounded-md border border-slate-300 bg-white px-2 py-1 text-xs dark:border-slate-700 dark:bg-slate-900"
            >
              {THEMES.map((t) => (
                <option key={t} value={t}>
                  {t} theme
                </option>
              ))}
            </select>
            {user && (
              <span className="hidden items-center gap-2 text-sm sm:flex">
                <span className="text-slate-600 dark:text-slate-300">{user.email}</span>
                <Badge tone="blue">{user.role}</Badge>
              </span>
            )}
            <Button size="sm" onClick={logout}>
              Sign out
            </Button>
          </div>
        </header>
        {menuOpen && (
          <div id="mobile-nav" className="border-b border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900 lg:hidden">
            {nav}
          </div>
        )}
        <main id="main" className="mx-auto w-full max-w-7xl flex-1 px-4 py-6 sm:px-6">
          <ErrorBoundary key={location.pathname}>
            <Outlet />
          </ErrorBoundary>
        </main>
      </div>
    </div>
  );
}
