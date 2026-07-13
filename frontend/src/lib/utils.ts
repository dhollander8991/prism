import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";
import type { Spike } from "./types";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

/**
 * A spike is worth surfacing to a PM only if it clears both an absolute-volume and a
 * z-score bar. The real corpus produces only floor-marginal spikes (count 5 against
 * near-zero baselines) whose z-scores overstate the signal — none clear this bar, so
 * the alerts strip correctly stays absent. Single source of truth for the threshold.
 */
export function isSignificantSpike(s?: Spike | null): s is Spike {
  return !!s && s.count >= 8 && s.sigma >= 3.0;
}
