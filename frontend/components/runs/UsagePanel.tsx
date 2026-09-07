import { Coins } from "lucide-react";

import type { CostReport, CostTotals } from "@/lib/api";
import { stageLabel } from "@/lib/stages";

/** Weakest first, mirroring `llm/routing.py:TIER_ORDER` so the rows read in order. */
const TIER_ORDER = ["low", "medium", "high"] as const;

/**
 * What the run spent on model calls.
 *
 * Every figure here is measured, not modelled. `reserved_tokens` is what the
 * pipeline claimed against the providers' per-minute windows, which is the
 * number that decides whether a run fits a free tier — and the only token figure
 * always available, because a structured response comes back with the provider's
 * usage stripped off. Where providers *did* report usage it is shown beside it,
 * labelled as reported; where they did not, nothing is shown rather than the
 * estimate wearing a costume.
 *
 * Cost is omitted entirely unless the API supplies one. There is no pricing
 * table behind this pipeline yet, and a plausible-looking dollar figure derived
 * from a guessed rate would be the least trustworthy thing on the page.
 *
 * Deliberately a sidebar card and not a dashboard: the point of Phase 0 is
 * visibility into efficiency, and the panel should stay smaller than the thing
 * it measures.
 */
export default function UsagePanel({ report }: { report: CostReport | Record<string, never> }) {
  const totals = report as Partial<CostTotals>;
  const calls = totals.calls ?? 0;

  // No calls means nothing was spent yet — an empty card would only be noise.
  if (!calls) return null;

  const byStage = (report as CostReport).by_stage ?? {};
  const stages = Object.entries(byStage)
    .filter(([, stage]) => stage.calls > 0)
    .sort((a, b) => b[1].reserved_tokens - a[1].reserved_tokens);

  // Both absent unless the run actually pooled accounts or routed by difficulty,
  // so neither section appears for the ordinary single-key, unrouted setup.
  const accounts = Object.entries((report as CostReport).by_account ?? {}).sort(
    (a, b) => b[1].reserved_tokens - a[1].reserved_tokens,
  );
  const tiers = TIER_ORDER.flatMap((tier) => {
    const totals = (report as CostReport).by_tier?.[tier];
    return totals ? [[tier, totals] as const] : [];
  });

  return (
    <div className="overflow-hidden rounded-xl border border-[var(--line)] bg-[var(--panel)]">
      <div className="flex items-center gap-2 border-b border-[var(--line)] px-5 py-3">
        <Coins className="h-3.5 w-3.5 text-[var(--muted-soft)]" strokeWidth={1.9} />
        <span className="font-display text-[11px] font-semibold uppercase tracking-[0.14em] text-[var(--muted)]">
          Usage
        </span>
      </div>

      <dl className="space-y-2.5 px-5 py-4">
        <Row label="LLM calls" value={String(calls)} />
        <Row label="Tokens" value={formatTokens(totals.reserved_tokens ?? 0)} note="reserved" />
        {totals.actual_tokens != null && (
          <Row
            label="Reported"
            value={formatTokens(totals.actual_tokens)}
            note={`${totals.calls_with_usage ?? 0} of ${calls} calls`}
          />
        )}
        <Row label="Model time" value={formatSeconds(totals.seconds ?? 0)} />
        {totals.estimated_cost != null && (
          <Row label="Estimated cost" value={`$${totals.estimated_cost.toFixed(4)}`} />
        )}
      </dl>

      {stages.length > 0 && (
        <div className="border-t border-[var(--line)] px-5 py-4">
          <div className="mb-2.5 text-[11px] uppercase tracking-[0.12em] text-[var(--muted-soft)]">
            By stage
          </div>
          <dl className="space-y-2">
            {stages.map(([id, stage]) => (
              <Row
                key={id}
                label={stageLabel(id)}
                value={formatTokens(stage.reserved_tokens)}
                note={`${stage.calls} ${stage.calls === 1 ? "call" : "calls"}`}
              />
            ))}
          </dl>
        </div>
      )}

      {tiers.length > 0 && (
        <div className="border-t border-[var(--line)] px-5 py-4">
          <div className="mb-2.5 text-[11px] uppercase tracking-[0.12em] text-[var(--muted-soft)]">
            By tier
          </div>
          <dl className="space-y-2">
            {tiers.map(([tier, totals]) => (
              <Row
                key={tier}
                label={tier}
                value={formatTokens(totals.reserved_tokens)}
                note={`${totals.calls} ${totals.calls === 1 ? "call" : "calls"}`}
              />
            ))}
          </dl>
        </div>
      )}

      {accounts.length > 0 && (
        <div className="border-t border-[var(--line)] px-5 py-4">
          <div className="mb-2.5 text-[11px] uppercase tracking-[0.12em] text-[var(--muted-soft)]">
            By account
          </div>
          <dl className="space-y-2">
            {accounts.map(([id, account]) => (
              <Row
                key={id}
                label={id}
                value={formatTokens(account.reserved_tokens)}
                note={`${account.calls} ${account.calls === 1 ? "call" : "calls"}`}
              />
            ))}
          </dl>
        </div>
      )}
    </div>
  );
}

function Row({ label, value, note }: { label: string; value: string; note?: string }) {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <dt className="truncate text-[12px] text-[var(--muted)]">{label}</dt>
      <dd className="shrink-0 text-right font-mono text-[12px] text-[var(--text)]">
        {value}
        {note && <span className="ml-1.5 text-[10.5px] text-[var(--muted-soft)]">{note}</span>}
      </dd>
    </div>
  );
}

function formatTokens(tokens: number): string {
  if (tokens < 1000) return String(tokens);
  return `${(tokens / 1000).toFixed(tokens < 10_000 ? 1 : 0)}k`;
}

function formatSeconds(seconds: number): string {
  if (seconds < 60) return `${seconds.toFixed(1)}s`;
  const minutes = Math.floor(seconds / 60);
  return `${minutes}m ${Math.round(seconds - minutes * 60)}s`;
}
