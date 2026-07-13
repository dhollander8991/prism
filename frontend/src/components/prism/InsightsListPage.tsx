import { useMemo, useState } from "react";
import { AnimatePresence } from "framer-motion";
import { Shell } from "@/components/prism/Shell";
import { StatTile } from "@/components/prism/StatTile";
import { FilterBar, type Filters } from "@/components/prism/FilterBar";
import { InsightRow } from "@/components/prism/InsightRow";
import { SpikeAlertsStrip } from "@/components/prism/SpikeAlertsStrip";
import { headerStats } from "@/lib/mockData";
import type { Insight, Priority } from "@/lib/types";

const priorityOrder: Record<Priority, number> = { P0: 0, P1: 1, P2: 2, P3: 3 };

export function InsightsListPage({
  insights,
  activeId,
}: {
  insights: Insight[];
  activeId: string | null;
}) {
  const [filters, setFilters] = useState<Filters>({
    priority: "all",
    churn: "all",
    category: "all",
  });

  const visible = useMemo(() => {
    return insights
      .filter((i) =>
        filters.priority === "all" ? true : i.priority === filters.priority,
      )
      .filter((i) => (filters.churn === "all" ? true : i.churn_risk === filters.churn))
      .filter((i) =>
        filters.category === "all" ? true : i.category === filters.category,
      )
      .slice()
      .sort((a, b) => {
        const p = priorityOrder[a.priority] - priorityOrder[b.priority];
        if (p !== 0) return p;
        return b.item_count - a.item_count;
      });
  }, [insights, filters]);

  return (
    <Shell>
      <SpikeAlertsStrip insights={insights} />
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">

        <StatTile label="Total feedback" value={headerStats.totalReviews} />
        <StatTile label="Themes" value={headerStats.totalThemes} />
        <StatTile label="P0 issues" value={headerStats.p0Count} accent="p0" />
        <StatTile
          label="High churn risk"
          value={headerStats.highChurnCount}
          accent="churn"
        />
      </div>

      <div className="mt-8">
        <FilterBar filters={filters} onChange={setFilters} />
        <div className="overflow-hidden rounded-md border border-border bg-card shadow-[var(--shadow-card)]">
          {visible.length === 0 ? (
            <div className="px-4 py-10 text-center text-sm text-muted-foreground">
              No insights match the current filters.
            </div>
          ) : (
            <AnimatePresence initial={false}>
              {visible.map((i) => (
                <InsightRow
                  key={i.id}
                  insight={i}
                  active={i.id === activeId}
                />
              ))}
            </AnimatePresence>
          )}
        </div>
        <div className="mt-3 text-[11px] tabular-nums text-muted-foreground">
          {visible.length} of {insights.length} insights
        </div>
      </div>
    </Shell>
  );
}
