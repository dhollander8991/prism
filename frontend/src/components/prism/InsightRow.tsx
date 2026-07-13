import { Link } from "@tanstack/react-router";
import { motion } from "framer-motion";
import type { Insight } from "@/lib/types";
import { PriorityBadge } from "./PriorityBadge";
import { ChurnBadge } from "./ChurnBadge";

export function InsightRow({
  insight,
  active,
}: {
  insight: Insight;
  active: boolean;
}) {
  const isP0 = insight.priority === "P0";
  return (
    <motion.div
      layout="position"
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -4 }}
      transition={{ duration: 0.18, ease: "easeOut" }}
      whileHover={{ y: -1 }}
    >
      <Link
        to="/insights/$id"
        params={{ id: insight.id }}
        className={`group relative block cursor-pointer border-b border-border px-4 py-3.5 transition-colors duration-150 ease-out hover:bg-muted/60 ${
          active ? "bg-muted/70" : ""
        }`}
      >
        {isP0 && (
          <span
            aria-hidden
            className="absolute inset-y-0 left-0 w-[3px] bg-priority-p0"
          />
        )}
        {active && !isP0 && (
          <span
            aria-hidden
            className="absolute inset-y-0 left-0 w-[3px] bg-foreground/70"
          />
        )}
        <div className="flex items-center gap-3">
          <PriorityBadge priority={insight.priority} />
          <span className="flex-1 truncate text-[14px] font-medium tracking-tight text-foreground">
            {insight.title}
          </span>
          <span className="text-[11px] tabular-nums text-muted-foreground">
            {insight.item_count.toLocaleString()} items
          </span>
          <ChurnBadge churn={insight.churn_risk} />
        </div>
        <p className="mt-1 line-clamp-1 pl-[38px] text-[12px] leading-relaxed text-muted-foreground/90">
          {insight.priority_rationale}
        </p>
      </Link>
    </motion.div>
  );
}
