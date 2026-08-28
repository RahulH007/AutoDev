import { Check, Minus, ShieldCheck, Slash } from "lucide-react";

import { LevelChip } from "@/components/ui/Primitives";
import { cn } from "@/lib/utils";
import type {
  QaBug,
  QaReport,
  QaServiceReport,
  StaticReport,
  VerificationReport,
} from "@/lib/api";

/**
 * What the machines found, as opposed to what the reviewer thought.
 *
 * The static gate and the test runner produce facts; the QA score is an opinion.
 * They are shown in that order and kept visually distinct for that reason.
 *
 * Pass and fail are marks, not colours: a filled tick against a struck circle.
 * Green-versus-red carried this before, which stopped working the moment the
 * palette lost its hue — two greys a shade apart is not a distinction.
 */
export default function VerificationPanel({
  staticReport,
  verification,
  qa,
}: {
  staticReport: StaticReport;
  verification: VerificationReport;
  qa: QaReport;
}) {
  const services = verification.services ?? [];
  const totalPassed = services.reduce((sum, service) => sum + service.passed, 0);
  const totalFailed = services.reduce((sum, service) => sum + service.failed + service.errors, 0);
  const reports = qa.service_reports ?? [];

  return (
    <div className="space-y-4">
      <section className="overflow-hidden rounded-xl border border-[var(--line)] bg-[var(--panel)]">
        <header className="flex items-center gap-2 border-b border-[var(--line)] px-5 py-3">
          <ShieldCheck className="h-3.5 w-3.5 text-[var(--muted)]" strokeWidth={1.9} />
          <span className="font-display text-[11px] font-semibold uppercase tracking-[0.14em] text-[var(--muted)]">
            Verification
          </span>
        </header>

        <div className="divide-y divide-[var(--line)]">
          <Row
            title="Static analysis"
            state={gateState(staticReport)}
            detail={
              !staticReport.ran
                ? "Has not run yet."
                : staticReport.passed
                  ? "Everything parses."
                  : `${staticReport.failures?.length ?? 0} problem(s) found.`
            }
          >
            {!staticReport.passed && (staticReport.failures?.length ?? 0) > 0 && (
              <Terminal>{staticReport.failures!.slice(0, 12).join("\n")}</Terminal>
            )}
          </Row>

          <Row
            title="Generated tests"
            state={gateState(verification)}
            detail={verification.summary || "Have not run yet."}
          >
            {services.length > 0 && (
              <div className="mt-3 space-y-2">
                {services.map((service) => (
                  <div
                    key={service.service}
                    className="rounded-lg border border-[var(--line)] bg-[var(--panel-2)] p-3"
                  >
                    <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
                      <span className="text-[12.5px] font-medium text-[var(--text)]">
                        {service.service}
                      </span>
                      <span className="shrink-0 font-mono text-[10.5px] text-[var(--muted)]">
                        {service.passed} passed
                        {service.failed + service.errors > 0 && (
                          <span className="font-semibold text-[var(--text)]">
                            {" · "}
                            {service.failed + service.errors} failed
                          </span>
                        )}
                        {service.skipped > 0 && ` · ${service.skipped} skipped`}
                      </span>
                    </div>

                    {service.error && (
                      <p className="mt-1.5 text-[11.5px] leading-relaxed text-[var(--text)]">
                        {service.error}
                      </p>
                    )}

                    {service.failures.slice(0, 4).map((failure) => (
                      <div key={failure.test} className="mt-2.5">
                        <p className="font-mono text-[11px] text-[var(--text)]">{failure.test}</p>
                        {failure.file && (
                          <p className="mt-0.5 font-mono text-[10px] text-[var(--muted-soft)]">
                            {failure.file}
                          </p>
                        )}
                        <Terminal className="mt-1 max-h-32">{failure.message}</Terminal>
                      </div>
                    ))}
                  </div>
                ))}
              </div>
            )}
          </Row>

          <div className="px-5 py-4">
            <div className="flex items-baseline justify-between gap-3">
              <span className="text-[13.5px] text-[var(--text)]">Reviewer assessment</span>
              <Score reports={reports} />
            </div>

            {qa.overall_assessment && (
              <p className="mt-2 text-[12.5px] leading-relaxed text-[var(--muted)]">
                {qa.overall_assessment}
              </p>
            )}

            <dl className="mt-3 flex flex-wrap gap-x-5 gap-y-1.5 font-mono text-[10.5px] text-[var(--muted)]">
              <Stat label="Tests written" value={qa.total_tests_written ?? 0} />
              <Stat label="Bugs found" value={qa.total_bugs_found ?? 0} />
              <Stat label="Critical" value={qa.critical_issues ?? 0} loud={(qa.critical_issues ?? 0) > 0} />
              <Stat label="Tests run" value={`${totalPassed} passed, ${totalFailed} failed`} />
            </dl>
          </div>
        </div>
      </section>

      <QaFindings reports={reports} />

      {(qa.recommendations?.length ?? 0) > 0 && (
        <section className="rounded-xl border border-[var(--line)] bg-[var(--panel)] px-5 py-4">
          <h3 className="font-display text-[11px] font-semibold uppercase tracking-[0.14em] text-[var(--muted)]">
            Recommendations
          </h3>
          <ul className="mt-2.5 space-y-1.5">
            {qa.recommendations!.map((recommendation) => (
              <li key={recommendation} className="flex items-start gap-2">
                <span className="mt-[7px] h-[3px] w-[3px] shrink-0 rounded-full bg-[var(--muted-soft)]" />
                <span className="text-[11.5px] leading-relaxed text-[var(--muted)]">
                  {recommendation}
                </span>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}

/* ── QA findings ─────────────────────────────────────────────── */

/**
 * The bugs themselves, which were previously reduced to a count.
 *
 * Every field is one the reviewer actually returned: where it is, how bad it
 * is, what is wrong, and what to do about it. Nothing here is inferred.
 */
function QaFindings({ reports }: { reports: QaServiceReport[] }) {
  const withBugs = reports.filter((report) => (report.bugs?.length ?? 0) > 0);
  if (withBugs.length === 0) return null;

  const total = withBugs.reduce((sum, report) => sum + report.bugs.length, 0);

  return (
    <section className="overflow-hidden rounded-xl border border-[var(--line)] bg-[var(--panel)]">
      <header className="flex items-baseline justify-between gap-3 border-b border-[var(--line)] px-5 py-3">
        <span className="font-display text-[11px] font-semibold uppercase tracking-[0.14em] text-[var(--muted)]">
          Findings
        </span>
        <span className="font-mono text-[10.5px] text-[var(--muted-soft)]">
          {total} across {withBugs.length} service{withBugs.length === 1 ? "" : "s"}
        </span>
      </header>

      <div className="divide-y divide-[var(--line)]">
        {withBugs.map((report) => (
          <div key={report.service_name} className="px-5 py-4">
            <div className="flex items-baseline justify-between gap-3">
              <h4 className="text-[12.5px] font-medium text-[var(--text)]">
                {report.service_name}
              </h4>
              <span className="shrink-0 font-mono text-[10.5px] text-[var(--muted-soft)]">
                quality {report.code_quality_score}/10
              </span>
            </div>

            <ul className="mt-3 space-y-2.5">
              {sortBySeverity(report.bugs).map((bug, index) => (
                <li
                  key={`${bug.file_path}-${bug.line_number}-${index}`}
                  className="rounded-lg border border-[var(--line)] bg-[var(--panel-2)] p-3"
                >
                  <div className="flex flex-wrap items-baseline gap-x-2.5 gap-y-1">
                    <LevelChip level={bug.severity} />
                    <span className="min-w-0 truncate font-mono text-[11px] text-[var(--text)]">
                      {bug.file_path || "unknown file"}
                      {bug.line_number && (
                        <span className="text-[var(--muted-soft)]">:{bug.line_number}</span>
                      )}
                    </span>
                  </div>

                  <p className="mt-1.5 text-[12px] leading-relaxed text-[var(--text)]">
                    {bug.description}
                  </p>

                  {bug.suggested_fix && (
                    <div className="mt-2 border-l-2 border-[var(--line-strong)] pl-3">
                      <p className="font-mono text-[9.5px] uppercase tracking-[0.1em] text-[var(--muted-soft)]">
                        Suggested fix
                      </p>
                      <p className="mt-1 text-[11.5px] leading-relaxed text-[var(--muted)]">
                        {bug.suggested_fix}
                      </p>
                    </div>
                  )}
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>
    </section>
  );
}

const SEVERITY_ORDER = ["critical", "high", "medium", "low"];

/** Worst first — a critical bug below three trivial ones is a list nobody reads. */
function sortBySeverity(bugs: QaBug[]): QaBug[] {
  return [...bugs].sort((a, b) => rank(a.severity) - rank(b.severity));
}

function rank(severity: string): number {
  const index = SEVERITY_ORDER.indexOf((severity ?? "").trim().toLowerCase());
  return index === -1 ? SEVERITY_ORDER.length : index;
}

/* ── Shared bits ─────────────────────────────────────────────── */

type GateState = "pass" | "fail" | "idle";

function gateState(report: { ran?: boolean; passed?: boolean }): GateState {
  if (!report.ran) return "idle";
  return report.passed ? "pass" : "fail";
}

function Row({
  title,
  state,
  detail,
  children,
}: {
  title: string;
  state: GateState;
  detail: string;
  children?: React.ReactNode;
}) {
  return (
    <div className="px-5 py-4">
      <div className="flex items-start gap-3">
        <GateMark state={state} />
        <div className="min-w-0 flex-1">
          <p className="text-[13.5px] text-[var(--text)]">{title}</p>
          <p className="mt-0.5 text-[12px] text-[var(--muted)]">{detail}</p>
          {children}
        </div>
      </div>
    </div>
  );
}

/** Filled tick for a pass, struck circle for a fail, dash for not yet run. */
function GateMark({ state }: { state: GateState }) {
  return (
    <span
      className={cn(
        "flex h-7 w-7 shrink-0 items-center justify-center rounded-md border",
        state === "pass" && "border-transparent bg-[var(--invert-bg)] text-[var(--invert-text)]",
        state === "fail" && "border-[var(--text)] text-[var(--text)]",
        state === "idle" && "border-dashed border-[var(--line-strong)] text-[var(--muted-soft)]",
      )}
    >
      {state === "pass" && <Check className="h-3.5 w-3.5" strokeWidth={2.6} />}
      {state === "fail" && <Slash className="h-3.5 w-3.5" strokeWidth={2.4} />}
      {state === "idle" && <Minus className="h-3.5 w-3.5" strokeWidth={2} />}
    </span>
  );
}

function Terminal({ children, className }: { children: string; className?: string }) {
  return (
    <pre
      className={cn(
        "mt-2 overflow-x-auto whitespace-pre-wrap rounded-md border border-[var(--line)] bg-[var(--panel-2)] p-3 font-mono text-[10.5px] leading-relaxed text-[var(--muted)]",
        className,
      )}
    >
      {children}
    </pre>
  );
}

function Stat({
  label,
  value,
  loud,
}: {
  label: string;
  value: string | number;
  loud?: boolean;
}) {
  return (
    <div className="flex items-baseline gap-1.5">
      <dt className="text-[var(--muted-soft)]">{label}</dt>
      <dd className={cn("text-[var(--text)]", loud && "font-semibold")}>{value}</dd>
    </div>
  );
}

function Score({ reports }: { reports: { code_quality_score: number }[] }) {
  if (reports.length === 0) {
    return <span className="text-[13.5px] text-[var(--muted-soft)]">—</span>;
  }

  const average =
    reports.reduce((sum, report) => sum + report.code_quality_score, 0) / reports.length;

  return (
    <span className="shrink-0 font-mono text-[15px] font-medium text-[var(--text)]">
      {average.toFixed(1)}
      <span className="text-[11px] text-[var(--muted-soft)]">/10</span>
    </span>
  );
}
