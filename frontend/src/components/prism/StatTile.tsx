import { useEffect, useRef, useState } from "react";
import { usePrefersReducedMotion } from "@/hooks/use-reduced-motion";

function useCountUp(target: number, durationMs = 600, enabled = true) {
  const [value, setValue] = useState(enabled ? 0 : target);
  const started = useRef(false);
  useEffect(() => {
    if (!enabled) {
      setValue(target);
      return;
    }
    if (started.current) return;
    started.current = true;
    const start = performance.now();
    let raf = 0;
    const tick = (t: number) => {
      const p = Math.min(1, (t - start) / durationMs);
      const eased = 1 - Math.pow(1 - p, 3);
      setValue(Math.round(eased * target));
      if (p < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [target, durationMs, enabled]);
  return value;
}

export function StatTile({
  label,
  value,
  accent,
}: {
  label: string;
  value: number;
  accent?: "p0" | "churn" | "neutral";
}) {
  const reduced = usePrefersReducedMotion();
  const animated = useCountUp(value, 650, !reduced);
  const valueColor =
    accent === "p0"
      ? "text-priority-p0"
      : accent === "churn"
        ? "text-churn-high"
        : "text-foreground";
  return (
    <div className="rounded-md border border-border bg-card px-4 py-3.5 shadow-[var(--shadow-card)]">
      <div className="text-[10.5px] font-medium uppercase tracking-[0.06em] text-muted-foreground">
        {label}
      </div>
      <div
        className={`mt-1.5 text-[26px] font-semibold leading-none tabular-nums tracking-tight ${valueColor}`}
      >
        {animated.toLocaleString()}
      </div>
    </div>
  );
}
