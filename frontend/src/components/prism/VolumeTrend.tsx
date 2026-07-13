import { motion } from "framer-motion";
import {
  Area,
  AreaChart,
  CartesianGrid,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { Category, Spike, TrendPoint } from "@/lib/types";
import { usePrefersReducedMotion } from "@/hooks/use-reduced-motion";
import { formatWeekShort } from "./chartTheme";

function TrendTooltip({
  active,
  payload,
  label,
}: {
  active?: boolean;
  payload?: Array<{ value: number }>;
  label?: string;
}) {
  if (!active || !payload?.length) return null;
  return (
    <div className="rounded-md border border-border bg-popover px-2.5 py-1.5 text-[11px] shadow-[var(--shadow-card)]">
      <div className="text-muted-foreground">
        Week of {label ? formatWeekShort(label) : ""}
      </div>
      <div className="font-semibold tabular-nums text-foreground">
        {payload[0]!.value} reviews
      </div>
    </div>
  );
}

/**
 * Small (~140px) area chart of weekly review volume.
 * Red-tinted for bug/complaint themes, neutral otherwise.
 * If `spike` is set, a vertical reference line marks that week and a
 * floating chip annotates the sigma value.
 */
export function VolumeTrend({
  trend,
  spike,
  category,
  height = 140,
}: {
  trend: TrendPoint[];
  spike: Spike | null;
  category: Category;
  height?: number;
}) {
  const reduced = usePrefersReducedMotion();
  const isNegative = category === "bug" || category === "complaint";
  const stroke = isNegative
    ? "var(--priority-p0)"
    : "color-mix(in oklch, var(--foreground) 45%, transparent)";
  const gradientId = `trend-fill-${isNegative ? "neg" : "neu"}`;
  const fillTop = isNegative
    ? "color-mix(in oklch, var(--priority-p0) 32%, transparent)"
    : "color-mix(in oklch, var(--foreground) 18%, transparent)";
  const fillBot = "transparent";

  // Sparse x labels — every 3rd week.
  const ticks = trend.filter((_, i) => i % 3 === 0).map((p) => p.week);

  return (
    <motion.div
      initial={reduced ? false : { opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: reduced ? 0 : 0.35, ease: "easeOut" }}
      className="relative w-full"
      style={{ height }}
    >
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart
          data={trend}
          margin={{ top: 8, right: 8, left: 0, bottom: 0 }}
        >
          <defs>
            <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={fillTop} />
              <stop offset="100%" stopColor={fillBot} />
            </linearGradient>
          </defs>
          <CartesianGrid
            vertical={false}
            stroke="var(--border)"
            strokeWidth={1}
          />
          <XAxis
            dataKey="week"
            ticks={ticks}
            tickFormatter={formatWeekShort}
            tick={{ fontSize: 10.5, fill: "var(--muted-foreground)" }}
            axisLine={{ stroke: "var(--border)" }}
            tickLine={false}
            interval={0}
          />
          <YAxis hide domain={[0, "dataMax + 2"]} />
          <Tooltip
            cursor={{ stroke: "var(--border)", strokeWidth: 1 }}
            content={<TrendTooltip />}
          />
          {spike && (
            <ReferenceLine
              x={spike.week}
              stroke="var(--priority-p0)"
              strokeDasharray="3 3"
              strokeWidth={1}
            />
          )}
          <Area
            type="monotone"
            dataKey="count"
            stroke={stroke}
            strokeWidth={1.5}
            fill={`url(#${gradientId})`}
            isAnimationActive={!reduced}
            animationDuration={reduced ? 0 : 500}
            dot={false}
            activeDot={{
              r: 3,
              fill: stroke,
              stroke: "var(--background)",
              strokeWidth: 1,
            }}
          />
        </AreaChart>
      </ResponsiveContainer>
      {spike && (
        <div
          className="pointer-events-none absolute right-2 top-2 inline-flex items-center gap-1 rounded-sm border border-priority-p0/40 bg-priority-p0/10 px-1.5 py-0.5 text-[10.5px] font-medium text-priority-p0"
        >
          {spike.sigma.toFixed(1)}σ spike · week of {formatWeekShort(spike.week)}
        </div>
      )}
    </motion.div>
  );
}
