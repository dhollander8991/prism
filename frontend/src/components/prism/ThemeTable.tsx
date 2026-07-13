import { useMemo, useState } from "react";
import { useNavigate } from "@tanstack/react-router";
import { ArrowDown, ArrowUp } from "lucide-react";
import type { ChurnRisk, Priority, ThemeStat } from "@/lib/types";
import { PriorityBadge } from "./PriorityBadge";
import { ChurnBadge } from "./ChurnBadge";
import { categoryLabel } from "./chartTheme";

type SortKey = "label" | "category" | "priority" | "churn_risk" | "item_count";
type SortDir = "asc" | "desc";

const priorityOrder: Record<Priority, number> = { P0: 0, P1: 1, P2: 2, P3: 3 };
const churnOrder: Record<ChurnRisk, number> = {
  high: 0,
  medium: 1,
  low: 2,
  none: 3,
};

function cmp(a: ThemeStat, b: ThemeStat, key: SortKey): number {
  switch (key) {
    case "label":
      return a.label.localeCompare(b.label);
    case "category":
      return a.category.localeCompare(b.category);
    case "priority":
      return priorityOrder[a.priority] - priorityOrder[b.priority];
    case "churn_risk":
      return churnOrder[a.churn_risk] - churnOrder[b.churn_risk];
    case "item_count":
      return a.item_count - b.item_count;
  }
}

function SortHeader({
  label,
  active,
  dir,
  onClick,
  align = "left",
}: {
  label: string;
  active: boolean;
  dir: SortDir;
  onClick: () => void;
  align?: "left" | "right";
}) {
  return (
    <th
      className={`px-4 py-2 text-[10.5px] font-semibold uppercase tracking-[0.08em] ${
        align === "right" ? "text-right" : "text-left"
      }`}
    >
      <button
        type="button"
        onClick={onClick}
        className={`inline-flex items-center gap-1 transition-colors ${
          active ? "text-foreground" : "text-muted-foreground hover:text-foreground"
        } ${align === "right" ? "flex-row-reverse" : ""}`}
      >
        {label}
        {active &&
          (dir === "asc" ? (
            <ArrowUp size={11} strokeWidth={2.25} />
          ) : (
            <ArrowDown size={11} strokeWidth={2.25} />
          ))}
      </button>
    </th>
  );
}

export function ThemeTable({ themes }: { themes: ThemeStat[] }) {
  const navigate = useNavigate();
  const [sort, setSort] = useState<{ key: SortKey; dir: SortDir }>({
    key: "item_count",
    dir: "desc",
  });

  const rows = useMemo(() => {
    const sorted = themes.slice().sort((a, b) => cmp(a, b, sort.key));
    return sort.dir === "desc" ? sorted.reverse() : sorted;
  }, [themes, sort]);

  const setKey = (key: SortKey) => {
    setSort((s) =>
      s.key === key
        ? { key, dir: s.dir === "asc" ? "desc" : "asc" }
        : { key, dir: key === "item_count" ? "desc" : "asc" },
    );
  };

  return (
    <div className="overflow-hidden rounded-md border border-border bg-card shadow-[var(--shadow-card)]">
      <table className="w-full text-[13px]">
        <thead className="border-b border-border bg-muted/40">
          <tr>
            <SortHeader
              label="Theme"
              active={sort.key === "label"}
              dir={sort.dir}
              onClick={() => setKey("label")}
            />
            <SortHeader
              label="Category"
              active={sort.key === "category"}
              dir={sort.dir}
              onClick={() => setKey("category")}
            />
            <SortHeader
              label="Priority"
              active={sort.key === "priority"}
              dir={sort.dir}
              onClick={() => setKey("priority")}
            />
            <SortHeader
              label="Churn"
              active={sort.key === "churn_risk"}
              dir={sort.dir}
              onClick={() => setKey("churn_risk")}
            />
            <SortHeader
              label="Items"
              active={sort.key === "item_count"}
              dir={sort.dir}
              onClick={() => setKey("item_count")}
              align="right"
            />
          </tr>
        </thead>
        <tbody>
          {rows.map((t) => (
            <tr
              key={t.id}
              onClick={() => navigate({ to: "/insights/$id", params: { id: t.id } })}
              className="cursor-pointer border-b border-border last:border-0 transition-colors hover:bg-muted/50"
            >
              <td className="px-4 py-2.5 font-medium text-foreground">
                {t.label}
              </td>
              <td className="px-4 py-2.5 text-muted-foreground">
                {categoryLabel[t.category]}
              </td>
              <td className="px-4 py-2.5">
                <PriorityBadge priority={t.priority} />
              </td>
              <td className="px-4 py-2.5">
                <ChurnBadge churn={t.churn_risk} />
              </td>
              <td className="px-4 py-2.5 text-right tabular-nums text-foreground">
                {t.item_count.toLocaleString()}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
