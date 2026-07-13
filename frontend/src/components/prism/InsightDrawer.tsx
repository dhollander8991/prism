import { useEffect } from "react";
import { createPortal } from "react-dom";
import { AnimatePresence, motion } from "framer-motion";
import { X, ArrowRight } from "lucide-react";
import type { InsightDetail } from "@/lib/types";
import { PriorityBadge } from "./PriorityBadge";
import { ChurnBadge } from "./ChurnBadge";
import { UrgencyChip } from "./UrgencyChip";
import { FindingCard } from "./FindingCard";
import { StarHistogram } from "./StarHistogram";
import { VolumeTrend } from "./VolumeTrend";
import { formatWeekShort } from "./chartTheme";
import { isSignificantSpike } from "@/lib/utils";
import { usePrefersReducedMotion } from "@/hooks/use-reduced-motion";

const TREND_WINDOW_WEEKS = 26;

function formatDate(iso: string) {
  try {
    return new Date(iso).toLocaleString(undefined, {
      month: "short",
      day: "numeric",
      hour: "numeric",
      minute: "2-digit",
    });
  } catch {
    return iso;
  }
}

export function InsightDrawer({
  insight,
  onClose,
}: {
  insight: InsightDetail | null;
  onClose: () => void;
}) {
  const reduced = usePrefersReducedMotion();
  const open = insight !== null;

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = prev;
    };
  }, [open, onClose]);

  if (typeof document === "undefined") return null;

  return createPortal(
    <AnimatePresence>
      {open && insight && (
        <motion.div
          key="drawer-root"
          className="fixed inset-0 z-50"
          initial={false}
        >
          <motion.div
            key="backdrop"
            className="absolute inset-0 bg-[rgba(0,0,0,0.28)] backdrop-blur-[3px]"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: reduced ? 0 : 0.22, ease: "easeOut" }}
            onClick={onClose}
          />
          <motion.aside
            key="panel"
            role="dialog"
            aria-modal="true"
            aria-label={insight.title}
            className="absolute inset-y-0 right-0 flex w-full flex-col bg-background shadow-[var(--shadow-drawer)] md:min-w-[640px] md:max-w-[52vw]"
            initial={reduced ? { x: 0 } : { x: "100%" }}
            animate={{ x: 0 }}
            exit={reduced ? { x: 0, opacity: 0 } : { x: "100%" }}
            transition={
              reduced
                ? { duration: 0 }
                : { type: "spring", stiffness: 300, damping: 32 }
            }
          >
            <div className="flex items-center justify-between border-b border-border bg-card/70 px-6 py-3 backdrop-blur">
              <div className="flex items-center gap-2 text-[11px] text-muted-foreground">
                <span className="tabular-nums">
                  {insight.item_count.toLocaleString()} items
                </span>
                <span>·</span>
                <span>{formatDate(insight.generated_at)}</span>
              </div>
              <button
                type="button"
                onClick={onClose}
                className="inline-flex h-7 w-7 items-center justify-center rounded-md border border-border bg-card text-muted-foreground transition-colors hover:text-foreground"
                aria-label="Close"
              >
                <X size={14} />
              </button>
            </div>

            <div className="flex-1 overflow-y-auto">
              <motion.div
                className="mx-auto max-w-[720px] px-6 py-8"
                initial="hidden"
                animate="show"
                variants={{
                  hidden: {},
                  show: {
                    transition: {
                      staggerChildren: reduced ? 0 : 0.04,
                      delayChildren: reduced ? 0 : 0.08,
                    },
                  },
                }}
              >
                <motion.header
                  variants={{
                    hidden: { opacity: 0, y: 8 },
                    show: { opacity: 1, y: 0 },
                  }}
                  transition={{ duration: 0.22, ease: "easeOut" }}
                >
                  <div className="flex items-start gap-3">
                    <div className="pt-[3px]">
                      <PriorityBadge priority={insight.priority} />
                    </div>
                    <h1 className="text-[22px] font-semibold leading-[1.2] tracking-tight text-foreground">
                      {insight.title}
                    </h1>
                  </div>
                  <div className="mt-3 flex flex-col gap-4 pl-[38px] sm:flex-row sm:items-start sm:justify-between">
                    <div className="flex flex-wrap items-center gap-x-3 gap-y-2 text-[11.5px] text-muted-foreground">
                      <ChurnBadge churn={insight.churn_risk} />
                      <span>·</span>
                      <span>{insight.affected_surface}</span>
                    </div>
                    {insight.star_distribution &&
                      Object.values(insight.star_distribution).reduce(
                        (s, n) => s + n,
                        0,
                      ) > 0 && (
                        <div className="sm:pt-0.5">
                          <StarHistogram
                            distribution={insight.star_distribution}
                          />
                        </div>
                      )}
                  </div>
                </motion.header>

                <motion.div
                  variants={{
                    hidden: { opacity: 0, y: 8 },
                    show: { opacity: 1, y: 0 },
                  }}
                  transition={{ duration: 0.22, ease: "easeOut" }}
                  className="mt-6 space-y-3 rounded-md border border-border bg-surface p-5"
                >
                  <div>
                    <div className="text-[10px] font-semibold uppercase tracking-[0.08em] text-muted-foreground">
                      Priority rationale
                    </div>
                    <p className="mt-1.5 text-[13.5px] leading-relaxed text-foreground/90">
                      {insight.priority_rationale}
                    </p>
                  </div>
                  <div className="pt-1">
                    <div className="text-[10px] font-semibold uppercase tracking-[0.08em] text-muted-foreground">
                      Churn rationale
                    </div>
                    <p className="mt-1.5 text-[13.5px] leading-relaxed text-foreground/90">
                      {insight.churn_rationale}
                    </p>
                  </div>
                </motion.div>

                {insight.trend && insight.trend.length > 0 && (() => {
                  const full = insight.trend;
                  // The API returns the full per-theme series (up to ~300 weeks); show
                  // only a trailing window for readability, with the full range captioned.
                  const windowed = full.slice(-TREND_WINDOW_WEEKS);
                  const total = windowed.reduce((s, p) => s + p.count, 0);
                  // Show the spike marker only if it's significant AND falls inside the
                  // visible window — otherwise the chip would reference an off-chart week.
                  const spikeInWindow =
                    isSignificantSpike(insight.spike) &&
                    windowed.some((p) => p.week === insight.spike!.week)
                      ? insight.spike
                      : null;
                  return (
                    <motion.div
                      variants={{
                        hidden: { opacity: 0, y: 8 },
                        show: { opacity: 1, y: 0 },
                      }}
                      transition={{ duration: 0.22, ease: "easeOut" }}
                      className="mt-5 rounded-md border border-border bg-card p-4 shadow-[var(--shadow-card)]"
                    >
                      <div className="mb-2 flex items-baseline justify-between">
                        <div className="text-[10px] font-semibold uppercase tracking-[0.08em] text-muted-foreground">
                          Weekly volume · last {windowed.length} weeks
                        </div>
                        <div className="text-[10.5px] tabular-nums text-muted-foreground">
                          {total} total
                        </div>
                      </div>
                      <VolumeTrend
                        trend={windowed}
                        spike={spikeInWindow}
                        category={insight.category}
                      />
                      {full.length > windowed.length && (
                        <div className="mt-2 text-[10px] text-muted-foreground">
                          Full history: {formatWeekShort(full[0]!.week)} –{" "}
                          {formatWeekShort(full[full.length - 1]!.week)} ({full.length}{" "}
                          weeks)
                        </div>
                      )}
                    </motion.div>
                  );
                })()}


                <div className="mt-9">
                  <motion.div
                    variants={{
                      hidden: { opacity: 0, y: 8 },
                      show: { opacity: 1, y: 0 },
                    }}
                    transition={{ duration: 0.22, ease: "easeOut" }}
                    className="mb-3 flex items-baseline justify-between"
                  >
                    <h2 className="text-[12px] font-semibold uppercase tracking-[0.08em] text-muted-foreground">
                      Findings
                    </h2>
                    <span className="text-[11px] text-muted-foreground">
                      {insight.findings.length}{" "}
                      {insight.findings.length === 1 ? "claim" : "claims"}
                    </span>
                  </motion.div>
                  <div className="space-y-3">
                    {insight.findings.map((f, i) => (
                      <motion.div
                        key={i}
                        variants={{
                          hidden: { opacity: 0, y: 8 },
                          show: { opacity: 1, y: 0 },
                        }}
                        transition={{ duration: 0.22, ease: "easeOut" }}
                      >
                        <FindingCard finding={f} />
                      </motion.div>
                    ))}
                  </div>
                </div>

                <div className="mt-9">
                  <motion.h2
                    variants={{
                      hidden: { opacity: 0, y: 8 },
                      show: { opacity: 1, y: 0 },
                    }}
                    transition={{ duration: 0.22, ease: "easeOut" }}
                    className="mb-3 text-[12px] font-semibold uppercase tracking-[0.08em] text-muted-foreground"
                  >
                    Recommended actions
                  </motion.h2>
                  <motion.ul
                    variants={{
                      hidden: { opacity: 0, y: 8 },
                      show: { opacity: 1, y: 0 },
                    }}
                    transition={{ duration: 0.22, ease: "easeOut" }}
                    className="divide-y divide-border overflow-hidden rounded-md border border-border bg-card shadow-[var(--shadow-card)]"
                  >
                    {insight.recommended_actions.map((a, i) => (
                      <li
                        key={i}
                        className="flex items-start gap-3 px-4 py-3.5"
                      >
                        <ArrowRight
                          size={14}
                          className="mt-[3px] shrink-0 text-muted-foreground"
                        />
                        <p className="flex-1 text-[13.5px] leading-relaxed text-foreground/90">
                          {a.action}
                        </p>
                        <UrgencyChip urgency={a.urgency} />
                      </li>
                    ))}
                  </motion.ul>
                </div>
              </motion.div>
            </div>
          </motion.aside>
        </motion.div>
      )}
    </AnimatePresence>,
    document.body,
  );
}
