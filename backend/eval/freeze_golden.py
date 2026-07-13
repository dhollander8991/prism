"""freeze_golden.py — run ONCE to snapshot the current DB state into eval/golden/*.json.

These fixtures are the fixed evaluation set; downstream evals read them, never the live
DB.  Re-running the synthesiser later must NOT overwrite them.  To re-freeze intentionally,
delete the golden/ files and run this script again.

Run from backend/:
    DATABASE_URL=postgresql://prism:prism@localhost:5433/prism python -m eval.freeze_golden
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# Must set DATABASE_URL before importing db modules.
if "DATABASE_URL" not in os.environ:
    os.environ["DATABASE_URL"] = "postgresql://prism:prism@localhost:5433/prism"

from db.database import AsyncSessionFactory  # noqa: E402
from db.models import ClusterORM, FeedbackItemORM, InsightReportORM  # noqa: E402
from sqlalchemy import select  # noqa: E402

# Import synthesiser helpers — we reuse them verbatim, not re-implement them.
from agents.synthesiser import (  # noqa: E402
    _CENTROID_SAMPLE,
    _EXTREME_SAMPLE,
    _compute_stats,
    _select_centroid_reps,
    _select_extreme_stars,
)

_GOLDEN_DIR = Path(__file__).parent / "golden"


def _safe_path(p: Path) -> Path:
    """Abort if the golden file already exists — freeze is idempotent, not overwriting."""
    if p.exists():
        print(f"SKIP (already frozen): {p.name} — delete it first to re-freeze.")
        return p
    return p


def _json_default(obj: object) -> object:
    """Serialise types that json.dumps doesn't handle natively."""
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Not serialisable: {type(obj)}")


def _write_golden(name: str, data: object) -> None:
    p = _GOLDEN_DIR / name
    if p.exists():
        print(f"SKIP (already frozen): {p.name}")
        return
    p.write_text(json.dumps(data, indent=2, default=_json_default))
    print(f"Wrote {p} ({p.stat().st_size:,} bytes)")


async def freeze() -> None:
    _GOLDEN_DIR.mkdir(parents=True, exist_ok=True)

    # -------------------------------------------------------------------------
    # 1. Load all clusters.
    # -------------------------------------------------------------------------
    async with AsyncSessionFactory() as db:
        cluster_rows = (await db.execute(select(ClusterORM))).scalars().all()

    if not cluster_rows:
        print("ERROR: no clusters found in DB — is the DB populated?", file=sys.stderr)
        sys.exit(1)

    cluster_ids = [c.cluster_id for c in cluster_rows]
    print(f"Found {len(cluster_ids)} clusters.")

    # -------------------------------------------------------------------------
    # 2. Load all feedback items (with embeddings) for those clusters.
    # -------------------------------------------------------------------------
    async with AsyncSessionFactory() as db:
        item_rows = (
            await db.execute(
                select(
                    FeedbackItemORM.id,
                    FeedbackItemORM.text,
                    FeedbackItemORM.item_metadata,
                    FeedbackItemORM.created_at,
                    FeedbackItemORM.embedding,
                    FeedbackItemORM.cluster_id,
                ).where(FeedbackItemORM.cluster_id.in_(cluster_ids))
            )
        ).all()

    print(f"Found {len(item_rows)} feedback items assigned to clusters.")

    # Build per-cluster lookup structures.
    cluster_items_raw: dict[str, list[dict]] = {cid: [] for cid in cluster_ids}
    embeddings: dict[str, list[float]] = {}

    for item_id, text, metadata, created_at, embedding, cid in item_rows:
        if cid not in cluster_items_raw:
            continue
        meta = metadata or {}
        cluster_items_raw[cid].append({
            "id": item_id,
            "text": text or "",
            "stars": meta.get("stars"),
            "country": meta.get("country", "unknown"),
            "created_at": created_at,
        })
        if embedding is not None:
            embeddings[item_id] = list(embedding) if not isinstance(embedding, list) else embedding

    # -------------------------------------------------------------------------
    # 3. themes.json — cluster metadata + member item ids.
    # -------------------------------------------------------------------------
    themes_data = []
    cluster_map = {c.cluster_id: c for c in cluster_rows}

    for cid in cluster_ids:
        c = cluster_map[cid]
        member_ids = [it["id"] for it in cluster_items_raw[cid]]
        themes_data.append({
            "cluster_id": cid,
            "label": c.label,
            "category": c.category,
            "sentiment": c.sentiment,
            "summary": c.summary,
            "item_count": c.item_count,
            "member_item_ids": member_ids,
        })

    _write_golden("themes.json", themes_data)

    # -------------------------------------------------------------------------
    # 4. samples.json + stats.json — deterministic sample + aggregate stats.
    # -------------------------------------------------------------------------
    samples_data = {}
    stats_data = {}

    for cid in cluster_ids:
        all_items = cluster_items_raw[cid]
        if not all_items:
            samples_data[cid] = []
            stats_data[cid] = {}
            continue

        item_ids = [it["id"] for it in all_items]

        # Exact same selection logic as synthesiser._synthesise_theme.
        centroid_ids = _select_centroid_reps(item_ids, embeddings, k=_CENTROID_SAMPLE)
        extreme_ids = _select_extreme_stars(
            all_items, excluded_ids=set(centroid_ids), k=_EXTREME_SAMPLE
        )

        shown_ids: list[str] = list(centroid_ids)
        for eid in extreme_ids:
            if eid not in set(shown_ids):
                shown_ids.append(eid)

        id_to_item = {it["id"]: it for it in all_items}
        sample_records = []
        for iid in shown_ids:
            if iid not in id_to_item:
                continue
            it = id_to_item[iid]
            created = it["created_at"]
            sample_records.append({
                "id": iid,
                "text": it["text"],           # full, untruncated
                "stars": it.get("stars"),
                "country": it.get("country", "unknown"),
                # isoformat so downstream json comparison is stable
                "created_at": created.isoformat() if isinstance(created, datetime) else str(created),
            })

        samples_data[cid] = sample_records

        # Aggregate stats — same function synthesiser uses, so numeric_guard can
        # validate claims against the exact numbers Claude was given.
        stats_data[cid] = _compute_stats(all_items)

    _write_golden("samples.json", samples_data)
    _write_golden("stats.json", stats_data)

    # -------------------------------------------------------------------------
    # 5. findings.json — the 137 baseline findings from insight_reports.
    # -------------------------------------------------------------------------
    async with AsyncSessionFactory() as db:
        report_rows = (await db.execute(select(InsightReportORM))).scalars().all()

    findings_data = []
    total = 0
    for r in report_rows:
        for f in (r.findings or []):
            findings_data.append({
                "cluster_id": r.cluster_id,
                "claim": f.get("claim", ""),
                "evidence_item_ids": f.get("evidence_item_ids", []),
            })
            total += 1

    # Also record recommended_actions per report — numeric_guard checks them too.
    # Store as a parallel list keyed by cluster_id.
    actions_data = []
    for r in report_rows:
        for act in (r.recommended_actions or []):
            actions_data.append({
                "cluster_id": r.cluster_id,
                "action": act.get("action", ""),
                "urgency": act.get("urgency", ""),
            })

    combined_findings = {
        "findings": findings_data,
        "recommended_actions": actions_data,
    }
    _write_golden("findings.json", combined_findings)

    print(f"\nFrozen {total} findings and {len(actions_data)} recommended_actions.")
    print(f"Golden fixtures written to {_GOLDEN_DIR}/")


if __name__ == "__main__":
    asyncio.run(freeze())
