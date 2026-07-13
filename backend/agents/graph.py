from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from langgraph.graph import END, START, StateGraph

from agents.alerter import alerter_node
from agents.clusterer import clusterer_node
from agents.enricher import enricher_node
from agents.ingestor import ingestor_node
from agents.labeller import labeller_node
from agents.state import PipelineState
from agents.synthesiser import synthesiser_node

logger = logging.getLogger(__name__)

Node = Callable[[PipelineState], Awaitable[dict]]


def _safe(node: Node) -> Node:
    """Catch node failures, record them in state['errors'], and let the batch continue."""

    async def wrapped(state: PipelineState) -> dict:
        try:
            return await node(state)
        except Exception as exc:
            logger.exception("node %s failed", node.__name__)
            return {"errors": state.get("errors", []) + [f"{node.__name__}: {exc}"]}

    wrapped.__name__ = node.__name__
    return wrapped


def _build():
    g = StateGraph(PipelineState)
    g.add_node("ingestor", _safe(ingestor_node))
    g.add_node("enricher", _safe(enricher_node))
    g.add_node("clusterer", _safe(clusterer_node))
    g.add_node("labeller", _safe(labeller_node))
    g.add_node("synthesiser", _safe(synthesiser_node))
    g.add_node("alerter", _safe(alerter_node))

    g.add_edge(START, "ingestor")
    g.add_edge("ingestor", "enricher")
    g.add_edge("enricher", "clusterer")
    g.add_edge("clusterer", "labeller")
    g.add_edge("labeller", "synthesiser")
    g.add_edge("synthesiser", "alerter")
    g.add_edge("alerter", END)
    return g.compile()


graph = _build()


async def run_pipeline(item_ids: list[str], params: dict | None = None) -> PipelineState:
    """Seed the state and run the full graph. The ingestor loads texts from the DB.
    params carries per-run clustering overrides (min_cluster_size, n_neighbors, ...)."""
    state: PipelineState = {
        "item_ids": item_ids,
        "texts": {},
        "embeddings": {},
        "clusters": {},
        "labels": {},
        "enrichment": {},
        "reports": [],
        "alerts": [],
        "errors": [],
        "params": params or {},
    }
    return await graph.ainvoke(state)
