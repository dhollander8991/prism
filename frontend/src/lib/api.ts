import type { Insight, InsightDetail, ThemeStat } from "./types";
import { insightsList } from "./mockData";

const API_BASE = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

export async function getInsights(): Promise<Insight[]> {
  const res = await fetch(`${API_BASE}/api/v1/insights`);
  if (!res.ok) {
    throw new Error(`Failed to fetch insights: ${res.status}`);
  }

  const data = await res.json();

  return data.insights;
}

export async function getInsight(id: string): Promise<InsightDetail> {
  const res = await fetch(`${API_BASE}/api/v1/insights/${id}`);
  if (!res.ok) {
    throw new Error(`Failed to fetch insight ${id}: ${res.status}`);
  }

  return res.json();
}

export async function getThemeStats(): Promise<ThemeStat[]> {
  return insightsList.map((i) => ({
    id: i.id,
    label: i.label,
    category: i.category,
    priority: i.priority,
    churn_risk: i.churn_risk,
    item_count: i.item_count,
  }));
}
