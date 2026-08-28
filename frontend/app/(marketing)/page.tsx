import Link from "next/link";

import KineticGrid from "@/components/ui/KineticGrid";
import { ArrowRight, Layers, ShieldCheck, GitMerge, Boxes, Radio, FolderLock } from "lucide-react";

const stages = [
  { tag: "01", kind: "agent", name: "Product Manager", desc: "Translates a plain-text requirement into a structured PRD with features, modules, data entities, and APIs." },
  { tag: "02", kind: "agent", name: "Architect", desc: "Designs the system: services, databases, environment, and folder structure ready for code generation." },
  { tag: "03", kind: "agent", name: "Developer", desc: "Generates source code for every service, and on a retry works from real compiler output instead of guesswork." },
  { tag: "04", kind: "gate", name: "Static Gate", desc: "Compiles every file and lints for undefined names before any reviewer is paid to look at it. No model call." },
  { tag: "05", kind: "agent", name: "QA Engineer", desc: "Reviews each service, scores quality, and writes the test cases the next stage will actually execute." },
  { tag: "06", kind: "gate", name: "Test Runner", desc: "Installs dependencies in an isolated venv and runs pytest. Failing tests route the work back to the Developer." },
];

const features = [
  { icon: ShieldCheck, title: "Verified, Not Just Reviewed", desc: "Generated code is compiled and its tests are executed. A run only passes when a compiler and pytest agree, not because a model said it looked fine." },
  { icon: GitMerge, title: "Evidence-Driven Retries", desc: "Failures route back to the Developer carrying the actual tracebacks and compiler errors, up to three attempts." },
  { icon: Radio, title: "Live Run Streaming", desc: "Every stage streams to the console over SSE with a replay cursor, so a reconnect resumes the log instead of losing it." },
  { icon: FolderLock, title: "Isolated Workspaces", desc: "Each run owns a directory. Model-supplied paths are sanitized, and generated code executes with your API keys scrubbed from its environment." },
  { icon: Layers, title: "Structured Outputs", desc: "Every agent returns a Pydantic-validated JSON document plus a human-readable PDF artifact." },
  { icon: Boxes, title: "Resumable by Default", desc: "State is checkpointed to SQLite, so a run paused for your review survives a restart and picks up where it stopped." },
];

export default function HomePage() {
  return (
    <>
      <KineticGrid className="border-b border-[var(--line)]">
        <div className="pointer-events-none absolute left-1/2 top-20 h-[400px] w-[800px] -translate-x-1/2 rounded-full bg-[color-mix(in_srgb,var(--text)_6%,transparent)] blur-[120px]" />

        <div className="relative mx-auto max-w-6xl px-6 pb-20 pt-24 text-center">
          <h1 className="text-5xl sm:text-6xl lg:text-7xl font-bold text-[var(--text)] leading-[1.05] tracking-tight mb-6">
            From requirement to
            <br />
            <span className="gradient-text">production codebase</span>
          </h1>

          <p className="max-w-2xl mx-auto text-lg text-[var(--muted)] leading-relaxed mb-10">
            Four AI agents — Product Manager, Architect, Developer, and QA — turn a single
            requirement into a complete codebase, and two automated gates compile it and run its
            tests before calling it done.
          </p>

          <div className="flex flex-col sm:flex-row items-center justify-center gap-3">
            <Link
              href="/dashboard"
              className="inline-flex items-center gap-2 px-6 py-3 rounded-md text-[var(--text)] font-medium text-sm bg-violet-600 hover:bg-violet-500 transition-colors"
            >
              Open Dashboard
              <ArrowRight className="w-4 h-4" />
            </Link>
            <Link
              href="/docs"
              className="inline-flex items-center gap-2 px-6 py-3 rounded-md text-[var(--text)] font-medium text-sm glass border border-white/10 hover:border-white/20 hover:text-[var(--text)] transition-colors"
            >
              Read the Docs
            </Link>
          </div>
        </div>
      </KineticGrid>

      <section>
        <div className="max-w-6xl mx-auto px-6 py-20">
          <div className="text-center mb-14">
            <h2 className="text-3xl sm:text-4xl font-bold text-[var(--text)] mb-3">The pipeline</h2>
            <p className="text-[var(--muted)] max-w-xl mx-auto">
              Four agents produce the work. Two gates check it against a compiler and a test runner.
            </p>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {stages.map((s) => (
              <div key={s.tag} className="glass rounded-xl border border-[var(--line)] p-6 hover:border-[var(--line-strong)] transition-colors">
                <div className="flex items-center justify-between mb-3">
                  <span className={`text-xs font-mono ${s.kind === "gate" ? "text-emerald-400" : "text-violet-400"}`}>
                    {s.tag}
                  </span>
                  <span
                    className={`text-[10px] uppercase tracking-wider px-2 py-0.5 rounded-full border ${
                      s.kind === "gate"
                        ? "text-emerald-400 border-emerald-500/25 bg-emerald-500/10"
                        : "text-violet-400 border-violet-500/25 bg-violet-500/10"
                    }`}
                  >
                    {s.kind}
                  </span>
                </div>
                <h3 className="text-[var(--text)] font-semibold mb-2">{s.name}</h3>
                <p className="text-[var(--muted)] text-sm leading-relaxed">{s.desc}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      <section className="border-t border-[var(--line)]">
        <div className="max-w-6xl mx-auto px-6 py-20">
          <div className="text-center mb-14">
            <h2 className="text-3xl sm:text-4xl font-bold text-[var(--text)] mb-3">Built for serious projects</h2>
            <p className="text-[var(--muted)] max-w-xl mx-auto">
              Real guardrails, not a code-generation toy.
            </p>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {features.map((f) => (
              <div key={f.title} className="glass rounded-xl border border-[var(--line)] p-6">
                <div className="w-10 h-10 rounded-md bg-violet-600/10 border border-violet-600/20 flex items-center justify-center mb-4">
                  <f.icon className="w-5 h-5 text-violet-400" />
                </div>
                <h3 className="text-[var(--text)] font-semibold mb-2">{f.title}</h3>
                <p className="text-[var(--muted)] text-sm leading-relaxed">{f.desc}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      <section className="border-t border-[var(--line)]">
        <div className="max-w-4xl mx-auto px-6 py-20 text-center">
          <h2 className="text-3xl sm:text-4xl font-bold text-[var(--text)] mb-3">
            Ready to ship?
          </h2>
          <p className="text-[var(--muted)] mb-8 max-w-xl mx-auto">
            Open the dashboard, describe what you need, and let the agent pipeline take it from there.
          </p>
          <Link
            href="/dashboard"
            className="inline-flex items-center gap-2 px-6 py-3 rounded-md text-[var(--text)] font-medium text-sm bg-violet-600 hover:bg-violet-500 transition-colors"
          >
            Open Dashboard
            <ArrowRight className="w-4 h-4" />
          </Link>
        </div>
      </section>
    </>
  );
}
