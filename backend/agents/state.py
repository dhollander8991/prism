from __future__ import annotations

from typing import TypedDict


class PipelineState(TypedDict, total=False):
    """Single object threaded through every agent node. total=False so a partially
    filled state (e.g. before the Clusterer runs) is still valid."""

    item_ids: list[str]                   # the batch of feedback item IDs being processed
    texts: dict[str, str]                 # item_id -> text
    embeddings: dict[str, list[float]]    # filled by Clusterer
    clusters: dict[str, str]              # item_id -> cluster_id, filled by Clusterer
    labels: dict[str, dict]               # cluster_id -> {label, category, sentiment, summary}
    enrichment: dict[str, dict]           # filled later by Enricher
    reports: list[dict]                   # filled later by Synthesiser
    synthesis_stats: dict                 # groundedness metrics from the Synthesiser
    alerts: list[dict]                    # filled later by Alerter
    errors: list[str]                     # any agent appends here on failure
    params: dict                          # per-run tuning overrides (e.g. min_cluster_size)
