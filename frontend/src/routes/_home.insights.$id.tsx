import { createFileRoute } from "@tanstack/react-router";
import { getInsight } from "@/lib/api";

export const Route = createFileRoute("/_home/insights/$id")({
  loader: ({ params }) => getInsight(params.id),
  head: ({ loaderData }) => ({
    meta: loaderData
      ? [
          { title: `${loaderData.title} — PRISM` },
          { name: "description", content: loaderData.priority_rationale },
        ]
      : [
          { title: "Insight not found — PRISM" },
          { name: "robots", content: "noindex" },
        ],
  }),
  component: () => null,
});
