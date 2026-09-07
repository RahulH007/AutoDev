import type { ReactNode } from "react";

import { LevelChip } from "@/components/ui/Primitives";
import type { Architecture, Prd, ProjectStructure } from "@/lib/api";

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

      {full && (prd.user_flows?.length ?? 0) > 0 && (
        <Block title={`User flows (${prd.user_flows!.length})`}>
          <div className="space-y-2.5">
            {prd.user_flows!.map((flow) => (
              <Card key={flow.name}>
                <NameRow name={flow.name} aside={flow.actor} />
                <ol className="mt-1.5 space-y-1">
                  {flow.steps.map((step, index) => (
                    <li key={step} className="flex items-start gap-2">
                      <span className="mt-[1px] w-3.5 shrink-0 font-mono text-[10px] text-[var(--muted-soft)]">
                        {index + 1}
                      </span>
                      <span className="text-[11.5px] leading-relaxed text-[var(--muted)]">
                        {step}
                      </span>
                    </li>
                  ))}
                </ol>
              </Card>
            ))}
          </div>
        </Block>
      )}

      {full && (prd.modules?.length ?? 0) > 0 && (
        <Block title={`Modules (${prd.modules!.length})`}>
          <div className="space-y-2">
            {prd.modules!.map((module) => (
              <Card key={module.name}>
                <NameRow
                  name={module.name}
                  aside={
                    (module.depends_on?.length ?? 0) > 0
                      ? `needs ${module.depends_on!.join(", ")}`
                      : undefined
                  }
                />
                <p className="mt-1 text-[11.5px] leading-relaxed text-[var(--muted)]">
                  {module.responsibility}
                </p>
                {(module.exposes?.length ?? 0) > 0 && (
                  <div className="mt-2">
                    <Chips items={module.exposes!} />
                  </div>
                )}
              </Card>
            ))}
          </div>
        </Block>
      )}

      {full && (prd.data_entities?.length ?? 0) > 0 && (
        <Block title={`Data entities (${prd.data_entities!.length})`}>
          <div className="space-y-2">
            {prd.data_entities!.map((entity) => (
              <Card key={entity.name}>
                <NameRow name={entity.name} />
                <p className="mt-1 text-[11.5px] leading-relaxed text-[var(--muted)]">
                  {entity.description}
                </p>
                {(entity.key_attributes?.length ?? 0) > 0 && (
                  <div className="mt-2">
                    <Chips items={entity.key_attributes!} />
                  </div>
                )}
                {(entity.relationships?.length ?? 0) > 0 && (
                  <p className="mt-1.5 font-mono text-[10.5px] text-[var(--muted-soft)]">
                    {entity.relationships!.join(" · ")}
                  </p>
                )}
              </Card>
            ))}
          </div>
        </Block>
      )}

      {full && (prd.possible_apis?.length ?? 0) > 0 && (
        <Block title={`Proposed endpoints (${prd.possible_apis!.length})`}>
          <Endpoints
            endpoints={prd.possible_apis!.map((api) => ({
              method: api.method,
              path: api.path,
              description: api.description,
            }))}
          />
        </Block>
      )}

      {full && (prd.functional_requirements?.length ?? 0) > 0 && (
        <Block title={`Functional requirements (${prd.functional_requirements!.length})`}>
          <Bullets items={prd.functional_requirements!} />
        </Block>
      )}

      {full && (prd.non_functional_requirements?.length ?? 0) > 0 && (
        <Block title="Non-functional requirements">
          <div className="space-y-1.5">
            {prd.non_functional_requirements!.map((requirement) => (
              <div key={requirement.description} className="flex flex-wrap items-baseline gap-x-2.5">
                <span className="font-mono text-[10px] uppercase tracking-[0.08em] text-[var(--text)]">
                  {requirement.category}
                </span>
                <span className="min-w-0 text-[11.5px] leading-relaxed text-[var(--muted)]">
                  {requirement.description}
                </span>
                {requirement.target_metric && (
                  <span className="font-mono text-[10.5px] text-[var(--muted-soft)]">
                    {requirement.target_metric}
                  </span>
                )}
              </div>
            ))}
          </div>
        </Block>
      )}

      {full && (prd.suggested_tech_stack?.length ?? 0) > 0 && (
        <Block title="Suggested stack">
          <Chips items={prd.suggested_tech_stack!} />
        </Block>
      )}

      {full && prd.expected_scale && (
        <Block title="Expected scale">
          <Prose>{prd.expected_scale}</Prose>
        </Block>
      )}

      {full && (prd.constraints?.length ?? 0) > 0 && (
        <Block title="Constraints">
          <Bullets items={prd.constraints!} />
        </Block>
      )}

      {full && (prd.assumptions?.length ?? 0) > 0 && (
        <Block title="Assumptions">
          <Bullets items={prd.assumptions!} />
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
                  <div className="mt-2.5 border-t border-[var(--line)] pt-2.5">
                    <Endpoints endpoints={service.api_endpoints!} />
                  </div>
                )}

                {full && (service.data_models?.length ?? 0) > 0 && (
                  <div className="mt-2.5 space-y-1.5 border-t border-[var(--line)] pt-2.5">
                    {service.data_models!.map((model) => (
                      <div key={model.name}>
                        <NameRow name={model.name} aside={model.description} />
                        {model.fields.length > 0 && (
                          <div className="mt-1">
                            <Chips items={model.fields} />
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                )}

                {full && structureFor(architecture, service.name) && (
                  <div className="mt-2.5 border-t border-[var(--line)] pt-2.5">
                    <Structure structure={structureFor(architecture, service.name)!} />
                  </div>
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

      {full && (architecture.external_integrations?.length ?? 0) > 0 && (
        <Block title="External integrations">
          <div className="space-y-1.5">
            {architecture.external_integrations!.map((integration) => (
              <div key={integration.name} className="flex flex-wrap items-baseline gap-x-2.5">
                <span className="font-mono text-[11px] text-[var(--text)]">
                  {integration.name}
                </span>
                <span className="font-mono text-[10px] text-[var(--muted-soft)]">
                  {integration.integration_method}
                </span>
                <span className="min-w-0 text-[11.5px] text-[var(--muted)]">
                  {integration.purpose}
                </span>
              </div>
            ))}
          </div>
        </Block>
      )}

      {full && (unmatchedStructures(architecture).length ?? 0) > 0 && (
        <Block title="Project structure">
          <div className="space-y-2">
            {unmatchedStructures(architecture).map((structure) => (
              <Card key={structure.service_name}>
                <NameRow name={structure.service_name} />
                <div className="mt-1.5">
                  <Structure structure={structure} />
                </div>
              </Card>
            ))}
          </div>
        </Block>
      )}

      {full && (architecture.implementation_tasks?.length ?? 0) > 0 && (
        <Block title={`Implementation tasks (${architecture.implementation_tasks!.length})`}>
          <div className="space-y-1.5">
            {architecture.implementation_tasks!.map((task) => (
              <div key={`${task.service}-${task.task}`}>
                <NameRow name={task.task} aside={task.service} />
                <p className="mt-0.5 text-[11.5px] leading-relaxed text-[var(--muted)]">
                  {task.description}
                </p>
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

/**
 * The project structure belongs to a service, and is stored beside them.
 *
 * `ArchitectSchema` keeps `project_structure` as a separate list keyed by
 * service name, so it is shown inside the service it describes where the two can
 * be matched, and in a section of its own where they cannot — rather than being
 * dropped because the architect spelled a name differently in two places.
 */
function slugOf(name: string): string {
  return name.trim().toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
}

function structureFor(architecture: Architecture, serviceName: string) {
  return (architecture.project_structure ?? []).find(
    (structure) => slugOf(structure.service_name) === slugOf(serviceName),
  );
}

function unmatchedStructures(architecture: Architecture) {
  const services = new Set((architecture.services ?? []).map((service) => slugOf(service.name)));
  return (architecture.project_structure ?? []).filter(
    (structure) => !services.has(slugOf(structure.service_name)),
  );
}

function Structure({ structure }: { structure: ProjectStructure }) {
  return (
    <div className="space-y-1.5">
      {structure.folders.length > 0 && (
        <div className="flex items-baseline gap-2">
          <span className="shrink-0 font-mono text-[9.5px] uppercase tracking-[0.1em] text-[var(--muted-soft)]">
            dirs
          </span>
          <Chips items={structure.folders} />
        </div>
      )}
      {structure.key_files.length > 0 && (
        <div className="flex items-baseline gap-2">
          <span className="shrink-0 font-mono text-[9.5px] uppercase tracking-[0.1em] text-[var(--muted-soft)]">
            files
          </span>
          <Chips items={structure.key_files} />
        </div>
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

/** One item in a list of structured records: a service, a flow, an entity. */
function Card({ children }: { children: ReactNode }) {
  return (
    <div className="rounded-lg border border-[var(--line)] bg-[var(--panel-2)] p-3">{children}</div>
  );
}

function NameRow({ name, aside }: { name: string; aside?: string }) {
  return (
    <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5">
      <span className="text-[12.5px] font-medium text-[var(--text)]">{name}</span>
      {aside && (
        <span className="min-w-0 truncate font-mono text-[10px] text-[var(--muted-soft)]">
          {aside}
        </span>
      )}
    </div>
  );
}

/** Method, path and purpose — the same shape whether proposed or decided. */
function Endpoints({
  endpoints,
}: {
  endpoints: { method: string; path: string; description?: string }[];
}) {
  return (
    <ul className="space-y-1">
      {endpoints.map((endpoint) => (
        <li key={`${endpoint.method}-${endpoint.path}`} className="flex items-baseline gap-2.5">
          <span className="w-12 shrink-0 font-mono text-[10px] uppercase tracking-[0.08em] text-[var(--text)]">
            {endpoint.method}
          </span>
          <span className="font-mono text-[10.5px] text-[var(--muted)]">{endpoint.path}</span>
          {endpoint.description && (
            <span className="min-w-0 truncate text-[10.5px] text-[var(--muted-soft)]">
              {endpoint.description}
            </span>
          )}
        </li>
      ))}
    </ul>
  );
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
