from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.database import get_db
from db.models import ClusterORM, FeedbackItemORM, InsightReportORM, ThemeTrendORM

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1", tags=["insights"])

# Priority ordering for sort: P0 < P1 < P2 < P3 (ascending = P0 first).
_PRIORITY_ORDER = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}


def _spike_obj(cluster: ClusterORM | None) -> dict | None:
    """Build the spike sub-object from cluster columns, or None when no spike was detected.

    The frontend field for the z-score is `sigma` (not `z`) so the dashboard alert strip
    can show "N sigma" without a data-contract change on the API side.
    Returns None when:
    - cluster is None (cluster was deleted between writes)
    - spike_week is None (alerter found no qualifying spike, or hasn't run yet)
    """
    if cluster is None or cluster.spike_week is None:
        return None
    return {
        "week": cluster.spike_week.isoformat(),
        "sigma": cluster.spike_z,
        "count": cluster.spike_count,
        "baseline_mean": cluster.spike_baseline_mean,
    }


@router.get("/insights")
async def list_insights(db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """All insight reports sorted by priority (P0 first) then item_count descending.

    Returns a lightweight list — findings are omitted here; use GET /insights/{id} for
    the full report with resolved evidence.

    Each report includes a `spike` field from the clusters table so the frontend alert
    strip can render only themes with a detected volume spike without a second request.
    """
    report_rows = (
        await db.execute(select(InsightReportORM))
    ).scalars().all()

    if not report_rows:
        return {"insights": []}

    # Fetch cluster rows in one query so we can join without a relationship.
    cluster_ids = list({r.cluster_id for r in report_rows})
    cluster_rows = (
        await db.execute(select(ClusterORM).where(ClusterORM.cluster_id.in_(cluster_ids)))
    ).scalars().all()
    cluster_by_id: dict[str, ClusterORM] = {c.cluster_id: c for c in cluster_rows}

    def _sort_key(r: InsightReportORM) -> tuple[int, int]:
        p = _PRIORITY_ORDER.get(r.priority, 99)
        # Negate item_count so higher counts sort earlier within the same priority.
        return (p, -(r.item_count or 0))

    sorted_reports = sorted(report_rows, key=_sort_key)

    return {
        "insights": [
            {
                "id": r.id,
                "cluster_id": r.cluster_id,
                "title": r.title,
                "label": cluster_by_id.get(r.cluster_id, None) and cluster_by_id[r.cluster_id].label or "",
                "priority": r.priority,
                "priority_rationale": r.priority_rationale,
                "churn_risk": r.churn_risk,
                "item_count": r.item_count,
                "generated_at": r.generated_at.isoformat() if r.generated_at else None,
                # Spike object for the frontend alert strip — null when no spike detected.
                "spike": _spike_obj(cluster_by_id.get(r.cluster_id)),
            }
            for r in sorted_reports
        ]
    }


@router.get("/insights/{report_id}")
async def get_insight(report_id: str, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """Full insight report with findings resolved to actual review text.

    Each finding's evidence_item_ids is expanded to include the actual review text,
    star rating, and country — the "receipts" the UI needs to show.

    New fields (added with alerter):
    - trend: weekly item counts for this cluster, ordered oldest→newest. Empty list
             when the alerter has not run yet.
    - spike: {week, sigma, count, baseline_mean} or null (null = no spike / not run).
    - star_distribution: {"1": n, ..., "5": n} tallied from item_metadata->stars for
             items in this cluster. Only integer star values 1–5 are counted; malformed
             or missing values are silently ignored.
    """
    report = (
        await db.execute(
            select(InsightReportORM).where(InsightReportORM.id == report_id)
        )
    ).scalar_one_or_none()

    if report is None:
        raise HTTPException(status_code=404, detail=f"Insight report {report_id!r} not found")

    # Collect all evidence item ids referenced across findings.
    all_evidence_ids: list[str] = []
    for finding in (report.findings or []):
        if isinstance(finding, dict):
            all_evidence_ids.extend(finding.get("evidence_item_ids") or [])

    evidence_lookup: dict[str, dict] = {}
    if all_evidence_ids:
        item_rows = (
            await db.execute(
                select(
                    FeedbackItemORM.id,
                    FeedbackItemORM.text,
                    FeedbackItemORM.item_metadata,
                ).where(FeedbackItemORM.id.in_(all_evidence_ids))
            )
        ).all()
        for item_id, text, metadata in item_rows:
            meta = metadata or {}
            evidence_lookup[item_id] = {
                "id": item_id,
                "text": text or "",
                "stars": meta.get("stars"),
                "country": meta.get("country"),
            }

    # Resolve findings: replace id lists with hydrated evidence objects.
    resolved_findings: list[dict] = []
    for finding in (report.findings or []):
        if not isinstance(finding, dict):
            continue
        evidence_ids = finding.get("evidence_item_ids") or []
        resolved_findings.append({
            "claim": finding.get("claim", ""),
            "evidence": [
                evidence_lookup[eid]
                for eid in evidence_ids
                if eid in evidence_lookup
            ],
        })

    # Fetch the cluster row for label, spike columns, and trend lookup.
    cluster = (
        await db.execute(
            select(ClusterORM).where(ClusterORM.cluster_id == report.cluster_id)
        )
    ).scalar_one_or_none()

    # ------------------------------------------------------------------
    # Trend: all theme_trends rows for this cluster, ordered by week.
    # ------------------------------------------------------------------
    trend_rows = (
        await db.execute(
            select(ThemeTrendORM)
            .where(ThemeTrendORM.cluster_id == report.cluster_id)
            .order_by(ThemeTrendORM.week)
        )
    ).scalars().all()
    trend = [{"week": row.week.isoformat(), "count": row.count} for row in trend_rows]

    # ------------------------------------------------------------------
    # Star distribution: tally from all items in this cluster.
    # Keys are strings ("1"–"5") to match JSON convention; missing/invalid stars ignored.
    # ------------------------------------------------------------------
    star_dist: dict[str, int] = {"1": 0, "2": 0, "3": 0, "4": 0, "5": 0}
    star_item_rows = (
        await db.execute(
            select(FeedbackItemORM.item_metadata).where(
                FeedbackItemORM.cluster_id == report.cluster_id
            )
        )
    ).scalars().all()
    for meta in star_item_rows:
        if not isinstance(meta, dict):
            continue
        stars = meta.get("stars")
        if isinstance(stars, (int, float)) and 1 <= int(stars) <= 5:
            star_dist[str(int(stars))] += 1

    return {
        "id": report.id,
        "cluster_id": report.cluster_id,
        "label": cluster.label if cluster else "",
        "title": report.title,
        "priority": report.priority,
        "priority_rationale": report.priority_rationale,
        "findings": resolved_findings,
        "recommended_actions": report.recommended_actions or [],
        "affected_surface": report.affected_surface,
        "churn_risk": report.churn_risk,
        "churn_rationale": report.churn_rationale,
        "item_count": report.item_count,
        "generated_at": report.generated_at.isoformat() if report.generated_at else None,
        # Alerter-populated fields.
        "trend": trend,
        "spike": _spike_obj(cluster),
        "star_distribution": star_dist,
    }
