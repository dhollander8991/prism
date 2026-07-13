import type { Category } from "@/lib/types";

/** Category → OKLCH swatch. Kept in one place so charts and legends agree. */
export const categoryColor: Record<Category, string> = {
  bug: "oklch(0.62 0.22 27)",
  complaint: "oklch(0.72 0.17 65)",
  feature_request: "oklch(0.6 0.16 250)",
  ux: "oklch(0.6 0.14 300)",
  praise: "oklch(0.65 0.16 150)",
  other: "oklch(0.62 0.02 260)",
};

export const categoryLabel: Record<Category, string> = {
  bug: "Bug",
  complaint: "Complaint",
  feature_request: "Feature request",
  ux: "UX",
  praise: "Praise",
  other: "Other",
};

/** Format a "2026-06-14" ISO week-start date as "Jun 14". */
export function formatWeekShort(iso: string): string {
  const d = new Date(iso + "T00:00:00Z");
  return d.toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    timeZone: "UTC",
  });
}
