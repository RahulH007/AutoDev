import Link from "next/link";
import type { ComponentProps, ReactNode } from "react";

import { cn } from "@/lib/utils";

/* ── Surface ─────────────────────────────────────────────────── */

export function Panel({
  className,
  children,
  ...rest
}: ComponentProps<"div">) {
  return (
    <div
      className={cn(
        "rounded-xl border border-[var(--line)] bg-[var(--panel)]",
        className,
      )}
      {...rest}
    >
      {children}
    </div>
  );
}

/* ── Section heading ─────────────────────────────────────────── */

export function SectionHeader({
  title,
  count,
  action,
  className,
}: {
  title: string;
  /** Shown beside the title when there is something to count. */
  count?: number;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("mb-3 flex items-end justify-between gap-4", className)}>
      <h2 className="flex items-baseline gap-2.5">
        <span className="font-display text-[13px] font-semibold uppercase tracking-[0.14em] text-[var(--muted)]">
          {title}
        </span>
        {typeof count === "number" && (
          <span className="font-mono text-[11px] text-[var(--muted-soft)]">
            {count}
          </span>
        )}
      </h2>
      {action}
    </div>
  );
}

/* ── Buttons ─────────────────────────────────────────────────── */

type Variant = "primary" | "secondary" | "ghost";

const VARIANT: Record<Variant, string> = {
  // The one inversion. Nothing else on a screen may be this loud.
  primary:
    "bg-[var(--invert-bg)] text-[var(--invert-text)] hover:opacity-90 border border-transparent",
  secondary:
    "bg-[var(--panel)] text-[var(--text)] border border-[var(--line-strong)] hover:bg-[var(--panel-2)]",
  ghost:
    "bg-transparent text-[var(--muted)] border border-transparent hover:bg-[var(--panel-2)] hover:text-[var(--text)]",
};

const BASE =
  "inline-flex items-center justify-center gap-2 rounded-lg px-4 py-2 text-sm font-medium transition-all duration-150 disabled:cursor-not-allowed disabled:opacity-45";

export function Button({
  variant = "secondary",
  className,
  ...rest
}: ComponentProps<"button"> & { variant?: Variant }) {
  return <button className={cn(BASE, VARIANT[variant], className)} {...rest} />;
}

export function ButtonLink({
  variant = "secondary",
  className,
  ...rest
}: ComponentProps<typeof Link> & { variant?: Variant }) {
  return <Link className={cn(BASE, VARIANT[variant], className)} {...rest} />;
}

/* ── Level chip ──────────────────────────────────────────────── */

/**
 * Severity and priority without hue: loud levels are filled, middling ones
 * outlined, low ones bare text. The same fill-versus-outline logic the status
 * marks use, so one reading habit covers both.
 */
export function LevelChip({ level, className }: { level: string; className?: string }) {
  const value = (level ?? "").trim().toLowerCase();
  const loud = value === "critical" || value === "high" || value === "blocker";
  const middling = value === "medium" || value === "moderate";

  return (
    <span
      className={cn(
        "shrink-0 rounded px-1.5 py-0.5 font-mono text-[9.5px] uppercase tracking-[0.08em]",
        loud
          ? "bg-[var(--invert-bg)] text-[var(--invert-text)]"
          : middling
            ? "border border-[var(--line-strong)] text-[var(--text)]"
            : "text-[var(--muted-soft)]",
        className,
      )}
    >
      {level?.trim() || "—"}
    </span>
  );
}

/* ── Empty state ─────────────────────────────────────────────── */

export function EmptyState({
  title,
  body,
  action,
  className,
}: {
  title: string;
  body: string;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex flex-col items-start gap-3 rounded-xl border border-dashed border-[var(--line-strong)] px-6 py-8",
        className,
      )}
    >
      <div>
        <p className="font-display text-[15px] font-semibold text-[var(--text)]">{title}</p>
        <p className="mt-1 max-w-md text-[13.5px] leading-relaxed text-[var(--muted)]">
          {body}
        </p>
      </div>
      {action}
    </div>
  );
}

/* ── Loading placeholder ─────────────────────────────────────── */

export function SkeletonRows({ rows = 3 }: { rows?: number }) {
  return (
    <div className="space-y-2" aria-hidden="true">
      {Array.from({ length: rows }).map((_, index) => (
        <div
          key={index}
          className="h-[68px] animate-pulse rounded-xl border border-[var(--line)] bg-[var(--panel-2)]"
          style={{ animationDelay: `${index * 90}ms` }}
        />
      ))}
    </div>
  );
}
