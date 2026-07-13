import type {
  Insight,
  InsightDetail,
  Evidence,
  StarDistribution,
  TrendPoint,
  Spike,
} from "./types";

const now = "2026-07-12T15:23:22Z";
const earlier = "2026-07-12T09:04:11Z";
const yesterday = "2026-07-11T18:47:03Z";

const genericQuotes: Evidence[] = [
  {
    id: "ev-g1",
    text: "Constantly crashes when I try to open a large page. Have to force quit multiple times a day.",
    stars: 1,
    country: "us",
  },
  {
    id: "ev-g2",
    text: "The app used to be great but recent updates have made it noticeably slower on my iPad.",
    stars: 2,
    country: "gb",
  },
  {
    id: "ev-g3",
    text: "Please add proper offline support. I commute on the subway and can't do anything.",
    stars: 3,
    country: "de",
  },
  {
    id: "ev-g4",
    text: "Font is too small on mobile and there's no way to change it. Accessibility fail.",
    stars: 2,
    country: "au",
  },
  {
    id: "ev-g5",
    text: "Sync between my phone and laptop is unreliable, edits regularly get lost.",
    stars: 2,
    country: "ca",
  },
  {
    id: "ev-g6",
    text: "Love how flexible this is for organising school, work and personal projects in one place.",
    stars: 5,
    country: "us",
  },
];

// ---- The 10 specified insights (verbatim) + 12 plausible additions ----

