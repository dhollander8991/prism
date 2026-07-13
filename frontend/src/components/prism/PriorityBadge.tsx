import type { Priority } from "@/lib/types";

const styles: Record<Priority, string> = {
  P0: "bg-priority-p0 text-priority-p0-foreground",
  P1: "bg-priority-p1 text-priority-p1-foreground",
  P2: "bg-priority-p2 text-priority-p2-foreground",
  P3: "bg-priority-p3 text-priority-p3-foreground",
};

export function PriorityBadge({ priority }: { priority: Priority }) {
  return (
    <span
      className={`inline-flex h-[18px] min-w-[26px] items-center justify-center rounded-[4px] px-1.5 text-[10.5px] font-semibold uppercase tracking-[0.04em] tabular-nums leading-none ${styles[priority]}`}
    >
      {priority}
    </span>
  );
}
