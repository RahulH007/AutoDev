import Link from "next/link";

export default function Footer() {
  return (
    <footer className="border-t border-[var(--line)] bg-[var(--ink)]">
      <div className="max-w-7xl mx-auto px-6 py-10 grid grid-cols-2 md:grid-cols-4 gap-8 text-sm">
        <div className="col-span-2">
          <div className="flex items-center gap-2.5 mb-3">
            <div className="w-7 h-7 rounded-md bg-gradient-to-br from-violet-600 to-violet-800 flex items-center justify-center">
              <span className="text-[var(--text)] font-bold text-xs">A</span>
            </div>
            <span className="text-[var(--text)] font-semibold">
              Agent<span className="text-violet-400">Forge</span>
            </span>
          </div>
          <p className="text-[var(--muted)] text-sm leading-relaxed max-w-md">
            A LangGraph multi-agent system that turns plain-text requirements into
            production-ready, full-stack software projects.
          </p>
        </div>

        <div>
          <h4 className="text-[var(--text)] font-medium mb-3 text-sm">Platform</h4>
          <ul className="space-y-2 text-[var(--muted)]">
            <li><Link href="/dashboard" className="hover:text-[var(--text)] transition-colors">Dashboard</Link></li>
            <li><Link href="/docs" className="hover:text-[var(--text)] transition-colors">Documentation</Link></li>
            <li><Link href="/contact" className="hover:text-[var(--text)] transition-colors">Contact</Link></li>
          </ul>
        </div>

        <div>
          <h4 className="text-[var(--text)] font-medium mb-3 text-sm">Resources</h4>
          <ul className="space-y-2 text-[var(--muted)]">
            <li><Link href="/docs#agents" className="hover:text-[var(--text)] transition-colors">Agent Reference</Link></li>
            <li><Link href="/docs#architecture" className="hover:text-[var(--text)] transition-colors">Architecture</Link></li>
            <li><Link href="/docs#api" className="hover:text-[var(--text)] transition-colors">API</Link></li>
          </ul>
        </div>
      </div>

      <div className="border-t border-[var(--line)]">
        <div className="max-w-7xl mx-auto px-6 py-5 flex flex-col sm:flex-row items-center justify-between text-xs text-[var(--muted-soft)] gap-2">
          <span>© {new Date().getFullYear()} AgentForge. All rights reserved.</span>
          <span>Built with LangGraph, Gemini, FastAPI, and Next.js.</span>
        </div>
      </div>
    </footer>
  );
}
