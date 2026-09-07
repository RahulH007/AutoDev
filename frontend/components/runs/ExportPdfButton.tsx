"use client";

import { useState } from "react";
import { Download, FileDown, Loader2 } from "lucide-react";

import { api, ApiError, EXPORT_ARTIFACT, type ExportKind } from "@/lib/api";

/**
 * Hand the document over as a PDF, when someone actually wants a file.
 *
 * The panel beside this is built from the structured JSON, which is the
 * canonical artifact — so nothing on screen waits on an export and a run that is
 * never exported is not missing anything. That is why this is a quiet secondary
 * control rather than the way the document is read.
 *
 * The first export costs a model call, so it says so before it is clicked and
 * says what it cost afterwards. A document already exported is a plain download:
 * the API returns the existing file rather than paying for it twice.
 */
export default function ExportPdfButton({
  runId,
  kind,
  existing,
}: {
  runId: string;
  kind: ExportKind;
  /** Artifact names the run already has, from `RunDetail.artifacts`. */
  existing: string[];
}) {
  const artifact = EXPORT_ARTIFACT[kind];
  const [ready, setReady] = useState(existing.includes(artifact));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  if (ready) {
    return (
      <a
        href={api.artifactUrl(runId, artifact)}
        target="_blank"
        rel="noreferrer"
        className="inline-flex items-center gap-1.5 rounded-md border border-[var(--line)] px-2.5 py-1 text-[11.5px] text-[var(--muted)] transition-colors hover:bg-[var(--panel-2)] hover:text-[var(--text)]"
      >
        <Download className="h-3 w-3" />
        PDF
      </a>
    );
  }

  const run = async () => {
    setBusy(true);
    setError("");
    try {
      await api.exportPdf(runId, kind);
      setReady(true);
    } catch (caught) {
      setError(
        caught instanceof ApiError ? caught.message : "The PDF could not be produced.",
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex items-center gap-2">
      {error && <span className="text-[11px] text-[var(--muted)]">{error}</span>}
      <button
        onClick={run}
        disabled={busy}
        title="Writes the document as prose and renders a PDF. Costs one model call."
        className="inline-flex items-center gap-1.5 rounded-md border border-[var(--line)] px-2.5 py-1 text-[11.5px] text-[var(--muted)] transition-colors hover:bg-[var(--panel-2)] hover:text-[var(--text)] disabled:opacity-40"
      >
        {busy ? (
          <Loader2 className="h-3 w-3 animate-spin" />
        ) : (
          <FileDown className="h-3 w-3" />
        )}
        {busy ? "Exporting…" : "Export PDF"}
      </button>
    </div>
  );
}