export const insightsList: Insight[] = [
  {
    id: "7d9940664662b0fbc7d84a7b",
    cluster_id: "cluster_12",
    title: "Login/logout events cause permanent data loss for users",
    label: "Login/logout causes data loss",
    priority: "P0",
    priority_rationale:
      "25 of 29 items are 1-star reviews reporting permanent loss of notes, pages, and workspaces after logout events, with no recovery path via trash or support.",
    churn_risk: "high",
    item_count: 29,
    generated_at: now,
    category: "bug",
  },
  {
    id: "a1b2c3d4e5f6a7b8c9d0e1f2",
    cluster_id: "cluster_03",
    title: "Critical login loop blocking user access and subscriptions",
    label: "Login loop blocks access",
    priority: "P0",
    priority_rationale:
      "50 reviews describe an infinite redirect between the login screen and the app shell, disproportionately affecting paying users whose subscriptions renewed while locked out.",
    churn_risk: "high",
    item_count: 50,
    generated_at: now,
    category: "bug",
  },
  {
    id: "b2c3d4e5f6a7b8c9d0e1f2a3",
    cluster_id: "cluster_07",
    title: "Mobile app freezing and unresponsiveness requires immediate stabilization",
    label: "Mobile freezes / unresponsive",
    priority: "P0",
    priority_rationale:
      "82 reviews report the app becoming unresponsive for 10+ seconds during routine editing, with a clear spike after the most recent iOS release.",
    churn_risk: "medium",
    item_count: 82,
    generated_at: earlier,
    category: "bug",
  },
  {
    id: "c3d4e5f6a7b8c9d0e1f2a3b4",
    cluster_id: "cluster_15",
    title: "Aggressive AI Feature Promotion Degrading Core User Experience",
    label: "AI upsell overwhelms UX",
    priority: "P1",
    priority_rationale:
      "115 reviews complain that AI prompts and upsell banners interrupt writing flow, with several long-time users threatening to switch tools.",
    churn_risk: "medium",
    item_count: 115,
    generated_at: earlier,
    category: "complaint",
  },
  {
    id: "d4e5f6a7b8c9d0e1f2a3b4c5",
    cluster_id: "cluster_09",
    title: "iOS/iPad app crashes, freezes, and auto-refreshes causing unusability",
    label: "iOS/iPad instability",
    priority: "P1",
    priority_rationale:
      "45 reviews cite hard crashes and forced refreshes on iPad specifically, most on iPadOS 18.",
    churn_risk: "medium",
    item_count: 45,
    generated_at: earlier,
    category: "bug",
  },
  {
    id: "e5f6a7b8c9d0e1f2a3b4c5d6",
    cluster_id: "cluster_18",
    title: "Subscription billing failures blocking paid feature access",
    label: "Billing failures",
    priority: "P1",
    priority_rationale:
      "63 reviews describe successful charges from Apple that don't unlock paid features in-app, and slow support resolution.",
    churn_risk: "medium",
    item_count: 63,
    generated_at: yesterday,
    category: "bug",
  },
  {
    id: "f6a7b8c9d0e1f2a3b4c5d6e7",
    cluster_id: "cluster_21",
    title: "Add adjustable font size controls for mobile accessibility",
    label: "Font size controls",
    priority: "P1",
    priority_rationale:
      "49 reviews explicitly request adjustable font sizing on mobile, many citing vision impairments and lack of Dynamic Type support.",
    churn_risk: "low",
    item_count: 49,
    generated_at: yesterday,
    category: "feature_request",
  },
  {
    id: "a7b8c9d0e1f2a3b4c5d6e7f8",
    cluster_id: "cluster_04",
    title: "Implement offline access for content viewing and editing",
    label: "Offline access",
    priority: "P1",
    priority_rationale:
      "30 reviews ask for reliable offline viewing and editing, citing commutes, travel, and unreliable coverage.",
    churn_risk: "low",
    item_count: 30,
    generated_at: yesterday,
    category: "feature_request",
  },
  {
    id: "b8c9d0e1f2a3b4c5d6e7f8a9",
    cluster_id: "cluster_11",
    title: "iPad keyboard and text input bugs",
    label: "iPad keyboard bugs",
    priority: "P2",
    priority_rationale:
      "47 reviews describe cursor jumping, dropped characters, and shortcut collisions when using an external keyboard on iPad.",
    churn_risk: "low",
    item_count: 47,
    generated_at: yesterday,
    category: "bug",
  },
  {
    id: "c9d0e1f2a3b4c5d6e7f8a9b0",
    cluster_id: "cluster_00",
    title: "Positive user sentiment on versatility and organization",
    label: "Positive sentiment",
    priority: "P3",
    priority_rationale:
      "551 reviews praise the app's flexibility across school, work and personal use — useful signal for marketing and retention messaging.",
    churn_risk: "none",
    item_count: 551,
    generated_at: yesterday,
    category: "praise",
  },
  // ---- 12 plausible additions ----
  {
    id: "d0e1f2a3b4c5d6e7f8a9b0c1",
    cluster_id: "cluster_22",
    title: "Cross-device sync conflicts overwrite recent edits",
    label: "Sync conflicts",
    priority: "P1",
    priority_rationale:
      "38 reviews report edits from one device silently overwritten by an older version from another.",
    churn_risk: "medium",
    item_count: 38,
    generated_at: yesterday,
    category: "bug",
  },
  {
    id: "e1f2a3b4c5d6e7f8a9b0c1d2",
    cluster_id: "cluster_23",
    title: "Apple Pencil latency and stroke drop-outs in handwriting mode",
    label: "Apple Pencil latency",
    priority: "P2",
    priority_rationale:
      "22 reviews cite noticeable lag and missed strokes when handwriting on iPad Pro.",
    churn_risk: "low",
    item_count: 22,
    generated_at: yesterday,
    category: "bug",
  },
  {
    id: "f2a3b4c5d6e7f8a9b0c1d2e3",
    cluster_id: "cluster_24",
    title: "Onboarding complexity overwhelms first-time users",
    label: "Onboarding too complex",
    priority: "P2",
    priority_rationale:
      "34 reviews from new users describe abandoning setup within the first session.",
    churn_risk: "medium",
    item_count: 34,
    generated_at: yesterday,
    category: "ux",
  },
  {
    id: "a3b4c5d6e7f8a9b0c1d2e3f4",
    cluster_id: "cluster_25",
    title: "Template gallery renders blank or crashes on open",
    label: "Template gallery bug",
    priority: "P2",
    priority_rationale:
      "19 reviews mention templates failing to load or crashing the app when previewed.",
    churn_risk: "low",
    item_count: 19,
    generated_at: yesterday,
    category: "bug",
  },
  {
    id: "b4c5d6e7f8a9b0c1d2e3f4a5",
    cluster_id: "cluster_26",
    title: "Search is slow and misses results in large workspaces",
    label: "Slow search",
    priority: "P2",
    priority_rationale:
      "41 reviews cite multi-second search delays and missing hits for known page titles.",
    churn_risk: "low",
    item_count: 41,
    generated_at: yesterday,
    category: "complaint",
  },
  {
    id: "c5d6e7f8a9b0c1d2e3f4a5b6",
    cluster_id: "cluster_27",
    title: "Calendar week view is cramped and hard to scan",
    label: "Calendar week UX",
    priority: "P3",
    priority_rationale:
      "17 reviews find week view illegible on smaller screens.",
    churn_risk: "low",
    item_count: 17,
    generated_at: yesterday,
    category: "ux",
  },
  {
    id: "d6e7f8a9b0c1d2e3f4a5b6c7",
    cluster_id: "cluster_28",
    title: "Image uploads fail silently on cellular connections",
    label: "Image upload failures",
    priority: "P2",
    priority_rationale:
      "26 reviews report uploads appearing to succeed but never showing up on other devices.",
    churn_risk: "low",
    item_count: 26,
    generated_at: yesterday,
    category: "bug",
  },
  {
    id: "e7f8a9b0c1d2e3f4a5b6c7d8",
    cluster_id: "cluster_29",
    title: "Dark mode contrast makes body text hard to read",
    label: "Dark mode contrast",
    priority: "P3",
    priority_rationale:
      "12 reviews call out low-contrast greys against near-black backgrounds.",
    churn_risk: "none",
    item_count: 12,
    generated_at: yesterday,
    category: "ux",
  },
  {
    id: "f8a9b0c1d2e3f4a5b6c7d8e9",
    cluster_id: "cluster_30",
    title: "Home screen widgets crash after latest update",
    label: "Widget crashes",
    priority: "P2",
    priority_rationale:
      "24 reviews report widgets showing an error state or disappearing entirely.",
    churn_risk: "low",
    item_count: 24,
    generated_at: yesterday,
    category: "bug",
  },
  {
    id: "a9b0c1d2e3f4a5b6c7d8e9f0",
    cluster_id: "cluster_31",
    title: "Share-link permissions are confusing and error-prone",
    label: "Share permissions unclear",
    priority: "P2",
    priority_rationale:
      "28 reviews describe accidental public shares or collaborators being locked out.",
    churn_risk: "medium",
    item_count: 28,
    generated_at: yesterday,
    category: "ux",
  },
  {
    id: "b0c1d2e3f4a5b6c7d8e9f0a1",
    cluster_id: "cluster_32",
    title: "PDF export formatting breaks tables and code blocks",
    label: "PDF export formatting",
    priority: "P3",
    priority_rationale:
      "15 reviews cite mangled tables and code blocks in exported PDFs.",
    churn_risk: "low",
    item_count: 15,
    generated_at: yesterday,
    category: "bug",
  },
  {
    id: "c1d2e3f4a5b6c7d8e9f0a1b2",
    cluster_id: "cluster_33",
    title: "Keyboard shortcuts are undiscoverable outside power users",
    label: "Shortcut discoverability",
    priority: "P3",
    priority_rationale:
      "9 reviews ask for a visible cheatsheet or command palette hints.",
    churn_risk: "none",
    item_count: 9,
    generated_at: yesterday,
    category: "feature_request",
  },
];

