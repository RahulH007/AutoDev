"use client";

import { useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { ArrowRight, Loader2 } from "lucide-react";

import StageTrack from "@/components/ui/StageTrack";
import { Panel } from "@/components/ui/Primitives";
import { STAGES } from "@/lib/stages";
import { ApiError, api } from "@/lib/api";
import { cn } from "@/lib/utils";

/**
 * The front door: describe an idea, get a verified proof of concept.
 *
 * A run is the only entity the API has. There is no project and no name field —
 * `service.create()` derives a placeholder name from the requirement, and
 * `_settle()` replaces it with the PRD's `product_name` once the PM agent
 * finishes. Asking the user to name anything here would be asking for input the
 * backend is about to overwrite.
 */

/** What actually makes a difference to the brief the PM agent writes back. */
const GUIDANCE = [
  { label: "Who it's for", hint: "The people who will use it day to day." },
  { label: "What it should do", hint: "The job it does, in one or two sentences." },
  { label: "Important features", hint: "The handful that matter most." },
  { label: "Constraints", hint: "Anything it must or must not use." },
];

/**
 * Full requirements rather than one-liners: they populate the box directly, so
 * each one has to be a decent example of the four points above.
 */
const EXAMPLES = [
  {
    title: "Support knowledge base",
    teaser: "Upload docs, ask questions, get cited answers.",
    body: "An AI customer-support platform for small SaaS teams. Support agents upload product documentation and past tickets, then ask questions in plain language and get answers that cite the source document. Needs user accounts, document upload for PDF and Markdown, a per-user search history, and an admin view showing which questions came back unanswered. Python backend with a simple web frontend, no third-party support-desk integrations.",
  },
  {
    title: "Freelance expense tracker",
    teaser: "Log expenses, see a monthly breakdown.",
    body: "An expense tracker for freelancers who invoice monthly. Users sign in, record an expense with a category, date, amount and an optional note, and see a monthly report broken down by category with a running total. Needs authentication, full expense create/read/update/delete, and a month-by-month summary. Keep it to a single service with a relational database and no accounting-software integrations.",
  },
  {
    title: "Warehouse stock tracker",
    teaser: "Check stock in and out, alert on low inventory.",
    body: "An internal inventory tracker for a small warehouse team. Staff type or scan a SKU to check stock in and out, and managers see low-stock alerts and a movement history for each item. Needs role-based access separating staff from managers, an audit trail recording who moved what and when, and CSV export of the movement log. Web only, no mobile app.",
  },
];

/**
 * The API accepts any non-empty requirement (`min_length=1`, then a strip-and-
 * reject in `service.create`). A longer floor here would be invented, so length
 * is a hint rather than a gate — the button only blocks on genuinely empty.
 */
const THIN_REQUIREMENT = 80;

export default function NewPocPage() {
  const router = useRouter();
  const inputRef = useRef<HTMLTextAreaElement>(null);

  const [requirement, setRequirement] = useState("");
  const [error, setError] = useState<ApiError | null>(null);
  const [starting, setStarting] = useState(false);

  const trimmed = requirement.trim();
  const canSubmit = trimmed.length > 0 && !starting;

  const useExample = (body: string) => {
    setRequirement(body);
    setError(null);
    inputRef.current?.focus();
  };

  const start = async () => {
    if (!canSubmit) return;
    setStarting(true);
    setError(null);
    try {
      const run = await api.createRun(trimmed);
      router.push(`/runs/${run.id}`);
      // Deliberately stays disabled: the route change unmounts this page, and
      // re-enabling first would let a second click start a duplicate run.
    } catch (caught) {
      setError(caught instanceof ApiError ? caught : new ApiError(0, String(caught)));
      setStarting(false);
    }
  };

  return (
    <div className="mx-auto max-w-3xl space-y-10">
      <header>
        <h1 className="font-display text-[32px] font-bold leading-[1.1] tracking-tightest text-[var(--text)] sm:text-[38px]">
          Build your next POC
        </h1>
        <p className="mt-3 max-w-xl text-[15px] leading-relaxed text-[var(--muted)]">
          Describe what you want to build. AgentForge will turn it into a working, verified POC.
        </p>
      </header>

      <section>
        <label htmlFor="requirement" className="sr-only">
          Describe what you want to build
        </label>
        <textarea
          id="requirement"
          ref={inputRef}
          value={requirement}
          onChange={(event) => setRequirement(event.target.value)}
          onKeyDown={(event) => {
            if ((event.metaKey || event.ctrlKey) && event.key === "Enter") start();
          }}
          disabled={starting}
          rows={9}
          placeholder="A scheduling tool for a dental practice. Receptionists book, move and cancel appointments across three dentists, and see the day at a glance…"
          aria-describedby="requirement-hint"
          className={cn(
            "w-full resize-y rounded-xl border bg-[var(--panel)] px-5 py-4",
            "text-[15px] leading-relaxed text-[var(--text)] placeholder:text-[var(--muted-soft)]",
            "border-[var(--line)] transition-colors focus:border-[var(--line-strong)] focus:outline-none",
            "disabled:opacity-60",
          )}
        />

        <div className="mt-3 flex flex-wrap items-center justify-between gap-x-6 gap-y-3">
          <p id="requirement-hint" className="text-[12.5px] text-[var(--muted)]">
            {trimmed.length === 0
              ? "The more you describe, the closer the result lands."
              : trimmed.length < THIN_REQUIREMENT
                ? "That will run, but a sentence or two more gives the agents something to work with."
                : `${wordCount(trimmed)} words — enough to work from.`}
          </p>

          <button
            onClick={start}
            disabled={!canSubmit}
            className={cn(
              "inline-flex shrink-0 items-center gap-2 rounded-lg px-6 py-3",
              "bg-[var(--invert-bg)] text-[14px] font-semibold text-[var(--invert-text)]",
              "transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-35",
            )}
          >
            {starting ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin" />
                Starting the run…
              </>
            ) : (
              <>
                Build POC
                <ArrowRight className="h-4 w-4" />
              </>
            )}
          </button>
        </div>

        {error && (
          <Panel className="mt-4 border-dashed p-4">
            <p className="text-[13.5px] font-medium text-[var(--text)]">
              {error.isOffline ? "The API is not reachable" : "The run could not be started"}
            </p>
            <p className="mt-1 font-mono text-[11.5px] leading-relaxed text-[var(--muted)]">
              {error.message}
            </p>
          </Panel>
        )}
      </section>

      <section>
        <SectionLabel>Start from an example</SectionLabel>
        <div className="grid gap-2.5 sm:grid-cols-3">
          {EXAMPLES.map((example) => (
            <button
              key={example.title}
              type="button"
              onClick={() => useExample(example.body)}
              disabled={starting}
              className={cn(
                "rounded-xl border border-[var(--line)] bg-[var(--panel)] p-4 text-left",
                "transition-colors hover:border-[var(--line-strong)] hover:bg-[var(--panel-2)]",
                "disabled:cursor-not-allowed disabled:opacity-50",
              )}
            >
              <p className="font-display text-[13.5px] font-semibold tracking-tight text-[var(--text)]">
                {example.title}
              </p>
              <p className="mt-1 text-[12px] leading-relaxed text-[var(--muted)]">
                {example.teaser}
              </p>
            </button>
          ))}
        </div>
      </section>

      <div className="grid gap-6 md:grid-cols-2">
        <section>
          <SectionLabel>What to include</SectionLabel>
          <Panel className="divide-y divide-[var(--line)]">
            {GUIDANCE.map((item) => (
              <div key={item.label} className="px-4 py-3">
                <p className="text-[13px] font-medium text-[var(--text)]">{item.label}</p>
                <p className="mt-0.5 text-[12px] leading-relaxed text-[var(--muted)]">
                  {item.hint}
                </p>
              </div>
            ))}
          </Panel>
        </section>

        <section>
          <SectionLabel>What happens next</SectionLabel>
          <Panel className="p-4">
            <StageTrack idle size="sm" />
            <ol className="mt-4 space-y-2">
              {STAGES.map((stage, index) => (
                <li key={stage.id} className="flex items-baseline gap-3 text-[12.5px]">
                  <span className="w-9 shrink-0 font-mono text-[9.5px] uppercase tracking-[0.1em] text-[var(--muted-soft)]">
                    {stage.short}
                  </span>
                  <span className="text-[var(--text)]">{stage.label}</span>
                  {index < 2 && (
                    <span className="ml-auto shrink-0 font-mono text-[9.5px] uppercase tracking-[0.08em] text-[var(--muted)]">
                      You review
                    </span>
                  )}
                </li>
              ))}
            </ol>
            <p className="mt-3.5 border-t border-[var(--line)] pt-3 text-[12px] leading-relaxed text-[var(--muted)]">
              The run pauses after the brief and again after the architecture. Approve to continue,
              or send notes and the agent reworks its own output.
            </p>
          </Panel>
        </section>
      </div>
    </div>
  );
}

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <h2 className="mb-3 font-display text-[11px] font-semibold uppercase tracking-[0.14em] text-[var(--muted)]">
      {children}
    </h2>
  );
}

function wordCount(text: string) {
  return text.split(/\s+/).filter(Boolean).length;
}
