import { motion } from "framer-motion";
import { useId } from "react";
import type { Category, ChurnRisk, Priority } from "@/lib/types";

export type Filters = {
  priority: Priority | "all";
  churn: ChurnRisk | "all";
  category: Category | "all";
};

const priorities: Array<Priority | "all"> = ["all", "P0", "P1", "P2", "P3"];
const churns: Array<ChurnRisk | "all"> = ["all", "high", "medium", "low", "none"];
const categories: Array<Category | "all"> = [
  "all",
  "bug",
  "feature_request",
  "praise",
  "complaint",
  "ux",
  "other",
];

function Group<T extends string>({
  label,
  values,
  active,
  onChange,
}: {
  label: string;
  values: T[];
  active: T;
  onChange: (v: T) => void;
}) {
  const layoutId = useId();
  return (
    <div className="flex items-center gap-2">
      <span className="text-[10.5px] font-medium uppercase tracking-[0.06em] text-muted-foreground">
        {label}
      </span>
      <div className="flex items-center rounded-md border border-border bg-card p-0.5 shadow-[var(--shadow-card)]">
        {values.map((v) => {
          const isActive = v === active;
          return (
            <button
              key={v}
              type="button"
              onClick={() => onChange(v)}
              className={`relative h-6 rounded-[5px] px-2.5 text-[11px] font-medium transition-colors ${
                isActive
                  ? "text-background"
                  : "text-muted-foreground hover:text-foreground"
              }`}
            >
              {isActive && (
                <motion.span
                  layoutId={layoutId}
                  className="absolute inset-0 rounded-[5px] bg-foreground"
                  transition={{ type: "spring", stiffness: 500, damping: 38 }}
                />
              )}
              <span className="relative z-10 whitespace-nowrap">
                {v === "all" ? "All" : v.replace("_", " ")}
              </span>
            </button>
          );
        })}
      </div>
    </div>
  );
}

export function FilterBar({
  filters,
  onChange,
}: {
  filters: Filters;
  onChange: (f: Filters) => void;
}) {
  return (
    <div className="flex flex-wrap items-center gap-x-6 gap-y-3 pb-4">
      <Group
        label="Priority"
        values={priorities}
        active={filters.priority}
        onChange={(v) => onChange({ ...filters, priority: v })}
      />
      <Group
        label="Churn"
        values={churns}
        active={filters.churn}
        onChange={(v) => onChange({ ...filters, churn: v })}
      />
      <Group
        label="Category"
        values={categories}
        active={filters.category}
        onChange={(v) => onChange({ ...filters, category: v })}
      />
    </div>
  );
}
