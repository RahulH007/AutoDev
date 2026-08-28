import type { ReactNode } from "react";

import { LevelChip } from "@/components/ui/Primitives";
import type { Architecture, Prd } from "@/lib/api";

/**
 * The two documents the pipeline exists to produce.
 *
 * Both are rendered in two places with different jobs: inside `ReviewPanel`,
 * where the reader is deciding whether to approve and wants the shape of the
 * thing; and on the run's Artifacts tab, where the run is over and the document
 * is the deliverable. That is one `variant` apart, not two components — a second
 * copy would drift the moment either schema gained a field.
 *
 * Every field comes from `RunDetail.prd` / `RunDetail.architecture`. Sections
 * whose data is absent are omitted rather than filled in.
 */

type Variant = "summary" | "full";

export function isEmptyArtifact(artifact: object | null | undefined): boolean {
  return !artifact || Object.keys(artifact).length === 0;
}

/* ── Product brief ───────────────────────────────────────────── */

export function PrdDocument({
  prd,
  variant = "summary",
}: {
  prd: Prd;
  variant?: Variant;
}) {
  const full = variant === "full";
  const features = prd.features ?? [];

  return (
    <div className="space-y-5 text-sm">
      <div>
        <h3 className="font-display text-[15px] font-semibold tracking-tight text-[var(--text)]">
          {prd.product_name ?? "Untitled product"}
        </h3>
        {prd.product_summary && (
          <p className="mt-1.5 text-[12.5px] leading-relaxed text-[var(--muted)]">
            {prd.product_summary}
          </p>
        )}
        {full && prd.complexity_estimate && (
          <p className="mt-2 font-mono text-[10.5px] uppercase tracking-[0.1em] text-[var(--muted-soft)]">
            Complexity: {prd.complexity_estimate}
          </p>
        )}
      </div>

      {prd.problem_statement && (
        <Block title="Problem">
          <Prose>{prd.problem_statement}</Prose>
        </Block>
      )}

      {features.length > 0 && (
        <Block title={`Features (${features.length})`}>
          <ul className="space-y-2">
            {features.map((feature) => (
              <li key={feature.name} className="flex items-start gap-2.5">
                <LevelChip level={feature.priority} className="mt-[1px]" />
                <div className="min-w-0">
                  <span className="text-[12.5px] text-[var(--text)]">{feature.name}</span>
                  {feature.is_mvp && (
                    <span className="ml-2 font-mono text-[9.5px] uppercase tracking-[0.1em] text-[var(--muted)]">
                      MVP
                    </span>
                  )}
                  <p className="mt-0.5 text-[11.5px] leading-relaxed text-[var(--muted)]">
                    {feature.description}
                  </p>
                  {full && (feature.acceptance_criteria?.length ?? 0) > 0 && (
                    <ul className="mt-1.5 space-y-1 border-l border-[var(--line)] pl-3">
                      {feature.acceptance_criteria.map((criterion) => (
                        <li
                          key={criterion}
                          className="text-[11px] leading-relaxed text-[var(--muted-soft)]"
                        >
                          {criterion}
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              </li>
            ))}
          </ul>
        </Block>
      )}

      {(prd.target_users?.length ?? 0) > 0 && (
        <Block title="Target users">
          <Chips items={prd.target_users!} />
        </Block>
      )}

      {full && (prd.success_metrics?.length ?? 0) > 0 && (
        <Block title="Success metrics">
          <Bullets items={prd.success_metrics!} />
        </Block>
      )}

      {full && (prd.functional_requirements?.length ?? 0) > 0 && (
        <Block title={`Functional requirements (${prd.functional_requirements!.length})`}>
          <Bullets items={prd.functional_requirements!} />
        </Block>
      )}

      {full && (prd.constraints?.length ?? 0) > 0 && (
        <Block title="Constraints">
          <Bullets items={prd.constraints!} />
        </Block>
      )}

      {(prd.open_questions?.length ?? 0) > 0 && (
        <Block title="Open questions">
          <Bullets items={prd.open_questions!} />
        </Block>
      )}

      {(prd.out_of_scope?.length ?? 0) > 0 && (
        <Block title="Out of scope">
          <Bullets items={prd.out_of_scope!} />
        </Block>
      )}
    </div>
  );
}

/* ── Architecture ────────────────────────────────────────────── */

export function ArchitectureDocument({
  architecture,
  variant = "summary",
}: {
  architecture: Architecture;
  variant?: Variant;
}) {
  const full = variant === "full";
  const services = architecture.services ?? [];
  const databases = architecture.databases ?? [];
  const variables = architecture.environment_variables ?? [];

  return (
    <div className="space-y-5 text-sm">
      <div>
        <h3 className="font-display text-[15px] font-semibold capitalize tracking-tight text-[var(--text)]">
          {(architecture.architecture_style ?? "Architecture").replace(/_/g, " ")}
        </h3>
        {architecture.system_overview && (
          <p className="mt-1.5 text-[12.5px] leading-relaxed text-[var(--muted)]">
            {architecture.system_overview}
          </p>
        )}
      </div>

      {services.length > 0 && (
        <Block title={`Services (${services.length})`}>
          <div className="space-y-2">
            {services.map((service) => (
              <div
                key={service.name}
                className="rounded-lg border border-[var(--line)] bg-[var(--panel-2)] p-3"
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="text-[12.5px] font-medium text-[var(--text)]">
                    {service.name}
                  </span>
                  <span className="shrink-0 font-mono text-[10px] text-[var(--muted-soft)]">
                    {service.api_endpoints?.length ?? 0} endpoints
                  </span>
                </div>
                <p className="mt-1 text-[11.5px] leading-relaxed text-[var(--muted)]">
                  {service.description}
                </p>
                <div className="mt-2">
                  <Chips items={service.tech_stack ?? []} />
                </div>

                {full && (service.dependencies?.length ?? 0) > 0 && (
                  <p className="mt-2 font-mono text-[10.5px] text-[var(--muted-soft)]">
                    Depends on: {service.dependencies!.join(", ")}
                  </p>
                )}

                {full && (service.api_endpoints?.length ?? 0) > 0 && (
                  <ul className="mt-2.5 space-y-1 border-t border-[var(--line)] pt-2.5">
                    {service.api_endpoints!.map((endpoint) => (
                      <li
                        key={`${endpoint.method}-${endpoint.path}`}
                        className="flex items-baseline gap-2.5"
                      >
                        <span className="w-12 shrink-0 font-mono text-[10px] uppercase tracking-[0.08em] text-[var(--text)]">
                          {endpoint.method}
                        </span>
                        <span className="font-mono text-[10.5px] text-[var(--muted)]">
                          {endpoint.path}
                        </span>
                        {endpoint.description && (
                          <span className="min-w-0 truncate text-[10.5px] text-[var(--muted-soft)]">
                            {endpoint.description}
                          </span>
                        )}
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            ))}
          </div>
        </Block>
      )}

      {databases.length > 0 && (
        <Block title="Databases">
          {full ? (
            <div className="space-y-1.5">
              {databases.map((database) => (
                <div key={database.name} className="flex items-baseline gap-2.5">
                  <span className="font-mono text-[11px] text-[var(--text)]">{database.name}</span>
                  <span className="font-mono text-[10px] text-[var(--muted-soft)]">
                    {database.type}
                  </span>
                  <span className="min-w-0 text-[11.5px] text-[var(--muted)]">
                    {database.purpose}
                  </span>
                </div>
              ))}
            </div>
          ) : (
            <Chips items={databases.map((database) => `${database.name} (${database.type})`)} />
          )}
        </Block>
      )}

      {full && variables.length > 0 && (
        <Block title={`Environment variables (${variables.length})`}>
          <div className="space-y-1.5">
            {variables.map((variable) => (
              <div key={variable.name} className="flex flex-wrap items-baseline gap-x-2.5 gap-y-0.5">
                <span className="font-mono text-[11px] text-[var(--text)]">{variable.name}</span>
                <span className="min-w-0 text-[11.5px] text-[var(--muted)]">
                  {variable.description}
                </span>
                {variable.example && (
                  <span className="font-mono text-[10.5px] text-[var(--muted-soft)]">
                    e.g. {variable.example}
                  </span>
                )}
              </div>
            ))}
          </div>
        </Block>
      )}

      {(architecture.risks?.length ?? 0) > 0 && (
        <Block title="Risks">
          <Bullets items={architecture.risks!} />
        </Block>
      )}

      {full && (architecture.development_notes?.length ?? 0) > 0 && (
        <Block title="Development notes">
          <Bullets items={architecture.development_notes!} />
        </Block>
      )}
    </div>
  );
}

/* ── Shared bits ─────────────────────────────────────────────── */

function Block({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div>
      <div className="mb-2 font-display text-[10.5px] font-semibold uppercase tracking-[0.14em] text-[var(--muted)]">
        {title}
      </div>
      {children}
    </div>
  );
}

function Prose({ children }: { children: ReactNode }) {
  return <p className="text-[12px] leading-relaxed text-[var(--muted)]">{children}</p>;
}

function Chips({ items }: { items: string[] }) {
  return (
    <div className="flex flex-wrap gap-1.5">
      {items.map((item) => (
        <span
          key={item}
          className="rounded border border-[var(--line)] bg-[var(--panel-2)] px-2 py-0.5 font-mono text-[10px] text-[var(--muted)]"
        >
          {item}
        </span>
      ))}
    </div>
  );
}

function Bullets({ items }: { items: string[] }) {
  return (
    <ul className="space-y-1.5">
      {items.map((item) => (
        <li key={item} className="flex items-start gap-2">
          <span className="mt-[7px] h-[3px] w-[3px] shrink-0 rounded-full bg-[var(--muted-soft)]" />
          <span className="text-[11.5px] leading-relaxed text-[var(--muted)]">{item}</span>
        </li>
      ))}
    </ul>
  );
}
