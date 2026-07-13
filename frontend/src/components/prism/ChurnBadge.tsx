import type { ChurnRisk } from "@/lib/types";

const styles: Record<ChurnRisk, string> = {
  high: "border-churn-high/40 text-churn-high",
  medium: "border-churn-med/50 text-churn-med",
  low: "border-border-strong text-muted-foreground",
  none: "border-border text-muted-foreground/70",
};

const label: Record<ChurnRisk, string> = {
  high: "churn · high",
  medium: "churn · med",
  low: "churn · low",
  none: "churn · none",
};

export function ChurnBadge({ churn }: { churn: ChurnRisk }) {
  return (
    <span
      className={`inline-flex h-[18px] items-center rounded-[4px] border bg-transparent px-1.5 text-[10.5px] font-medium leading-none ${styles[churn]}`}
    >
      {label[churn]}
    </span>
  );
}
