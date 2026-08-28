"use client";

import { useState } from "react";
import { Check, Loader2, MessageSquare } from "lucide-react";

import {
  ArchitectureDocument,
  PrdDocument,
} from "@/components/runs/ArtifactDocuments";
import { api, type Architecture, type Prd, type RunStatus } from "@/lib/api";

export default function ReviewPanel({
  runId,
  status,
  prd,
  architecture,
  onResumed,
}: {
  runId: string;
  status: RunStatus;
  prd: Prd;
  architecture: Architecture;
  onResumed: () => void;
}) {
  const isPmReview = status === "awaiting_pm_review";
  const [feedback, setFeedback] = useState("");
  const [busy, setBusy] = useState<"approve" | "revise" | null>(null);
  const [error, setError] = useState("");

  const act = async (kind: "approve" | "revise") => {
    setBusy(kind);
    setError("");
    try {
      if (kind === "approve") {
        await api.approve(runId);
      } else {
        await api.sendFeedback(runId, feedback.trim());
      }
      setFeedback("");
      onResumed();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Something went wrong.");
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="overflow-hidden rounded-xl border border-[var(--line-strong)] bg-[var(--panel)] ring-1 ring-inset ring-[var(--line)]">
      <div className="border-b border-[var(--line)] bg-[var(--panel-2)] px-5 py-4">
        <h2 className="text-[var(--text)] font-semibold text-sm">
          {isPmReview ? "Review the requirements" : "Review the architecture"}
        </h2>
        <p className="text-xs text-[var(--muted)] mt-1">
          The pipeline is paused. Approve to continue, or describe what should change and it goes
          back to the {isPmReview ? "PM" : "architecture"} agent.
        </p>
      </div>

      <div className="px-5 py-4 max-h-[420px] overflow-y-auto">
        {isPmReview ? (
          <PrdDocument prd={prd} />
        ) : (
          <ArchitectureDocument architecture={architecture} />
        )}
      </div>

      <div className="px-5 py-4 border-t border-[var(--line)] space-y-3">
        <label className="block text-xs font-medium text-[var(--muted)]">
          Revision notes
          <span className="ml-1.5 text-[var(--muted-soft)] font-normal">— leave empty to approve</span>
        </label>
        <textarea
          value={feedback}
          onChange={(event) => setFeedback(event.target.value)}
          rows={3}
          placeholder={
            isPmReview
              ? "e.g. Add budget alerts and drop the recurring-expense feature."
              : "e.g. Split the API into separate auth and reporting services."
          }
          className="w-full resize-none rounded-md border border-[var(--line)] bg-[var(--panel-2)] px-3 py-2 text-sm leading-relaxed text-[var(--text)] transition-colors placeholder:text-[var(--muted-soft)] focus:border-[var(--line-strong)] focus:outline-none"
        />

        {error && <p className="text-xs font-medium text-[var(--text)]">{error}</p>}

        <div className="flex items-center justify-end gap-2">
          <button
            onClick={() => act("revise")}
            disabled={busy !== null || !feedback.trim()}
            className="inline-flex items-center gap-2 px-4 py-2 rounded-md text-sm border border-[var(--line-strong)] text-[var(--text)] hover:bg-[var(--panel-2)] transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
          >
            {busy === "revise" ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : (
              <MessageSquare className="w-4 h-4" />
            )}
            Request revision
          </button>
          <button
            onClick={() => act("approve")}
            disabled={busy !== null}
            className="inline-flex items-center gap-2 rounded-lg bg-[var(--invert-bg)] px-4 py-2 text-sm font-semibold text-[var(--invert-text)] transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-40"
          >
            {busy === "approve" ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : (
              <Check className="w-4 h-4" />
            )}
            Approve and continue
          </button>
        </div>
      </div>
    </div>
  );
}
