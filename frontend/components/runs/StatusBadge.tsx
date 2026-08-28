import {
  AlertTriangle,
  CheckCircle2,
  Clock,
  Loader2,
  PauseCircle,
  XCircle,
} from "lucide-react";

import { cn } from "@/lib/utils";
import { STATUS_LABELS, type RunStatus } from "@/lib/api";

const META: Record<RunStatus, { cls: string; icon: typeof Clock; spin?: boolean }> = {
  queued: { cls: "text-[var(--muted)] bg-zinc-500/10 border-zinc-500/20", icon: Clock },
  running: { cls: "text-violet-300 bg-violet-500/10 border-violet-500/20", icon: Loader2, spin: true },
  awaiting_pm_review: { cls: "text-amber-300 bg-amber-500/10 border-amber-500/25", icon: PauseCircle },
  awaiting_architecture_review: {
    cls: "text-amber-300 bg-amber-500/10 border-amber-500/25",
    icon: PauseCircle,
  },
  completed: { cls: "text-emerald-400 bg-emerald-500/10 border-emerald-500/20", icon: CheckCircle2 },
  failed: { cls: "text-red-400 bg-red-500/10 border-red-500/20", icon: AlertTriangle },
  cancelled: { cls: "text-[var(--muted)] bg-zinc-500/10 border-zinc-500/20", icon: XCircle },
};

export default function StatusBadge({
  status,
  className,
}: {
  status: RunStatus;
  className?: string;
}) {
  const meta = META[status] ?? META.queued;
  const Icon = meta.icon;

  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 text-[10px] px-2 py-0.5 rounded border font-medium whitespace-nowrap",
        meta.cls,
        className,
      )}
    >
      <Icon className={cn("w-3 h-3", meta.spin && "animate-spin")} />
      {STATUS_LABELS[status] ?? status}
    </span>
  );
}
