import type { Urgency } from "@/lib/types";

const styles: Record<Urgency, string> = {
  immediate: "border-urgency-immediate/40 text-urgency-immediate",
  this_sprint: "border-urgency-sprint/50 text-urgency-sprint",
  next_quarter: "border-border-strong text-muted-foreground",
};

const label: Record<Urgency, string> = {
  immediate: "immediate",
  this_sprint: "this sprint",
  next_quarter: "next quarter",
};

export function UrgencyChip({ urgency }: { urgency: Urgency }) {
  return (
    <span
      className={`inline-flex h-[18px] items-center rounded-[4px] border bg-transparent px-1.5 text-[10.5px] font-medium uppercase tracking-[0.04em] leading-none ${styles[urgency]}`}
    >
      {label[urgency]}
    </span>
  );
}
