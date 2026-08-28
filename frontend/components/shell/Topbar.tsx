"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useTheme } from "next-themes";
import { ChevronRight, Menu, Monitor, Moon, Sun, User } from "lucide-react";

import { cn } from "@/lib/utils";

const CRUMBS: Record<string, string> = {
  dashboard: "Dashboard",
  new: "New POC",
  projects: "Projects",
  runs: "Runs",
  settings: "Settings",
};

export default function Topbar({ onOpenNav }: { onOpenNav: () => void }) {
  const pathname = usePathname();
  const segments = pathname.split("/").filter(Boolean);

  return (
    <header className="sticky top-0 z-30 flex h-16 items-center gap-3 border-b border-[var(--line)] bg-[color-mix(in_srgb,var(--ink)_88%,transparent)] px-4 backdrop-blur-md sm:px-6">
      <button
        onClick={onOpenNav}
        className="rounded-md p-2 text-[var(--muted)] hover:bg-[var(--panel-2)] hover:text-[var(--text)] lg:hidden"
        aria-label="Open navigation"
      >
        <Menu className="h-[18px] w-[18px]" />
      </button>

      <nav aria-label="Breadcrumb" className="min-w-0 flex-1">
        <ol className="flex items-center gap-1.5 text-[13px]">
          {segments.length === 0 && (
            <li className="font-medium text-[var(--text)]">Dashboard</li>
          )}
          {segments.map((segment, index) => {
            const last = index === segments.length - 1;
            const href = "/" + segments.slice(0, index + 1).join("/");
            const label = CRUMBS[segment] ?? truncateId(segment);
            return (
              <li key={href} className="flex min-w-0 items-center gap-1.5">
                {index > 0 && (
                  <ChevronRight
                    className="h-3.5 w-3.5 shrink-0 text-[var(--muted-soft)]"
                    aria-hidden="true"
                  />
                )}
                {last ? (
                  <span
                    className={cn(
                      "truncate font-medium text-[var(--text)]",
                      !CRUMBS[segment] && "font-mono text-[12px]",
                    )}
                    aria-current="page"
                  >
                    {label}
                  </span>
                ) : (
                  <Link
                    href={href}
                    className="truncate text-[var(--muted)] transition-colors hover:text-[var(--text)]"
                  >
                    {label}
                  </Link>
                )}
              </li>
            );
          })}
        </ol>
      </nav>

      <div className="flex items-center gap-1">
        <ThemeControl />
        <ProfileMenu />
      </div>
    </header>
  );
}

function truncateId(segment: string) {
  return segment.length > 12 ? `${segment.slice(0, 8)}…` : segment;
}

/* ── Theme ───────────────────────────────────────────────────── */

const THEMES = [
  { value: "light", label: "Light", icon: Sun },
  { value: "dark", label: "Dark", icon: Moon },
  { value: "system", label: "System", icon: Monitor },
] as const;

function ThemeControl() {
  const { theme, setTheme } = useTheme();
  const [mounted, setMounted] = useState(false);

  // The server cannot know the stored preference, so the control renders inert
  // until hydration rather than flashing the wrong icon.
  useEffect(() => setMounted(true), []);

  if (!mounted) {
    return <div className="h-8 w-[92px] rounded-lg border border-[var(--line)]" aria-hidden />;
  }

  return (
    <div
      className="flex items-center gap-0.5 rounded-lg border border-[var(--line)] bg-[var(--panel)] p-0.5"
      role="group"
      aria-label="Colour theme"
    >
      {THEMES.map(({ value, label, icon: Icon }) => (
        <button
          key={value}
          onClick={() => setTheme(value)}
          aria-label={label}
          aria-pressed={theme === value}
          title={label}
          className={cn(
            "rounded-[6px] p-1.5 transition-colors",
            theme === value
              ? "bg-[var(--panel-2)] text-[var(--text)]"
              : "text-[var(--muted-soft)] hover:text-[var(--text)]",
          )}
        >
          <Icon className="h-3.5 w-3.5" strokeWidth={2} />
        </button>
      ))}
    </div>
  );
}

/* ── Profile ─────────────────────────────────────────────────── */

/**
 * There is no auth in the API, so this names the only identity that exists —
 * the local operator — and links to the settings that are genuinely settable.
 * It does not pretend to an account system the backend does not have.
 */
function ProfileMenu() {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onPointer = (event: MouseEvent) => {
      if (ref.current && !ref.current.contains(event.target as Node)) setOpen(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onPointer);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div className="relative ml-1" ref={ref}>
      <button
        onClick={() => setOpen((value) => !value)}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label="Account"
        className="flex h-8 w-8 items-center justify-center rounded-full border border-[var(--line-strong)] bg-[var(--panel-2)] text-[var(--muted)] transition-colors hover:text-[var(--text)]"
      >
        <User className="h-3.5 w-3.5" strokeWidth={2} />
      </button>

      {open && (
        <div
          role="menu"
          className="absolute right-0 top-10 w-56 overflow-hidden rounded-xl border border-[var(--line-strong)] bg-[var(--panel)] shadow-[var(--shadow-card)]"
        >
          <div className="border-b border-[var(--line)] px-3.5 py-3">
            <p className="text-[13px] font-medium text-[var(--text)]">Local operator</p>
            <p className="mt-0.5 font-mono text-[10.5px] text-[var(--muted-soft)]">
              Single-user console
            </p>
          </div>
          <Link
            href="/settings"
            role="menuitem"
            onClick={() => setOpen(false)}
            className="block px-3.5 py-2.5 text-[13px] text-[var(--muted)] transition-colors hover:bg-[var(--panel-2)] hover:text-[var(--text)]"
          >
            Settings
          </Link>
        </div>
      )}
    </div>
  );
}
