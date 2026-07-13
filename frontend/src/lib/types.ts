export type Priority = "P0" | "P1" | "P2" | "P3";
export type ChurnRisk = "high" | "medium" | "low" | "none";
export type Urgency = "immediate" | "this_sprint" | "next_quarter";
export type Category =
  | "bug"
  | "feature_request"
  | "praise"
  | "complaint"
  | "ux"
  | "other";

export interface Insight {
  id: string;
  cluster_id: string;
  title: string;
  label: string;
  priority: Priority;
  priority_rationale: string;
  churn_risk: ChurnRisk;
  item_count: number;
  generated_at: string;
  /** Not returned by the real API list endpoint, but useful for the Themes screen.
   *  Safe to drop when wiring: the Themes screen is the only reader. */
  category: Category;
  /** The theme's most significant historical volume spike, or null. Returned by both
   *  the list and detail endpoints. Guard before use — see isSignificantSpike. */
  spike?: Spike | null;
}

export interface Evidence {
  id: string;
  text: string;
  stars: number;
  country: string;
}

export interface Finding {
  claim: string;
  evidence: Evidence[];
}

export interface RecommendedAction {
  action: string;
  urgency: Urgency;
}

export type StarDistribution = {
  1: number;
  2: number;
  3: number;
  4: number;
  5: number;
};

export interface TrendPoint {
  /** ISO date of the Monday of the week, e.g. "2026-05-04" */
  week: string;
  count: number;
}

export interface Spike {
  week: string;
  /** The rolling Z-score. For a flat baseline this is a display magnitude, not a true σ. */
  sigma: number;
  count: number;
  baseline_mean: number;
}

export interface InsightDetail extends Insight {
  findings: Finding[];
  recommended_actions: RecommendedAction[];
  affected_surface: string;
  churn_rationale: string;
  // Optional: a theme may have no trend/stars (guard before rendering). `spike` is
  // inherited from Insight. `trend` is the FULL per-theme weekly series (up to ~300
  // weeks, 2017-2026) — slice to a display window in the UI.
  star_distribution?: StarDistribution;
  trend?: TrendPoint[];
}

export interface ThemeStat {
  id: string;
  label: string;
  category: Category;
  priority: Priority;
  churn_risk: ChurnRisk;
  item_count: number;
}
