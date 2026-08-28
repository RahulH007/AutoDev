import Link from "next/link";
import { ChevronRight } from "lucide-react";

const sections = [
  { id: "overview",     label: "Overview" },
  { id: "quickstart",   label: "Quickstart" },
  { id: "architecture", label: "Architecture" },
  { id: "agents",       label: "Agent Reference" },
  { id: "verification", label: "Verification" },
  { id: "hitl",         label: "Human-in-the-Loop" },
  { id: "outputs",      label: "Outputs" },
  { id: "api",          label: "API" },
  { id: "configuration", label: "Configuration" },
];

export default function DocsPage() {
  return (
    <div className="max-w-7xl mx-auto px-6 py-12">
      <div className="grid grid-cols-1 lg:grid-cols-[220px_1fr] gap-10">
        <aside className="lg:sticky lg:top-24 self-start">
          <div className="text-xs uppercase tracking-wider text-[var(--muted)] font-medium mb-3">
            Documentation
          </div>
          <nav className="space-y-0.5">
            {sections.map((s) => (
              <a
                key={s.id}
                href={`#${s.id}`}
                className="flex items-center justify-between text-sm text-[var(--muted)] hover:text-[var(--text)] px-3 py-2 rounded-md hover:bg-[var(--panel-2)] transition-colors"
              >
                {s.label}
                <ChevronRight className="w-3.5 h-3.5 text-[var(--muted-soft)]" />
              </a>
            ))}
          </nav>
        </aside>

        <article className="space-y-16 max-w-3xl">
          <Section id="overview" title="Overview">
            <p>
              AgentForge is a LangGraph-orchestrated multi-agent system that turns a plain-text
              requirement into a complete, runnable codebase. Four agents — Product Manager,
              Architect, Developer, and QA — execute in sequence, with human review between PM and
              Architecture, and two automated gates that check the generated code.
            </p>
            <p>
              Every agent produces a Pydantic-validated JSON document and a human-readable PDF.
              Generated code is compiled and its tests are executed before a run is called done; a
              failure routes the work back to the Developer agent for up to three attempts in total.
            </p>
          </Section>

          <Section id="quickstart" title="Quickstart">
            <p>Run it from the command line:</p>
            <Code>{`pip install -r requirements.txt
cp .env.example .env       # add your GOOGLE_API_KEY
python main.py "Build an expense tracker with login and monthly reports."`}</Code>
            <p>Or start the API and use this console:</p>
            <Code>{`python scripts/dev.py         # terminal 1
cd frontend && npm run dev    # terminal 2`}</Code>
            <p>
              Either way the run pauses after the PM and Architecture agents so you can approve or
              request revisions. Everything a run produces lands in{" "}
              <code>runs/&lt;run_id&gt;/</code>, including a zip of the finished project.
            </p>
          </Section>

          <Section id="architecture" title="Architecture">
            <p>The graph (defined in <code>graph/build_graph.py</code>) is:</p>
            <Code>{`START
  -> pm_agent
        -> [conditional] pm_feedback present?
              yes -> pm_agent (loop)
              no  -> architecture_agent
  -> architecture_agent
        -> [conditional] architect_feedback present?
              yes -> architecture_agent (loop)
              no  -> developer_agent
  -> developer_agent
  -> static_gate
        -> [conditional] does the code parse?
              no  -> developer_agent (skips the QA model call entirely)
              yes -> qa_agent
  -> qa_agent
  -> test_runner
        -> [conditional] failing tests, critical bugs, or score < 7 ?
              yes -> developer_agent (max 3 attempts)
              no  -> END`}</Code>
            <p>
              State is checkpointed to SQLite with <code>AsyncSqliteSaver</code>, so a run that is
              paused for review survives a server restart. The graph interrupts after{" "}
              <code>pm_agent</code> and <code>architecture_agent</code>, and the explicit{" "}
              <code>current_stage</code> field records which review a paused run is waiting on.
            </p>
          </Section>

          <Section id="agents" title="Agent Reference">
            <AgentCard
              name="PM Agent"
              path="agents/pm_agent.py"
              reads="user_requirements, prd (on revision), pm_feedback"
              writes="prd, pm_feedback (cleared)"
              schema="schema/product_manager_schema.py - ManagerSchema"
              desc="Generates a structured PRD: features, modules, data entities, APIs, NFRs."
            />
            <AgentCard
              name="Architecture Agent"
              path="agents/architecture_agent.py"
              reads="user_requirements, prd, architecture (on revision), architect_feedback"
              writes="architecture, architect_feedback (cleared)"
              schema="schema/architect_schema.py - ArchitectSchema"
              desc="Designs the system: services, databases, env vars, project layout."
            />
            <AgentCard
              name="Developer Agent"
              path="agents/developer_agent.py"
              reads="user_requirements, prd, architecture, qa_report, static_report, verification_report"
              writes="code_manifest, retry_count, status"
              schema="schema/developer_schema.py - DeveloperSchema"
              desc="Generates source code for every service into runs/<id>/source/. On a retry the prompt carries real compiler output and pytest tracebacks, not just the reviewer's opinion."
            />
            <AgentCard
              name="QA Agent"
              path="agents/qa_agent.py"
              reads="prd, architecture, code_manifest"
              writes="qa_report, status"
              schema="schema/qa_schema.py - QASchema"
              desc="Reviews each service, scores code quality 1-10, and writes test cases. A cheap triage pass picks which files are worth reading before the expensive review call."
            />
          </Section>

          <Section id="verification" title="Verification">
            <p>
              Two nodes in the graph call no model at all. They exist so the pipeline&apos;s opinion
              of the generated code is grounded in what a compiler and a test runner actually say.
            </p>
            <ul>
              <li>
                <strong>Static gate</strong> (<code>verification/static_gate.py</code>) parses every
                Python file with <code>compile()</code>, validates JSON manifests, and runs the
                error-level subset of ruff to catch undefined names. Nothing is imported or
                executed. Because it is free, it runs <em>before</em> the QA agent — a syntax error
                never costs a review call.
              </li>
              <li>
                <strong>Test runner</strong> (<code>verification/test_runner.py</code>) scaffolds
                the <code>conftest.py</code> that generated suites always assume exists (the{" "}
                <code>client</code> fixture and a SQLite <code>DATABASE_URL</code>), then runs
                pytest with <code>--junitxml</code> and parses the failures.
              </li>
            </ul>
            <p>
              Generated code is untrusted input, so it executes through{" "}
              <code>verification/runner.py</code>: a scrubbed environment with no API keys in it, a
              working directory pinned inside the run workspace, a wall-clock timeout, and a cap on
              captured output. On a laptop that is a plain subprocess; the <code>Runner</code>{" "}
              protocol is the seam where a container backend would go.
            </p>
          </Section>

          <Section id="hitl" title="Human-in-the-Loop">
            <p>
              After the PM agent finishes, the run pauses with status <code>awaiting_review</code>{" "}
              and <code>current_stage</code> set to <code>pm</code>. You have three options, whether
              you are in the CLI or on a run page:
            </p>
            <ul>
              <li><strong>Approve</strong> — advance to the Architecture agent.</li>
              <li><strong>Send revision feedback</strong> — the state picks up your feedback and
              routes back through <code>pm_agent</code>, which re-generates the PRD with your
              guidance.</li>
              <li><strong>Cancel</strong> — abort the run.</li>
            </ul>
            <p>
              The same flow applies after the Architecture agent. The conditional routers in{" "}
              <code>graph/build_graph.py</code> only loop back when the corresponding feedback field
              (<code>pm_feedback</code> / <code>architect_feedback</code>) is non-empty. Because the
              checkpoint lives in SQLite, a paused run can be resumed later or from a different
              client.
            </p>
          </Section>

          <Section id="outputs" title="Outputs">
            <p>
              Every run gets its own workspace at <code>runs/&lt;run_id&gt;/</code>, so concurrent
              runs never overwrite each other:
            </p>
            <ul>
              <li><code>docs/product_manager.json</code> + <code>.pdf</code> — PRD artifacts</li>
              <li><code>docs/architect_agent.json</code> + <code>architecture_doc.pdf</code> — architecture artifacts</li>
              <li><code>docs/developer_agent.json</code> + <code>developer_doc.pdf</code> — developer artifacts</li>
              <li><code>docs/qa_agent.json</code> + <code>qa_report.pdf</code> — QA artifacts</li>
              <li><code>source/&lt;service&gt;/</code> — generated source files</li>
              <li><code>tests/&lt;service&gt;/</code> — generated test files</li>
              <li><code>artifacts/&lt;project&gt;.zip</code> — final deliverable</li>
              <li><code>run.log</code> — the same stream the console shows live</li>
            </ul>
            <p>
              Service names coming back from the model are slugified and every write goes through{" "}
              <code>safe_join</code>, so a file path in a model response cannot escape the run
              directory.
            </p>
          </Section>

          <Section id="api" title="API">
            <p>
              This console is a client of <code>server/app.py</code>. Everything it does is
              available over HTTP:
            </p>
            <Code>{`POST   /api/runs                       -> start a new run
GET    /api/runs                       -> list runs
GET    /api/runs/{id}                  -> run state, stages, reports, artifacts
DELETE /api/runs/{id}                  -> delete a run and its workspace
POST   /api/runs/{id}/approve          -> resume a paused run
POST   /api/runs/{id}/feedback         -> submit pm or architect feedback
POST   /api/runs/{id}/cancel           -> stop a run
GET    /api/runs/{id}/log              -> historical log lines
GET    /api/runs/{id}/events           -> SSE stream of live events
GET    /api/runs/{id}/files            -> generated file tree
GET    /api/runs/{id}/files/{path}     -> read one generated file
GET    /api/runs/{id}/artifacts/{name} -> download a PDF or JSON artifact
GET    /api/runs/{id}/zip              -> download the generated project`}</Code>
            <p>
              The SSE stream accepts a <code>?since=</code> cursor, so a page that reconnects
              replays what it missed instead of showing a gap.
            </p>
          </Section>

          <Section id="configuration" title="Configuration">
            <Code>{`# .env
LLM_PROVIDER=google              # google | groq | openai | ollama | cerebras
GOOGLE_API_KEY=your_gemini_key
LLM_FALLBACK_PROVIDERS=groq      # tried in order when the primary fails

MODEL_HEAVY=gemini-2.5-pro       # developer agent
MODEL_STRUCTURED=gemini-2.5-flash
MODEL_CHEAP=gemini-2.5-flash-lite

MAX_DEVELOPER_RETRIES=3
MIN_QUALITY_SCORE=7
VERIFY_TIMEOUT_SECONDS=300
RUNS_DIR=runs`}</Code>
            <p>
              Every tunable lives in <code>core/config.py</code> and nothing else reads the
              environment directly. Models are chosen per <em>purpose</em> rather than per agent —
              heavy for code generation, cheap for triage — so you can move spend to where it
              matters. Clients are built lazily in <code>llm/registry.py</code>, so importing an
              agent never requires an API key, which is what lets the test suite run offline.
            </p>
            <p>
              See <code>.env.example</code> for the full list with defaults.
            </p>
            <p>
              The frontend reads <code>NEXT_PUBLIC_API_URL</code> (defaults to{" "}
              <code>http://127.0.0.1:8000</code>). PDF generation requires DejaVu fonts in{" "}
              <code>utils/fonts/</code> (download from{" "}
              <Link href="https://dejavu-fonts.github.io/" className="text-violet-400 hover:text-violet-300">dejavu-fonts.github.io</Link>).
            </p>
          </Section>
        </article>
      </div>
    </div>
  );
}

function Section({ id, title, children }: { id: string; title: string; children: React.ReactNode }) {
  return (
    <section id={id} className="scroll-mt-24">
      <h2 className="text-2xl font-bold text-[var(--text)] mb-4 tracking-tight">{title}</h2>
      <div className="prose prose-invert text-[var(--muted)] text-sm leading-relaxed space-y-4 max-w-none
                      [&_code]:text-violet-300 [&_code]:bg-[var(--panel-2)] [&_code]:px-1.5 [&_code]:py-0.5 [&_code]:rounded
                      [&_strong]:text-[var(--text)] [&_strong]:font-medium
                      [&_ul]:list-disc [&_ul]:pl-6 [&_ul]:space-y-2
                      [&_a]:text-violet-400 [&_a]:hover:text-violet-300">
        {children}
      </div>
    </section>
  );
}

function Code({ children }: { children: React.ReactNode }) {
  return (
    <pre className="my-3 p-4 rounded-md bg-[var(--ink)] border border-[var(--line)] text-xs text-[var(--text)] overflow-x-auto font-mono leading-relaxed">
      <code>{children}</code>
    </pre>
  );
}

function AgentCard({
  name, path, reads, writes, schema, desc,
}: { name: string; path: string; reads: string; writes: string; schema: string; desc: string }) {
  return (
    <div className="my-4 p-5 rounded-lg border border-[var(--line)] bg-[var(--panel-2)]">
      <div className="flex items-baseline justify-between gap-3 mb-2">
        <h3 className="text-[var(--text)] font-semibold">{name}</h3>
        <code className="text-[11px] text-[var(--muted)]">{path}</code>
      </div>
      <p className="text-[var(--muted)] text-sm mb-3">{desc}</p>
      <dl className="grid grid-cols-1 sm:grid-cols-3 gap-x-4 gap-y-1 text-xs">
        <div>
          <dt className="text-[var(--muted-soft)] uppercase tracking-wider">Reads</dt>
          <dd className="text-[var(--text)] font-mono">{reads}</dd>
        </div>
        <div>
          <dt className="text-[var(--muted-soft)] uppercase tracking-wider">Writes</dt>
          <dd className="text-[var(--text)] font-mono">{writes}</dd>
        </div>
        <div>
          <dt className="text-[var(--muted-soft)] uppercase tracking-wider">Schema</dt>
          <dd className="text-[var(--text)] font-mono">{schema}</dd>
        </div>
      </dl>
    </div>
  );
}
