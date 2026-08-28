"use client";

import { useEffect, useRef, useState } from "react";
import { ArrowDownToLine, Terminal } from "lucide-react";

import { cn } from "@/lib/utils";
import { api, streamEvents, type RunEvent } from "@/lib/api";

/**
 * Log levels climb the ramp rather than changing hue: debug recedes, errors are
 * full-contrast and bold, a stage boundary is full-contrast and spaced out so it
 * reads as a heading in the stream.
 */
const LEVEL_COLOR: Record<string, string> = {
  debug: "text-[var(--muted-soft)]",
  info: "text-[var(--muted)]",
  warning: "text-[var(--text)]",
  error: "font-semibold text-[var(--text)]",
  stage: "font-semibold uppercase tracking-[0.08em] text-[var(--text)]",
};

export default function LiveLog({
  runId,
  onRunSettled,
}: {
  runId: string;
  /** Fired when the server closes the stream, which means the run paused or ended. */
  onRunSettled?: () => void;
}) {
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [connected, setConnected] = useState(false);
  const [follow, setFollow] = useState(true);

  const bottom = useRef<HTMLDivElement>(null);
  const settledRef = useRef(onRunSettled);
  settledRef.current = onRunSettled;

  useEffect(() => {
    let cancelled = false;
    let stop: (() => void) | undefined;

    // Load history first so the terminal is never empty, then attach the stream
    // from the last id we already have.
    (async () => {
      let lastId = 0;
      try {
        const history = await api.log(runId);
        if (cancelled) return;
        setEvents(history);
        lastId = history.at(-1)?.id ?? 0;
      } catch {
        /* the stream replays history too, so this is only a head start */
      }

      if (cancelled) return;
      setConnected(true);

      stop = streamEvents(
        runId,
        {
          onEvent: (event) =>
            setEvents((previous) =>
              previous.some((existing) => existing.id === event.id)
                ? previous
                : [...previous, event],
            ),
          onEnd: () => {
            setConnected(false);
            settledRef.current?.();
          },
          onError: () => setConnected(false),
        },
        lastId,
      );
    })();

    return () => {
      cancelled = true;
      stop?.();
    };
  }, [runId]);

  useEffect(() => {
    if (follow) bottom.current?.scrollIntoView({ block: "end" });
  }, [events, follow]);

  return (
    <div className="glass rounded-xl border border-[var(--line)] overflow-hidden">
      <div className="flex items-center justify-between px-5 py-3 border-b border-[var(--line)]">
        <div className="flex items-center gap-2">
          <Terminal className="w-3.5 h-3.5 text-[var(--muted)]" />
          <span className="text-[var(--muted)] text-xs font-medium uppercase tracking-wider">
            Activity
          </span>
          <span
            className={cn(
              "ml-1 w-1.5 h-1.5 rounded-full",
              connected
                ? "animate-pulse bg-[var(--text)]"
                : "border border-[var(--muted-soft)] bg-transparent",
            )}
            title={connected ? "Streaming" : "Not streaming"}
          />
        </div>

        <button
          onClick={() => setFollow((value) => !value)}
          className={cn(
            "inline-flex items-center gap-1.5 text-[11px] px-2 py-1 rounded-md border transition-colors",
            follow
              ? "border-[var(--line-strong)] bg-[var(--panel-2)] font-medium text-[var(--text)]"
              : "border-[var(--line)] text-[var(--muted)] hover:border-[var(--line-strong)] hover:text-[var(--text)]",
          )}
        >
          <ArrowDownToLine className="w-3 h-3" />
          Follow
        </button>
      </div>

      <div
        className="h-[380px] overflow-y-auto px-5 py-3 font-mono text-[11.5px] leading-relaxed"
        onWheel={() => setFollow(false)}
      >
        {events.length === 0 ? (
          <p className="text-[var(--muted-soft)]">Waiting for the first log line…</p>
        ) : (
          events.map((event) => (
            <div key={event.id} className="flex gap-3 py-0.5">
              <span className="text-[var(--muted-soft)] shrink-0">{time(event.ts)}</span>
              {event.stage && (
                <span className="text-[var(--muted-soft)] shrink-0 w-[110px] truncate">{event.stage}</span>
              )}
              <span className={cn("whitespace-pre-wrap break-words", LEVEL_COLOR[event.level])}>
                {event.message}
              </span>
            </div>
          ))
        )}
        <div ref={bottom} />
      </div>
    </div>
  );
}

function time(iso: string) {
  const parsed = new Date(iso);
  return Number.isNaN(parsed.getTime()) ? "--:--:--" : parsed.toLocaleTimeString("en-GB");
}
