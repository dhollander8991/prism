import {
  createFileRoute,
  Outlet,
  useNavigate,
  useParams,
  useRouterState,
} from "@tanstack/react-router";
import { InsightsListPage } from "@/components/prism/InsightsListPage";
import { InsightDrawer } from "@/components/prism/InsightDrawer";
import { getInsights, getInsight } from "@/lib/api";
import { useEffect, useState } from "react";
import type { InsightDetail } from "@/lib/types";

export const Route = createFileRoute("/_home")({
  head: () => ({
    meta: [
      { title: "PRISM — Product Feedback Intelligence" },
      {
        name: "description",
        content:
          "Prioritised, evidence-backed insights from thousands of app store reviews, generated for product managers.",
      },
      { property: "og:title", content: "PRISM — Product Feedback Intelligence" },
      {
        property: "og:description",
        content:
          "Prioritised, evidence-backed insights from thousands of app store reviews.",
      },
    ],
  }),
  loader: () => getInsights(),
  component: HomeLayout,
});

function HomeLayout() {
  const insights = Route.useLoaderData();
  const navigate = useNavigate();
  const params = useParams({ strict: false }) as { id?: string };
  const activeId = params.id ?? null;
  const isDrawerRoute = useRouterState({
    select: (s) => s.location.pathname.startsWith("/insights/"),
  });

  // Cache last-loaded detail so exit animation has content.
  const [detail, setDetail] = useState<InsightDetail | null>(null);
  useEffect(() => {
    let cancelled = false;
    if (activeId) {
      getInsight(activeId).then((d) => {
        if (!cancelled) setDetail(d);
      });
    }
    return () => {
      cancelled = true;
    };
  }, [activeId]);

  const close = () => navigate({ to: "/" });

  return (
    <>
      <InsightsListPage insights={insights} activeId={activeId} />
      <InsightDrawer
        insight={isDrawerRoute ? detail : null}
        onClose={close}
      />
      <Outlet />
    </>
  );
}
