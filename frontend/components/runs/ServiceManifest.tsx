import { FileCode2 } from "lucide-react";

import type { CodeManifest } from "@/lib/api";

/**
 * What the developer agent says it wrote, service by service.
 *
 * This is the manifest, not the disk — `FileExplorer` shows the actual bytes.
 * The manifest is worth its own view because it carries the two things the file
 * tree cannot: why each file exists, and which service it belongs to. The
 * manifest is keyed by slug (see `core/paths.py:slugify`), so `display_name`
 * is what a reader should see and the key is only an identity.
 */
export default function ServiceManifest({ manifest }: { manifest: CodeManifest }) {
  const services = Object.entries(manifest ?? {});

  if (services.length === 0) {
    return (
      <div className="rounded-xl border border-dashed border-[var(--line-strong)] px-5 py-8">
        <p className="font-display text-[14px] font-semibold text-[var(--text)]">
          No code generated yet
        </p>
        <p className="mt-1 max-w-md text-[12.5px] leading-relaxed text-[var(--muted)]">
          The developer agent writes this after the architecture is approved. Each service it
          creates is listed here with the purpose of every file.
        </p>
      </div>
    );
  }

  const totalFiles = services.reduce(
    (sum, [, service]) => sum + (service.files?.length ?? 0),
    0,
  );

  return (
    <div className="space-y-3">
      <p className="font-mono text-[10.5px] uppercase tracking-[0.12em] text-[var(--muted-soft)]">
        {services.length} service{services.length === 1 ? "" : "s"} · {totalFiles} file
        {totalFiles === 1 ? "" : "s"}
      </p>

      {services.map(([slug, service]) => {
        const files = service.files ?? [];
        return (
          <section
            key={slug}
            className="overflow-hidden rounded-xl border border-[var(--line)] bg-[var(--panel)]"
          >
            <header className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 border-b border-[var(--line)] px-4 py-3">
              <div className="min-w-0">
                <h3 className="font-display text-[14px] font-semibold tracking-tight text-[var(--text)]">
                  {service.display_name || slug}
                </h3>
                <p className="mt-0.5 font-mono text-[10px] text-[var(--muted-soft)]">{slug}</p>
              </div>
              <div className="flex shrink-0 items-center gap-2.5">
                {languagesIn(files).map((language) => (
                  <span
                    key={language}
                    className="rounded border border-[var(--line)] bg-[var(--panel-2)] px-1.5 py-0.5 font-mono text-[9.5px] uppercase tracking-[0.08em] text-[var(--muted)]"
                  >
                    {language}
                  </span>
                ))}
                <span className="font-mono text-[10.5px] text-[var(--muted-soft)]">
                  {files.length} file{files.length === 1 ? "" : "s"}
                </span>
              </div>
            </header>

            {files.length > 0 && (
              <ul className="divide-y divide-[var(--line)]">
                {files.map((file) => (
                  <li
                    key={file.file_path}
                    className="flex items-start gap-3 px-4 py-2.5 transition-colors hover:bg-[var(--panel-2)]"
                  >
                    <FileCode2
                      className="mt-[3px] h-3.5 w-3.5 shrink-0 text-[var(--muted-soft)]"
                      strokeWidth={1.8}
                    />
                    <div className="min-w-0 flex-1">
                      <p className="truncate font-mono text-[11.5px] text-[var(--text)]">
                        {file.file_path}
                      </p>
                      {file.description && (
                        <p className="mt-0.5 text-[11.5px] leading-relaxed text-[var(--muted)]">
                          {file.description}
                        </p>
                      )}
                    </div>
                    {file.language && (
                      <span className="shrink-0 font-mono text-[9.5px] uppercase tracking-[0.08em] text-[var(--muted-soft)]">
                        {file.language}
                      </span>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </section>
        );
      })}
    </div>
  );
}

function languagesIn(files: { language: string }[]): string[] {
  const seen: string[] = [];
  for (const file of files) {
    const language = file.language?.trim();
    if (language && !seen.includes(language)) seen.push(language);
  }
  return seen.slice(0, 3);
}
