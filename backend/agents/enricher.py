from __future__ import annotations

from agents.state import PipelineState


async def enricher_node(state: PipelineState) -> dict:
    # TODO: GPT-4o-mini structured extraction (feature, intent, entities) + hybrid RAG
    # retrieval, writing per-item results into state["enrichment"]. Stub for now.
    return {}
