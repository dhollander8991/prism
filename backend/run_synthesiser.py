"""Ad-hoc runner for Part C: synthesise the 22 canonical themes and report.

Run from backend/:  DATABASE_URL=postgresql://prism:prism@localhost:5433/prism \
                    ANTHROPIC_API_KEY=... venv/bin/python run_synthesiser.py

Wraps the Anthropic client to tally tokens/cost, runs synthesiser_node directly
(it reads themes/items from the DB), then prints the report list, groundedness
metric, cost, and the full "Data Loss After Logout" report.
"""
from __future__ import annotations

import asyncio

from dotenv import load_dotenv

load_dotenv()

import agents.synthesiser as syn  # noqa: E402
from db.database import AsyncSessionFactory  # noqa: E402
from db.models import ClusterORM, FeedbackItemORM, InsightReportORM  # noqa: E402
from sqlalchemy import select  # noqa: E402

# claude-sonnet-4-5 pricing (Sonnet tier): $3 / $15 per 1M tokens.
_IN_PER_M, _OUT_PER_M = 3.0, 15.0

_usage = {"calls": 0, "in": 0, "out": 0}
_real_get_client = syn._get_client


def _counting_client():
    client = _real_get_client()
    orig = client.messages.create

    async def create(*a, **k):
        resp = await orig(*a, **k)
        _usage["calls"] += 1
        _usage["in"] += resp.usage.input_tokens
        _usage["out"] += resp.usage.output_tokens
        return resp

    client.messages.create = create
    return client


syn._get_client = _counting_client

_PRI = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}


async def main() -> None:
    state = await syn.synthesiser_node({"errors": []})
    stats = state.get("synthesis_stats", {})

    async with AsyncSessionFactory() as db:
        reports = (await db.execute(select(InsightReportORM))).scalars().all()
        labels = {c.cluster_id: c.label for c in (await db.execute(select(ClusterORM))).scalars()}
    reports.sort(key=lambda r: (_PRI.get(r.priority, 9), -(r.item_count or 0)))

    print("\n=== REPORTS (P0 first, then item_count desc) ===")
    for r in reports:
        print(f"  [{r.priority}] churn={r.churn_risk or '-':6} n={r.item_count:4}  {r.title}")

    print("\n=== GROUNDEDNESS ===")
    print(f"  themes={stats.get('themes')} reports={stats.get('reports')} "
          f"total_findings={stats.get('total_findings')}")
    print(f"  dropped(no valid evidence)={stats.get('dropped_findings')} "
          f"partial_fabrications={stats.get('partial_fabrications')}")
    print(f"  HALLUCINATED CITATION RATE = {stats.get('hallucination_rate', 0):.3%}")

    cost = _usage["in"] / 1e6 * _IN_PER_M + _usage["out"] / 1e6 * _OUT_PER_M
    print("\n=== CLAUDE USAGE ===")
    print(f"  calls={_usage['calls']} (expect ~22, +1 per retry)")
    print(f"  input_tokens={_usage['in']:,} output_tokens={_usage['out']:,}")
    print(f"  cost=${cost:.4f}  (sonnet-4-5 @ ${_IN_PER_M}/${_OUT_PER_M} per 1M)")

    errs = state.get("errors", [])
    if errs:
        print(f"\n=== ERRORS/WARNINGS ({len(errs)}) ===")
        for e in errs:
            print(f"  - {e}")

    # Full "Data Loss After Logout" report with receipts (cluster_12).
    target = next((r for r in reports if "data loss" in (labels.get(r.cluster_id, "") + r.title).lower()
                   or "logout" in (labels.get(r.cluster_id, "") + r.title).lower()), None)
    if target is None:
        print("\n[!] Could not locate a 'Data Loss After Logout' theme in the reports.")
        return

    ev_ids = [eid for f in (target.findings or []) for eid in (f.get("evidence_item_ids") or [])]
    async with AsyncSessionFactory() as db:
        rows = (await db.execute(
            select(FeedbackItemORM.id, FeedbackItemORM.text, FeedbackItemORM.item_metadata)
            .where(FeedbackItemORM.id.in_(ev_ids))
        )).all()
    texts = {i: (t, m or {}) for i, t, m in rows}

    print("\n" + "=" * 70)
    print(f"FULL REPORT — {labels.get(target.cluster_id, target.cluster_id)}")
    print("=" * 70)
    print(f"title:              {target.title}")
    print(f"priority:           {target.priority} — {target.priority_rationale}")
    print(f"affected_surface:   {target.affected_surface}")
    print(f"churn_risk:         {target.churn_risk} — {target.churn_rationale}")
    print(f"item_count:         {target.item_count}")
    print("\nfindings:")
    for i, f in enumerate(target.findings or [], 1):
        print(f"  {i}. {f.get('claim')}")
        for eid in f.get("evidence_item_ids") or []:
            t, m = texts.get(eid, ("<missing>", {}))
            print(f"       [{eid}] {m.get('stars','?')}★ {m.get('country','?')}: {t[:180]!r}")
    print("\nrecommended_actions:")
    for a in target.recommended_actions or []:
        print(f"  - ({a.get('urgency')}) {a.get('action')}")


if __name__ == "__main__":
    asyncio.run(main())
