"use client";

import { useState } from "react";
import { Mail, MessageSquare, Github, Send, MapPin, type LucideIcon } from "lucide-react";

const subjects = [
  "General inquiry",
  "Feature request",
  "Bug report",
  "Enterprise / partnership",
  "Press",
];

export default function ContactPage() {
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [subject, setSubject] = useState(subjects[0]);
  const [message, setMessage] = useState("");
  const [submitted, setSubmitted] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim() || !email.trim() || !message.trim()) return;
    setSubmitting(true);
    await new Promise((r) => setTimeout(r, 800));
    setSubmitting(false);
    setSubmitted(true);
  };

  return (
    <div className="max-w-6xl mx-auto px-6 py-14">
      <div className="text-center mb-12">
        <div className="text-xs uppercase tracking-wider text-violet-400 font-medium mb-2">Contact</div>
        <h1 className="text-4xl sm:text-5xl font-bold text-[var(--text)] tracking-tight mb-3">Let's talk</h1>
        <p className="text-[var(--muted)] max-w-xl mx-auto">
          Questions about deployment, custom agents, or running AgentForge inside your organisation?
          Reach out and we'll get back within one business day.
        </p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-[1fr_360px] gap-8">
        <div className="glass-strong rounded-xl border border-[var(--line)] p-7">
          {submitted ? (
            <div className="text-center py-12">
              <div className="w-12 h-12 rounded-full bg-emerald-500/15 border border-emerald-500/30 flex items-center justify-center mx-auto mb-4">
                <Send className="w-5 h-5 text-emerald-400" />
              </div>
              <h2 className="text-[var(--text)] font-semibold text-lg mb-1.5">Message sent</h2>
              <p className="text-[var(--muted)] text-sm">
                Thanks for reaching out — we'll reply to <span className="text-[var(--text)]">{email}</span> shortly.
              </p>
              <button
                onClick={() => {
                  setSubmitted(false);
                  setName("");
                  setEmail("");
                  setMessage("");
                }}
                className="mt-6 text-sm text-violet-400 hover:text-violet-300"
              >
                Send another message
              </button>
            </div>
          ) : (
            <form onSubmit={handleSubmit} className="space-y-5">
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <div>
                  <label className="block text-xs font-medium text-[var(--muted)] mb-2">Full name</label>
                  <input
                    type="text"
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    placeholder="Jane Doe"
                    required
                    className="w-full h-10 px-3 rounded-md bg-[var(--panel-2)] border border-[var(--line)] text-[var(--text)] text-sm placeholder-zinc-600 focus:outline-none focus:border-violet-500/40 transition-colors"
                  />
                </div>
                <div>
                  <label className="block text-xs font-medium text-[var(--muted)] mb-2">Email</label>
                  <input
                    type="email"
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    placeholder="jane@company.com"
                    required
                    className="w-full h-10 px-3 rounded-md bg-[var(--panel-2)] border border-[var(--line)] text-[var(--text)] text-sm placeholder-zinc-600 focus:outline-none focus:border-violet-500/40 transition-colors"
                  />
                </div>
              </div>

              <div>
                <label className="block text-xs font-medium text-[var(--muted)] mb-2">Subject</label>
                <select
                  value={subject}
                  onChange={(e) => setSubject(e.target.value)}
                  className="w-full h-10 px-3 rounded-md bg-[var(--panel-2)] border border-[var(--line)] text-[var(--text)] text-sm focus:outline-none focus:border-violet-500/40 transition-colors"
                >
                  {subjects.map((s) => (
                    <option key={s} value={s} className="bg-[var(--ink)]">{s}</option>
                  ))}
                </select>
              </div>

              <div>
                <label className="block text-xs font-medium text-[var(--muted)] mb-2">Message</label>
                <textarea
                  value={message}
                  onChange={(e) => setMessage(e.target.value)}
                  placeholder="Tell us what you need..."
                  rows={6}
                  required
                  className="w-full px-3 py-2 rounded-md bg-[var(--panel-2)] border border-[var(--line)] text-[var(--text)] text-sm placeholder-zinc-600 focus:outline-none focus:border-violet-500/40 transition-colors resize-none leading-relaxed"
                />
              </div>

              <div className="flex items-center justify-between pt-1">
                <p className="text-[11px] text-[var(--muted-soft)]">
                  This form is a placeholder — it has no backend yet. Use the email links instead.
                </p>
                <button
                  type="submit"
                  disabled={submitting || !name.trim() || !email.trim() || !message.trim()}
                  className="inline-flex items-center gap-2 px-5 py-2.5 rounded-md bg-violet-600 hover:bg-violet-500 text-[var(--text)] text-sm font-medium transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
                >
                  {submitting ? (
                    "Sending..."
                  ) : (
                    <>
                      Send message <Send className="w-4 h-4" />
                    </>
                  )}
                </button>
              </div>
            </form>
          )}
        </div>

        <aside className="space-y-4">
          <ContactCard
            icon={Mail}
            title="Email"
            value="hello@agentforge.dev"
            href="mailto:hello@agentforge.dev"
          />
          <ContactCard
            icon={MessageSquare}
            title="Support"
            value="support@agentforge.dev"
            href="mailto:support@agentforge.dev"
          />
          <ContactCard
            icon={Github}
            title="GitHub"
            value="github.com/agentforge"
            href="https://github.com"
          />
          <ContactCard
            icon={MapPin}
            title="Office"
            value="Bengaluru, India"
          />

          <div className="glass rounded-xl border border-[var(--line)] p-5 mt-6">
            <h4 className="text-[var(--text)] font-medium text-sm mb-2">Response times</h4>
            <ul className="text-xs text-[var(--muted)] space-y-1.5">
              <li><span className="text-[var(--text)]">General:</span> within 1 business day</li>
              <li><span className="text-[var(--text)]">Bug reports:</span> within 24 hours</li>
              <li><span className="text-[var(--text)]">Enterprise:</span> within 4 hours</li>
            </ul>
          </div>
        </aside>
      </div>
    </div>
  );
}

function ContactCard({
  icon: Icon, title, value, href,
}: { icon: LucideIcon; title: string; value: string; href?: string }) {
  const inner = (
    <div className="flex items-center gap-3 p-4 rounded-xl glass border border-[var(--line)] hover:border-[var(--line-strong)] transition-colors">
      <div className="w-9 h-9 rounded-md bg-violet-600/10 border border-violet-600/20 flex items-center justify-center flex-shrink-0">
        <Icon className="w-4 h-4 text-violet-400" />
      </div>
      <div className="min-w-0">
        <div className="text-[11px] uppercase tracking-wider text-[var(--muted)]">{title}</div>
        <div className="text-sm text-[var(--text)] truncate">{value}</div>
      </div>
    </div>
  );
  if (href) {
    return (
      <a href={href} target={href.startsWith("http") ? "_blank" : undefined} rel="noreferrer" className="block">
        {inner}
      </a>
    );
  }
  return inner;
}
