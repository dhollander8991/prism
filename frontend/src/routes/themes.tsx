import { createFileRoute } from "@tanstack/react-router";
import { Shell } from "@/components/prism/Shell";
import { PriorityVolumeScatter } from "@/components/prism/PriorityVolumeScatter";
import { ThemeTable } from "@/components/prism/ThemeTable";
import { getThemeStats } from "@/lib/api";

export const Route = createFileRoute("/themes")({
  head: () => ({
    meta: [
      { title: "Themes — PRISM" },
      {
        name: "description",
        content:
          "All clustered review themes plotted by priority and volume, sized by churn risk and coloured by category.",
      },
    ],
  }),
  loader: () => getThemeStats(),
  component: ThemesPage,
});

function ThemesPage() {
  const themes = Route.useLoaderData();
  const total = themes.reduce((s: number, t: { item_count: number }) => s + t.item_count, 0);

  return (
    <Shell>
      <div>
        <h1 className="text-lg font-semibold tracking-tight text-foreground">
          Themes overview
        </h1>
        <p className="mt-1 text-[12.5px] text-muted-foreground">
          {themes.length} clustered themes across{" "}
          <span className="tabular-nums">{total.toLocaleString()}</span> reviews.
          Bubble size = churn risk. Click any bubble or row to open its report.
        </p>
      </div>

      <section className="mt-6 rounded-md border border-border bg-card p-4 shadow-[var(--shadow-card)]">
        <PriorityVolumeScatter themes={themes} />
      </section>

      <section className="mt-8">
        <div className="mb-2 flex items-baseline justify-between">
          <h2 className="text-[11px] font-semibold uppercase tracking-[0.08em] text-muted-foreground">
            All themes
          </h2>
          <span className="text-[11px] tabular-nums text-muted-foreground">
            {themes.length} themes
          </span>
        </div>
        <ThemeTable themes={themes} />
      </section>
    </Shell>
  );
}
