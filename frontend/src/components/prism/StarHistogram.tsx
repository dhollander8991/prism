import type { StarDistribution } from "@/lib/types";
import { motion } from "framer-motion";
import { usePrefersReducedMotion } from "@/hooks/use-reduced-motion";

/**
 * Compact 5-bar histogram. 5★ at top, 1★ at bottom.
 * Reads at a glance — no axes, no legend, no title.
 */
export function StarHistogram({
  distribution,
  className = "",
}: {
  distribution: StarDistribution;
  className?: string;
}) {
  const reduced = usePrefersReducedMotion();
  const order: Array<1 | 2 | 3 | 4 | 5> = [5, 4, 3, 2, 1];
  const max = Math.max(1, ...order.map((s) => distribution[s]));
  const total = order.reduce((sum, s) => sum + distribution[s], 0);

  const barColor = (s: 1 | 2 | 3 | 4 | 5) =>
    s === 1
      ? "var(--priority-p0)"
      : s === 2
        ? "var(--priority-p1)"
        : "color-mix(in oklch, var(--muted-foreground) 55%, transparent)";

  return (
    <div
      className={`w-[168px] ${className}`}
      role="img"
      aria-label={`Star distribution: ${total} reviews`}
    >
      <div className="flex flex-col gap-[6px]">
        {order.map((s) => {
          const value = distribution[s];
          const pct = max === 0 ? 0 : (value / max) * 100;
          return (
            <div key={s} className="flex items-center gap-2">
              <span className="w-4 text-right text-[10.5px] tabular-nums text-muted-foreground">
                {s}★
              </span>
              <div className="relative h-2 flex-1 overflow-hidden rounded-[2px] bg-muted">
                <motion.div
                  initial={{ width: 0 }}
                  animate={{ width: `${pct}%` }}
                  transition={{
                    duration: reduced ? 0 : 0.5,
                    ease: [0.22, 1, 0.36, 1],
                    delay: reduced ? 0 : 0.05 * order.indexOf(s),
                  }}
                  className="h-full rounded-[2px]"
                  style={{ backgroundColor: barColor(s) }}
                />
              </div>
              <span className="w-8 text-right text-[10.5px] tabular-nums text-foreground/80">
                {value}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
