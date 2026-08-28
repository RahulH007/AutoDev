"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  BookOpen,
  FolderGit2,
  Home,
  LayoutDashboard,
  ListOrdered,
  Mail,
  Plus,
  Settings,
  X,
} from "lucide-react";

import { api, ApiError, API_URL } from "@/lib/api";
import { cn } from "@/lib/utils";

const NAV = [
  { href: "/dashboard", label: "Dashboard", icon: LayoutDashboard },
  { href: "/new", label: "New POC", icon: Plus, emphasis: true },
  { href: "/projects", label: "Projects", icon: FolderGit2 },
  { href: "/runs", label: "Runs", icon: ListOrdered },
  { href: "/settings", label: "Settings", icon: Settings },
];

/**
 * The pages outside the console.
 *
 * Splitting the routes into console and marketing groups took the top navbar
 * off these pages, which made /dashboard a one-way door: nothing linked back to
 * Home, the docs, or contact. They live here rather than in NAV because they
 * leave the console, and the quieter styling says so.
 */
const SITE_NAV = [
  { href: "/", label: "Home", icon: Home },
  { href: "/docs", label: "Documentation", icon: BookOpen },
  { href: "/contact", label: "Contact", icon: Mail },
];

export default function Sidebar({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  const pathname = usePathname();

  // Navigating on mobile should put the drawer away.
  useEffect(() => {
    onClose();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pathname]);

  return (
    <>
      {open && (
        <div
          className="fixed inset-0 z-40 bg-black/50 backdrop-blur-sm lg:hidden"
          onClick={onClose}
          aria-hidden="true"
        />
      )}

      <aside
        className={cn(
          "fixed inset-y-0 left-0 z-50 flex w-[248px] flex-col border-r border-[var(--line)] bg-[var(--panel)]",
          "transition-transform duration-200 ease-out lg:translate-x-0",
          open ? "translate-x-0" : "-translate-x-full",
        )}
        aria-label="Primary"
      >
        <div className="flex h-16 items-center justify-between border-b border-[var(--line)] px-5">
          <Link href="/dashboard" className="flex items-center gap-2.5">
            <Mark />
            <span className="font-display text-[15px] font-bold tracking-tightest text-[var(--text)]">
              AgentForge
            </span>
          </Link>
          <button
            onClick={onClose}
            className="rounded-md p-1.5 text-[var(--muted)] hover:bg-[var(--panel-2)] hover:text-[var(--text)] lg:hidden"
            aria-label="Close navigation"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <nav className="flex-1 overflow-y-auto px-3 py-4">
          <ul className="space-y-0.5">
            {NAV.map(({ href, label, icon: Icon, emphasis }) => {
              const active = isActiveHref(pathname, href);
              return (
                <li key={href}>
                  <Link
                    href={href}
                    aria-current={active ? "page" : undefined}
                    className={cn(
                      "group relative flex items-center gap-3 rounded-lg px-3 py-2 text-[13.5px] transition-colors",
                      active
                        ? "bg-[var(--panel-2)] font-medium text-[var(--text)]"
                        : "text-[var(--muted)] hover:bg-[var(--panel-2)] hover:text-[var(--text)]",
                    )}
                  >
                    {active && (
                      <span className="absolute left-0 top-1/2 h-4 w-[2px] -translate-y-1/2 rounded-r bg-[var(--text)]" />
                    )}
                    <Icon
                      className={cn(
                        "h-[15px] w-[15px] shrink-0",
                        emphasis && !active && "text-[var(--text)]",
                      )}
                      strokeWidth={active || emphasis ? 2.1 : 1.8}
                    />
                    {label}
                  </Link>
                </li>
              );
            })}
          </ul>

          <ul className="mt-4 space-y-0.5 border-t border-[var(--line)] pt-4">
            {SITE_NAV.map(({ href, label, icon: Icon }) => {
              const active = href === "/" ? pathname === "/" : isActiveHref(pathname, href);
              return (
                <li key={href}>
                  <Link
                    href={href}
                    aria-current={active ? "page" : undefined}
                    className={cn(
                      "flex items-center gap-3 rounded-lg px-3 py-1.5 text-[12.5px] transition-colors",
                      active
                        ? "font-medium text-[var(--text)]"
                        : "text-[var(--muted-soft)] hover:bg-[var(--panel-2)] hover:text-[var(--text)]",
                    )}
                  >
                    <Icon className="h-[13px] w-[13px] shrink-0" strokeWidth={1.8} />
                    {label}
                  </Link>
                </li>
              );
            })}
          </ul>
        </nav>

        <ApiStatus />
      </aside>
    </>
  );
}

function isActiveHref(pathname: string, href: string) {
  return pathname === href || pathname.startsWith(href + "/");
}

/** The AgentForge mark: six stacked rules, one per pipeline stage. */
function Mark() {
  return (
    <span
      className="flex h-7 w-7 flex-col items-center justify-center gap-[2.5px] rounded-md bg-[var(--invert-bg)]"
      aria-hidden="true"
    >
      {[3, 5, 7, 9, 7, 4].map((width, index) => (
        <span
          key={index}
          className="h-[1.5px] rounded-full bg-[var(--invert-text)]"
          style={{ width: `${width * 1.1}px`, opacity: 0.35 + index * 0.13 }}
        />
      ))}
    </span>
  );
}

/**
 * Live API reachability, from `/api/health`.
 *
 * The console is useless without the server and the failure is silent
 * otherwise, so the connection state is permanent furniture rather than a
 * toast that arrives once and disappears.
 */
function ApiStatus() {
  const [state, setState] = useState<"checking" | "up" | "down">("checking");
  const [runs, setRuns] = useState<number | null>(null);

  useEffect(() => {
    let cancelled = false;

    const check = async () => {
      try {
        const health = await api.health();
        if (cancelled) return;
        setState("up");
        setRuns(health.runs);
      } catch (caught) {
        if (cancelled) return;
        setState("down");
        if (!(caught instanceof ApiError)) setRuns(null);
      }
    };

    check();
    const timer = setInterval(check, 15_000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, []);

  const host = API_URL.replace(/^https?:\/\//, "");

  return (
    <div className="border-t border-[var(--line)] px-5 py-3.5">
      <div className="flex items-center gap-2">
        <span
          className={cn(
            "h-1.5 w-1.5 shrink-0 rounded-full",
            state === "up" && "bg-[var(--text)]",
            state === "down" && "border border-[var(--text)] bg-transparent",
            state === "checking" && "animate-pulse bg-[var(--muted-soft)]",
          )}
        />
        <span className="font-mono text-[10.5px] uppercase tracking-[0.1em] text-[var(--muted)]">
          {state === "up" ? "API connected" : state === "down" ? "API offline" : "Checking"}
        </span>
      </div>
      <p className="mt-1 truncate font-mono text-[10.5px] text-[var(--muted-soft)]" title={API_URL}>
        {state === "down" ? "python scripts/dev.py" : host}
        {state === "up" && runs !== null && ` · ${runs} run${runs === 1 ? "" : "s"}`}
      </p>
    </div>
  );
}