// ---- Trend / spike / stars helpers ---------------------------------------

/** 12 consecutive weeks ending Mon 2026-07-06. */
const WEEKS: string[] = [
  "2026-04-20",
  "2026-04-27",
  "2026-05-04",
  "2026-05-11",
  "2026-05-18",
  "2026-05-25",
  "2026-06-01",
  "2026-06-08",
  "2026-06-15",
  "2026-06-22",
  "2026-06-29",
  "2026-07-06",
];

/** Seeded pseudo-random so mock data is stable across renders. */
function seeded(seed: number) {
  let s = seed >>> 0;
  return () => {
    s = (s * 1664525 + 1013904223) >>> 0;
    return s / 0x100000000;
  };
}

function hashSeed(id: string): number {
  let h = 2166136261;
  for (let i = 0; i < id.length; i++) {
    h ^= id.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return h >>> 0;
}

// DEAD CODE: this spike config and everything below that builds InsightDetail
// (makeTrend/makeStars/dataLossDetail/makeGenericDetail/insightDetailsById/
// getSpikingInsights) are no longer consumed — the app now reads trend/spike/
// star_distribution from the real API. Only `insightsList` and `headerStats` are
// still imported. Safe to delete this whole block in a dedicated cleanup.
const spikeConfig: Record<string, Spike> = {
  // data-loss
  "7d9940664662b0fbc7d84a7b": { week: "2026-06-15", sigma: 4.2, count: 14, baseline_mean: 3.1 },
  // login loop
  a1b2c3d4e5f6a7b8c9d0e1f2: { week: "2026-06-29", sigma: 3.6, count: 12, baseline_mean: 3.0 },
  // mobile freezing (iOS release)
  b2c3d4e5f6a7b8c9d0e1f2a3: { week: "2026-06-08", sigma: 3.1, count: 18, baseline_mean: 6.2 },
  // billing
  e5f6a7b8c9d0e1f2a3b4c5d6: { week: "2026-06-22", sigma: 2.8, count: 11, baseline_mean: 4.0 },
  // sync conflicts
  d0e1f2a3b4c5d6e7f8a9b0c1: { week: "2026-06-15", sigma: 2.5, count: 9, baseline_mean: 3.2 },
  // widget crashes
  f8a9b0c1d2e3f4a5b6c7d8e9: { week: "2026-06-29", sigma: 3.4, count: 10, baseline_mean: 2.6 },
};

function makeTrend(insight: Insight, spike: Spike | null): TrendPoint[] {
  const rnd = seeded(hashSeed(insight.id));
  const total = insight.item_count;
  // Base weekly volume distributes total across 12 weeks with mild noise.
  const base = total / WEEKS.length;
  const noise = Math.max(1, base * 0.35);
  const raw = WEEKS.map((w) => {
    const n = base + (rnd() - 0.5) * 2 * noise;
    return { week: w, count: Math.max(0, n) };
  });
  if (spike) {
    const idx = WEEKS.indexOf(spike.week);
    if (idx !== -1) {
      // Concentrate a chunk of volume into the spike week.
      const spikeShare = Math.min(0.55, 0.18 + spike.sigma * 0.08);
      const spikeAmt = total * spikeShare;
      raw[idx]!.count += spikeAmt;
      // Small residual bump the week after.
      if (idx + 1 < raw.length) raw[idx + 1]!.count += spikeAmt * 0.25;
    }
  }
  // Normalise so the sum roughly matches total.
  const sum = raw.reduce((s, p) => s + p.count, 0);
  const scale = sum > 0 ? total / sum : 1;
  return raw.map((p) => ({ week: p.week, count: Math.round(p.count * scale) }));
}

function makeStars(insight: Insight): StarDistribution {
  const rnd = seeded(hashSeed(insight.id) ^ 0xa5a5a5a5);
  const total = insight.item_count;
  // Weight profile by category / churn.
  let weights: [number, number, number, number, number]; // 1..5
  if (insight.category === "praise") {
    weights = [0.02, 0.03, 0.08, 0.22, 0.65];
  } else if (insight.category === "feature_request") {
    weights = [0.1, 0.15, 0.3, 0.28, 0.17];
  } else if (insight.churn_risk === "high") {
    weights = [0.78, 0.12, 0.05, 0.03, 0.02];
  } else if (insight.category === "bug" || insight.category === "complaint") {
    weights = [0.45, 0.28, 0.15, 0.08, 0.04];
  } else if (insight.category === "ux") {
    weights = [0.22, 0.28, 0.28, 0.15, 0.07];
  } else {
    weights = [0.2, 0.2, 0.2, 0.2, 0.2];
  }
  // Perturb slightly per-insight for realism.
  weights = weights.map((w) => Math.max(0.01, w + (rnd() - 0.5) * 0.05)) as typeof weights;
  const wSum = weights.reduce((s, w) => s + w, 0);
  const raw = weights.map((w) => (w / wSum) * total);
  const rounded = raw.map((n) => Math.round(n));
  // Adjust to hit exact total.
  let diff = total - rounded.reduce((s, n) => s + n, 0);
  let i = 0;
  while (diff !== 0) {
    const step = diff > 0 ? 1 : -1;
    if (rounded[i]! + step >= 0) {
      rounded[i]! += step;
      diff -= step;
    }
    i = (i + 1) % 5;
  }
  return {
    1: rounded[0]!,
    2: rounded[1]!,
    3: rounded[2]!,
    4: rounded[3]!,
    5: rounded[4]!,
  };
}

// ---- Detailed data-loss insight (full fidelity, real quotes) ----



const dataLossDetail: InsightDetail = {
  ...insightsList[0]!,
  affected_surface: "login flow",
  churn_rationale:
    "25 of 29 reviews are 1-star, and multiple users explicitly state they are uninstalling or moving to another app.",
  findings: [
    {
      claim:
        "Users report that after being logged out, all their notes and pages permanently disappear when they log back in.",
      evidence: [
        {
          id: "874f9e90-45a8-4293-a5db-b246cd6e43a5",
          text: "IT DELETED ALL MY PAGES Bro I literally just clicked on one of my pages and it LOGGED ME OUT. When I logged back in all I saw was nothing but one of my pages. GIVE ME MY PAGES BACK",
          stars: 1,
          country: "ca",
        },
        {
          id: "5c0a9c14-2f8e-4a11-a2cc-1b0f5b6bcb01",
          text: "Trash Logged me out for absolutely no reason, and now I've lost all my school notes. Thanks a bunch",
          stars: 1,
          country: "gb",
        },
        {
          id: "3b1e9dab-9d63-4e0e-91cc-2c40df1e0abc",
          text: "Lost everything Do not download. I never leave reviews but this app randomly logged me out one day and when I logged back in everything was gone. I am a university student and I have just lost all my lecture notes with just a month until finals.",
          stars: 1,
          country: "ca",
        },
        {
          id: "0f4c3d2b-7a9e-4b6c-8e5a-1d3f2c7b8a90",
          text: "Deleted everything I was loving the app everything was great then i went to go on one day and it had logged me out and deleted 6 months worth of stuff, asking me to pay for premium to get my old pages back. Really disappointed wont be using it anymore.",
          stars: 1,
          country: "gb",
        },
        {
          id: "2e7d8c1a-4b6f-4b3a-9c1a-9e2b6f4a5c3d",
          text: "Deleted all my important notes I've been using this app for years. I've logged hundreds of volunteer hours and one day the entire workspace disappeared. I contacted support and they said they somehow can't find it?",
          stars: 1,
          country: "us",
        },
      ],
    },
    {
      claim:
        "Support is unable to recover lost workspaces, and users are prompted to pay to restore prior content.",
      evidence: [
        {
          id: "0f4c3d2b-7a9e-4b6c-8e5a-1d3f2c7b8a90",
          text: "Deleted everything I was loving the app everything was great then i went to go on one day and it had logged me out and deleted 6 months worth of stuff, asking me to pay for premium to get my old pages back. Really disappointed wont be using it anymore.",
          stars: 1,
          country: "gb",
        },
        {
          id: "2e7d8c1a-4b6f-4b3a-9c1a-9e2b6f4a5c3d",
          text: "Deleted all my important notes I've been using this app for years. I've logged hundreds of volunteer hours and one day the entire workspace disappeared. I contacted support and they said they somehow can't find it?",
          stars: 1,
          country: "us",
        },
      ],
    },
  ],
  recommended_actions: [
    {
      action:
        "Audit the session-expiry and re-auth flow for workspace unbinding — a stale token appears to associate the account with an empty workspace on re-login.",
      urgency: "immediate",
    },
    {
      action:
        "Surface a recoverable Trash / version history in the mobile UI so accidentally-hidden content is visibly retrievable without contacting support.",
      urgency: "this_sprint",
    },
    {
      action:
        "Build internal tooling for support to locate and rebind orphaned workspaces to the correct account.",
      urgency: "next_quarter",
    },
  ],
  // Exact star distribution reflecting priority_rationale: 25 of 29 are 1-star.
  star_distribution: { 1: 25, 2: 2, 3: 1, 4: 1, 5: 0 },
  trend: (() => {
    const insight = insightsList[0]!;
    const spike = spikeConfig[insight.id] ?? null;
    return makeTrend(insight, spike);
  })(),
  spike: spikeConfig["7d9940664662b0fbc7d84a7b"]!,
};

// ---- Generic detail builder for the other insights ----

function makeGenericDetail(insight: Insight): InsightDetail {
  const evidencePool = genericQuotes;
  const pick = (n: number, offset = 0): Evidence[] =>
    Array.from({ length: n }, (_, i) => evidencePool[(i + offset) % evidencePool.length]!)
      .map((e, i) => ({ ...e, id: `${insight.id}-ev-${i}` }));

  return {
    ...insight,
    affected_surface:
      insight.category === "bug"
        ? "core app runtime"
        : insight.category === "feature_request"
          ? "mobile settings"
          : "editor",
    churn_rationale:
      insight.churn_risk === "high"
        ? "A significant share of reviews come from long-tenure users citing an intent to switch tools."
        : insight.churn_risk === "medium"
          ? "Reviews trend negative and mention comparable alternatives, but most users are staying for now."
          : "Reviews are frustrated but users are not signalling churn.",
    findings: [
      {
        claim: `${insight.title}. Users describe this recurring across recent app versions.`,
        evidence: pick(3, 0),
      },
      {
        claim:
          "Workarounds shared in reviews (restart, reinstall, contacting support) are inconsistent and rarely resolve the issue.",
        evidence: pick(2, 2),
      },
    ],
    recommended_actions: [
      {
        action: `Investigate and prioritise a fix for: ${insight.label}.`,
        urgency:
          insight.priority === "P0"
            ? "immediate"
            : insight.priority === "P1"
              ? "this_sprint"
              : "next_quarter",
      },
      {
        action: "Add telemetry to quantify frequency and affected user segments.",
        urgency: "this_sprint",
      },
    ],
    star_distribution: makeStars(insight),
    trend: makeTrend(insight, spikeConfig[insight.id] ?? null),
    spike: spikeConfig[insight.id] ?? null,
  };
}

export const insightDetailsById: Record<string, InsightDetail> = {
  [dataLossDetail.id]: dataLossDetail,
  ...Object.fromEntries(
    insightsList
      .filter((i) => i.id !== dataLossDetail.id)
      .map((i) => [i.id, makeGenericDetail(i)]),
  ),
};

// ---- Header stats ----

export const headerStats = {
  totalReviews: 1820,
  totalThemes: 22,
  p0Count: insightsList.filter((i) => i.priority === "P0").length,
  highChurnCount: insightsList.filter((i) => i.churn_risk === "high").length,
  product: "Notion iOS",
  lastSynced: "2h ago",
};

// ---- Spike lookup for the alerts strip ----
export function getSpikingInsights(): Array<{
  id: string;
  label: string;
  title: string;
  spike: Spike;
}> {
  return insightsList
    .filter((i) => spikeConfig[i.id])
    .map((i) => ({
      id: i.id,
      label: i.label,
      title: i.title,
      spike: spikeConfig[i.id]!,
    }))
    .sort((a, b) => b.spike.sigma - a.spike.sigma);
}

