import { Braces } from "lucide-react";

/**
 * The canonical artifact, exactly as the API returned it.
 *
 * The panel above this is a reading of the document; this is the document. Both
 * are the *same object* — `RunDetail.prd` or `RunDetail.architecture`, already
 * fetched, never re-requested and never stored a second time — so the two can
 * never drift and there is nothing here to keep in sync.
 *
 * Nothing is selected, renamed, reordered or dropped on the way through:
 * `JSON.stringify` is given the parsed response whole, and the only thing it
 * changes is whitespace. That is the point — a reader opening this is checking
 * what the model actually produced, and a view that tidied the payload first
 * would be the one thing that could not answer that question.
 *
 * Collapsed by default. It is here for transparency and debugging, not as the
 * way the document is meant to be read.
 */
export default function RawJson({
  value,
  label = "raw JSON",
}: {
  value: object;
  label?: string;
}) {
  const text = JSON.stringify(value, null, 2);
  const fields = Object.keys(value).length;

  return (
    <details className="mt-5 border-t border-[var(--line)] pt-3">
      <summary className="flex cursor-pointer list-none items-center gap-2 text-[var(--muted)] transition-colors hover:text-[var(--text)]">
        <Braces className="h-3 w-3 shrink-0" strokeWidth={1.9} />
        <span className="font-mono text-[11px]">View {label}</span>
        <span className="font-mono text-[10px] text-[var(--muted-soft)]">
          {fields} field{fields === 1 ? "" : "s"} · {formatBytes(text.length)}
        </span>
      </summary>

      <pre className="mt-2.5 max-h-[28rem] overflow-auto rounded-md border border-[var(--line)] bg-[var(--panel-2)] p-3 font-mono text-[10.5px] leading-relaxed text-[var(--muted)]">
        {text}
      </pre>
    </details>
  );
}

function formatBytes(length: number): string {
  return length < 1024 ? `${length} B` : `${(length / 1024).toFixed(1)} kB`;
}
