from __future__ import annotations

import logging
from collections import Counter

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agents.graph import run_pipeline
from db.database import get_db
from db.models import ClusterORM, FeedbackItemORM

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1", tags=["pipeline"])


@router.post("/pipeline/run")
async def run(
    source: str | None = None,
    limit: int | None = None,
    min_cluster_size: int | None = None,
    n_neighbors: int | None = None,
    n_components: int | None = None,
    rescue_percentile: float | None = None,
    db: AsyncSession = Depends(get_db),
) -> dict:
    q = (
        select(FeedbackItemORM.id)
        .where(FeedbackItemORM.processed.is_(False))
        .order_by(FeedbackItemORM.created_at.desc())
    )
    if source:
        q = q.where(FeedbackItemORM.source == source)
    if limit:
        q = q.limit(limit)

    ids = list((await db.execute(q)).scalars().all())
    if not ids:
        return {"items_processed": 0, "clusters_found": 0, "cluster_sizes": {}, "errors": []}

    params = {
        k: v
        for k, v in {
            "min_cluster_size": min_cluster_size,
            "n_neighbors": n_neighbors,
            "n_components": n_components,
            "rescue_percentile": rescue_percentile,
        }.items()
        if v is not None
    }
    final = await run_pipeline(ids, params)
    clusters = final.get("clusters", {})
    sizes = Counter(c for c in clusters.values() if c)
    return {
        "items_processed": len(ids),
        "clusters_found": len(sizes),
        "cluster_sizes": dict(sizes),
        "errors": final.get("errors", []),
    }


@router.get("/clusters")
async def list_clusters(db: AsyncSession = Depends(get_db)) -> dict:
    # 3 sample texts per cluster.
    samples: dict[str, list[str]] = {}
    rows = await db.execute(
        select(FeedbackItemORM.cluster_id, FeedbackItemORM.text)
        .where(FeedbackItemORM.cluster_id.is_not(None))
    )
    for cid, text in rows.all():
        s = samples.setdefault(cid, [])
        if len(s) < 3:
            s.append(text[:200])

    labelled = await db.execute(
        select(ClusterORM).order_by(ClusterORM.item_count.desc())
    )
    clusters = [
        {
            "cluster_id": c.cluster_id,
            "label": c.label,
            "category": c.category,
            "sentiment": c.sentiment,
            "summary": c.summary,
            "item_count": c.item_count,
            "samples": samples.get(c.cluster_id, []),
        }
        for c in labelled.scalars()
    ]
    return {"clusters": clusters}
