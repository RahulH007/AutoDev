"use client";

import { useEffect, useMemo, useState } from "react";
import { FileCode2, FlaskConical, Folder, Loader2 } from "lucide-react";

import { cn } from "@/lib/utils";
import { api, type FileContent, type FileNode } from "@/lib/api";

export default function FileExplorer({ runId }: { runId: string }) {
  const [files, setFiles] = useState<FileNode[] | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [content, setContent] = useState<FileContent | null>(null);
  const [loadingFile, setLoadingFile] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;

    api
      .listFiles(runId)
      .then(({ files: listed }) => {
        if (cancelled) return;
        setFiles(listed);
        setSelected((current) => current ?? listed[0]?.path ?? null);
      })
      .catch((caught) => !cancelled && setError(caught.message));

    return () => {
      cancelled = true;
    };
  }, [runId]);

  useEffect(() => {
    if (!selected) return;
    let cancelled = false;

    setLoadingFile(true);
    api
      .readFile(runId, selected)
      .then((body) => !cancelled && setContent(body))
      .catch((caught) => !cancelled && setError(caught.message))
      .finally(() => !cancelled && setLoadingFile(false));

    return () => {
      cancelled = true;
    };
  }, [runId, selected]);

  const grouped = useMemo(() => groupByDirectory(files ?? []), [files]);

  if (error) {
    return <Empty>{error}</Empty>;
  }

  if (files === null) {
    return (
      <Empty>
        <Loader2 className="w-4 h-4 animate-spin inline mr-2" />
        Loading files…
      </Empty>
    );
  }

  if (files.length === 0) {
    return <Empty>No code has been generated for this run yet.</Empty>;
  }

  return (
    <div className="glass rounded-xl border border-[var(--line)] overflow-hidden">
      <div className="grid grid-cols-1 lg:grid-cols-[280px_1fr] divide-y lg:divide-y-0 lg:divide-x divide-white/[0.05]">
        <div className="max-h-[520px] overflow-y-auto py-2">
          {Object.entries(grouped).map(([directory, entries]) => (
            <div key={directory} className="mb-1">
              <div className="flex items-center gap-1.5 px-4 py-1.5 text-[11px] text-[var(--muted-soft)] font-mono truncate">
                <Folder className="w-3 h-3 shrink-0" />
                {directory}
              </div>
              {entries.map((file) => (
                <button
                  key={file.path}
                  onClick={() => setSelected(file.path)}
                  className={cn(
                    "w-full flex items-center gap-2 px-4 py-1.5 text-left text-xs font-mono transition-colors",
                    selected === file.path
                      ? "bg-[var(--panel-2)] font-medium text-[var(--text)]"
                      : "text-[var(--muted)] hover:bg-[var(--panel-2)] hover:text-[var(--text)]",
                  )}
                >
                  {file.is_generated_test ? (
                    <FlaskConical className="w-3 h-3 shrink-0 text-[var(--text)]" />
                  ) : (
                    <FileCode2 className="w-3 h-3 shrink-0 text-[var(--muted-soft)]" />
                  )}
                  <span className="truncate">{basename(file.path)}</span>
                  <span className="ml-auto text-[10px] text-[var(--muted-soft)] shrink-0">
                    {formatSize(file.size)}
                  </span>
                </button>
              ))}
            </div>
          ))}
        </div>

        <div className="min-w-0">
          <div className="flex items-center justify-between px-4 py-2.5 border-b border-[var(--line)]">
            <span className="text-[11px] font-mono text-[var(--muted)] truncate">
              {selected ?? "No file selected"}
            </span>
            {content && (
              <span className="text-[10px] px-2 py-0.5 rounded border border-[var(--line)] bg-[var(--panel-2)] text-[var(--muted)] shrink-0">
                {content.language}
              </span>
            )}
          </div>

          <div className="max-h-[470px] overflow-auto">
            {loadingFile ? (
              <div className="p-6 text-[var(--muted-soft)] text-xs">
                <Loader2 className="w-4 h-4 animate-spin inline mr-2" />
                Loading…
              </div>
            ) : (
              <pre className="p-4 text-[11.5px] font-mono leading-relaxed text-[var(--text)] whitespace-pre">
                {content?.content ?? ""}
              </pre>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

function Empty({ children }: { children: React.ReactNode }) {
  return (
    <div className="glass rounded-xl border border-[var(--line)] px-5 py-14 text-center text-[var(--muted)] text-sm">
      {children}
    </div>
  );
}

function groupByDirectory(files: FileNode[]): Record<string, FileNode[]> {
  const grouped: Record<string, FileNode[]> = {};
  for (const file of files) {
    const directory = file.path.split("/").slice(0, -1).join("/") || ".";
    (grouped[directory] ??= []).push(file);
  }
  return grouped;
}

function basename(path: string) {
  return path.split("/").pop() ?? path;
}

function formatSize(bytes: number) {
  if (bytes < 1024) return `${bytes}B`;
  return `${(bytes / 1024).toFixed(1)}K`;
}
