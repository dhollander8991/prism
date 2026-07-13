import { useNavigate } from "@tanstack/react-router";
import { motion } from "framer-motion";
import {
  CartesianGrid,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
  ZAxis,
} from "recharts";
import type { Category, ChurnRisk, Priority, ThemeStat } from "@/lib/types";
import { usePrefersReducedMotion } from "@/hooks/use-reduced-motion";
import { categoryColor, categoryLabel } from "./chartTheme";

const priorityY: Record<Priority, number> = { P3: 1, P2: 2, P1: 3, P0: 4 };
const yToPriority: Record<number, Priority> = { 1: "P3", 2: "P2", 3: "P1", 4: "P0" };
const churnZ: Record<ChurnRisk, number> = {
  none: 60,
  low: 120,
  medium: 240,
  high: 420,
};
const churnLabel: Record<ChurnRisk, string> = {
  none: "none",
  low: "low",
  medium: "medium",
  high: "high",
};

type Point = ThemeStat & { x: number; y: number; z: number };

function ScatterTooltip({
  active,
  payload,
}: {
  active?: boolean;
  payload?: Array<{ payload: Point }>;
}) {
  if (!active || !payload?.length) return null;
  const p = payload[0]!.payload;
  return (
    <div className="min-w-[200px] rounded-md border border-border bg-popover px-3 py-2 text-[11.5px] shadow-[var(--shadow-card)]">
      <div className="mb-1 font-semibold leading-snug text-foreground">
        {p.label}
      </div>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-0.5 text-muted-foreground">
        <span>{categoryLabel[p.category]}</span>
        <span>·</span>
        <span className="tabular-nums">{p.item_count} items</span>
        <span>·</span>
        <span>{p.priority}</span>
        <span>·</span>
        <span>churn: {churnLabel[p.churn_risk]}</span>
      </div>
    </div>
  );
}

function CategoryLegend() {
  const cats: Category[] = [
    "bug",
    "complaint",
    "ux",
    "feature_request",
    "praise",
    "other",
  ];
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-muted-foreground">
      {cats.map((c) => (
        <span key={c} className="inline-flex items-center gap-1.5">
          <span
            className="h-2 w-2 rounded-full"
            style={{ backgroundColor: categoryColor[c] }}
          />
          {categoryLabel[c]}
        </span>
      ))}
      <span className="ml-2 text-border">|</span>
      <span>bubble = churn risk</span>
    </div>
  );
}

export function PriorityVolumeScatter({
  themes,
  height = 420,
}: {
  themes: ThemeStat[];
  height?: number;
}) {
  const reduced = usePrefersReducedMotion();
  const navigate = useNavigate();

  const points: Point[] = themes.map((t) => ({
    ...t,
    x: t.item_count,
    y: priorityY[t.priority],
    z: churnZ[t.churn_risk],
  }));

  // Group by category so each series gets its own color.
  const byCategory = points.reduce<Record<Category, Point[]>>(
    (acc, p) => {
      (acc[p.category] ||= []).push(p);
      return acc;
    },
    { bug: [], complaint: [], feature_request: [], praise: [], ux: [], other: [] },
  );

  const maxX = Math.max(10, ...points.map((p) => p.x));

  return (
    <div>
      <motion.div
        initial={reduced ? false : { opacity: 0, y: 8 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: reduced ? 0 : 0.35, ease: "easeOut" }}
        className="relative w-full"
        style={{ height }}
      >
        {/* Subtle quadrant label — top-right = high volume × high priority */}
        <div className="pointer-events-none absolute right-3 top-2 z-10 text-right">
          <div className="text-[10px] font-semibold uppercase tracking-[0.08em] text-priority-p0/70">
            high volume · high priority
          </div>
          <div className="text-[10.5px] text-muted-foreground">the roadmap</div>
        </div>
        <ResponsiveContainer width="100%" height="100%">
          <ScatterChart margin={{ top: 24, right: 24, bottom: 32, left: 44 }}>
            <CartesianGrid stroke="var(--border)" strokeWidth={1} />
            <XAxis
              type="number"
              dataKey="x"
              name="Items"
              scale="log"
              domain={[1, Math.ceil(maxX * 1.2)]}
              ticks={[1, 3, 10, 30, 100, 300]}
              tick={{ fontSize: 11, fill: "var(--muted-foreground)" }}
              axisLine={{ stroke: "var(--border)" }}
              tickLine={{ stroke: "var(--border)" }}
              label={{
                value: "items (log)",
                position: "insideBottom",
                offset: -18,
                style: {
                  fill: "var(--muted-foreground)",
                  fontSize: 10.5,
                  textTransform: "uppercase",
                  letterSpacing: "0.08em",
                },
              }}
            />
            <YAxis
              type="number"
              dataKey="y"
              domain={[0.5, 4.5]}
              ticks={[1, 2, 3, 4]}
              tickFormatter={(v: number) => yToPriority[v] ?? ""}
              tick={{ fontSize: 11, fill: "var(--muted-foreground)" }}
              axisLine={{ stroke: "var(--border)" }}
              tickLine={{ stroke: "var(--border)" }}
              width={40}
            />
            <ZAxis type="number" dataKey="z" range={[60, 460]} />
            <Tooltip
              cursor={{ stroke: "var(--border)", strokeDasharray: "3 3" }}
              content={<ScatterTooltip />}
            />
            {(Object.keys(byCategory) as Category[]).map((c) => (
              <Scatter
                key={c}
                name={categoryLabel[c]}
                data={byCategory[c]}
                fill={categoryColor[c]}
                fillOpacity={0.7}
                stroke={categoryColor[c]}
                strokeWidth={1}
                isAnimationActive={!reduced}
                animationDuration={reduced ? 0 : 500}
                onClick={(payload: unknown) => {
                  const p = payload as Point;
                  if (p?.id) navigate({ to: "/insights/$id", params: { id: p.id } });
                }}
                style={{ cursor: "pointer" }}
              />
            ))}
          </ScatterChart>
        </ResponsiveContainer>
      </motion.div>
      <div className="mt-3 border-t border-border pt-3">
        <CategoryLegend />
      </div>
    </div>
  );
}
