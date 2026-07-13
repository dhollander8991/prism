import { Link } from "@tanstack/react-router";
import { motion } from "framer-motion";
import { AlertTriangle } from "lucide-react";
import type { Insight, Spike } from "@/lib/types";
import { isSignificantSpike } from "@/lib/utils";
import { usePrefersReducedMotion } from "@/hooks/use-reduced-motion";

export function SpikeAlertsStrip({ insights }: { insights: Insight[] }) {
  const reduced = usePrefersReducedMotion();

  // Only surface spikes that clear the significance bar (count >= 8 AND z >= 3.0).
  // On the real corpus nothing qualifies, so the strip renders nothing — which is the
  // correct behaviour, not an "all clear" empty state.
  const spikes = insights
    .filter((i): i is Insight & { spike: Spike } => isSignificantSpike(i.spike))
    .sort((a, b) => b.spike.sigma - a.spike.sigma);
  if (spikes.length === 0) return null;

  const shown = spikes.slice(0, 3);
  const extra = spikes.length - shown.length;

  return (
    <motion.div
      initial={reduced ? false : { opacity: 0, y: -4 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: reduced ? 0 : 0.25, ease: "easeOut" }}
      className="mb-3 flex flex-wrap items-center gap-x-3 gap-y-1 rounded-md border border-priority-p0/30 bg-priority-p0/[0.06] px-3 py-2 text-[12px]"
      role="alert"
    >
      <span className="inline-flex items-center gap-1.5 font-medium text-priority-p0">
        <AlertTriangle size={13} strokeWidth={2.25} />
        {spikes.length} {spikes.length === 1 ? "theme" : "themes"} spiking
      </span>
      <span className="text-muted-foreground">—</span>
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-muted-foreground">
        {shown.map((s, i) => (
          <span key={s.id} className="inline-flex items-center gap-1">
            {i > 0 && <span className="text-border">·</span>}
            <Link
              to="/insights/$id"
              params={{ id: s.id }}
              className="font-medium text-foreground underline-offset-2 hover:underline"
            >
              {s.label}
            </Link>
            <span className="tabular-nums text-priority-p0">
              ({s.spike.sigma.toFixed(1)}σ)
            </span>
          </span>
        ))}
        {extra > 0 && (
          <span className="text-muted-foreground">+{extra} more</span>
        )}
      </div>
    </motion.div>
  );
}
