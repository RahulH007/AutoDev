"use client";

import { useEffect, useState } from "react";
import { useTheme } from "next-themes";
import { Check, Monitor, Moon, Sun } from "lucide-react";

import { Panel } from "@/components/ui/Primitives";
import { API_URL, ApiError, api } from "@/lib/api";
import { cn } from "@/lib/utils";

/**
 * Only what is genuinely settable from here.
 *
 * The API exposes no settings endpoint, so this page does not pretend to write
 * one. Theme is a real local preference; the API address and health are real
 * facts about this browser's connection. Everything that shapes a run — models,
 * token budget, retries, verification — is server configuration, read only by
 * `core/config.py` from the server's own environment, so this page says where
 * those live rather than offering controls that would not do anything.
 */
export default function SettingsPage() {
  return (
    <div className="max-w-2xl space-y-8">
      <header>
        <h1 className="font-display text-[26px] font-bold tracking-tightest text-[var(--text)]">
          Settings
        </h1>
        <p className="mt-1.5 text-[13.5px] text-[var(--muted)]">
          Preferences for this browser, and where everything else is configured.
        </p>
      </header>

      <Appearance />
      <Connection />
      <ServerConfig />
    </div>
  );
}

/* ── Appearance ──────────────────────────────────────────────── */

const THEMES = [
  { value: "light", label: "Light", icon: Sun },
  { value: "dark", label: "Dark", icon: Moon },
  { value: "system", label: "System", icon: Monitor },
] as const;

function Appearance() {
  const { theme, setTheme } = useTheme();
  const [mounted, setMounted] = useState(false);

  useEffect(() => setMounted(true), []);

  return (
    <section>
      <SettingHeading title="Appearance" hint="Stored in this browser only." />
      <div className="grid gap-2 sm:grid-cols-3">
        {THEMES.map(({ value, label, icon: Icon }) => {
          const active = mounted && theme === value;
          return (
            <button
              key={value}
              onClick={() => setTheme(value)}
              aria-pressed={active}
              className={cn(
                "flex items-center gap-3 rounded-xl border px-4 py-3 text-left transition-colors",
                active
                  ? "border-[var(--text)] bg-[var(--panel-2)]"
                  : "border-[var(--line)] bg-[var(--panel)] hover:border-[var(--line-strong)]",
              )}
            >
              <Icon className="h-4 w-4 shrink-0 text-[var(--text)]" strokeWidth={1.9} />
              <span className="flex-1 text-[13.5px] font-medium text-[var(--text)]">{label}</span>
              {active && <Check className="h-3.5 w-3.5 shrink-0 text-[var(--text)]" />}
            </button>
          );
        })}
      </div>
    </section>
  );
}

/* ── Connection ──────────────────────────────────────────────── */

function Connection() {
  const [state, setState] = useState<"checking" | "up" | "down">("checking");
  const [detail, setDetail] = useState<string>("");

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const health = await api.health();
        if (cancelled) return;
        setState("up");
        setDetail(`${health.runs} run${health.runs === 1 ? "" : "s"} recorded`);
      } catch (caught) {
        if (cancelled) return;
        setState("down");
        setDetail(caught instanceof ApiError ? caught.message : String(caught));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <section>
      <SettingHeading
        title="API connection"
        hint="Set with NEXT_PUBLIC_API_URL at build time."
      />
      <Panel className="divide-y divide-[var(--line)]">
        <Row label="Address" value={API_URL} mono />
        <Row
          label="Status"
          value={
            state === "checking"
              ? "Checking…"
              : state === "up"
                ? `Connected — ${detail}`
                : "Offline"
          }
        />
        {state === "down" && (
          <div className="px-4 py-3">
            <p className="text-[12.5px] leading-relaxed text-[var(--muted)]">{detail}</p>
            <p className="mt-2 font-mono text-[11.5px] text-[var(--text)]">
              python scripts/dev.py
            </p>
          </div>
        )}
      </Panel>
    </section>
  );
}

/* ── Server configuration ────────────────────────────────────── */

const SERVER_SETTINGS = [
  { key: "LLM_PROVIDER", what: "Which provider runs the agents" },
  { key: "MODEL_HEAVY / MODEL_STRUCTURED", what: "Models for code generation and structured output" },
  { key: "LLM_TOKENS_PER_MINUTE", what: "Token ceiling the pipeline paces itself under" },
  { key: "MAX_DEVELOPER_RETRIES", what: "Attempts before a run gives up" },
  { key: "MIN_QUALITY_SCORE", what: "Lowest QA score that still passes" },
];

function ServerConfig() {
  return (
    <section>
      <SettingHeading
        title="Pipeline configuration"
        hint="Read from the server environment, not editable here."
      />
      <Panel className="divide-y divide-[var(--line)]">
        {SERVER_SETTINGS.map(({ key, what }) => (
          <div key={key} className="flex flex-wrap items-baseline gap-x-4 gap-y-1 px-4 py-3">
            <span className="font-mono text-[11.5px] text-[var(--text)]">{key}</span>
            <span className="text-[12.5px] text-[var(--muted)]">{what}</span>
          </div>
        ))}
      </Panel>
      <p className="mt-2.5 text-[12.5px] leading-relaxed text-[var(--muted)]">
        Change these in the server&rsquo;s <span className="font-mono text-[11.5px]">.env</span>{" "}
        and restart it. They are deliberately not editable from the browser — a run in flight
        reads them, and changing one mid-run would produce results that no longer match the
        settings that were recorded.
      </p>
    </section>
  );
}

/* ── Shared bits ─────────────────────────────────────────────── */

function SettingHeading({ title, hint }: { title: string; hint: string }) {
  return (
    <div className="mb-3">
      <h2 className="font-display text-[15px] font-semibold tracking-tight text-[var(--text)]">
        {title}
      </h2>
      <p className="mt-0.5 text-[12.5px] text-[var(--muted)]">{hint}</p>
    </div>
  );
}

function Row({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 px-4 py-3">
      <span className="text-[13px] text-[var(--muted)]">{label}</span>
      <span
        className={cn(
          "text-[13px] text-[var(--text)]",
          mono && "font-mono text-[11.5px]",
        )}
      >
        {value}
      </span>
    </div>
  );
}
