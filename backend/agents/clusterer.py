from __future__ import annotations

import asyncio
import logging
import os
from collections import Counter

import hdbscan
import numpy as np
from sqlalchemy import select

from agents.state import PipelineState
from db.database import AsyncSessionFactory
from db.models import FeedbackItemORM

logger = logging.getLogger(__name__)

_MODEL_NAME = "all-MiniLM-L6-v2"  # 384-dim, matches the Vector(384) column
_UMAP_MIN_ITEMS = 15              # below this there's too little to reduce; cluster on raw dims

# Defaults tuned for the real ~500-item single-app corpus.
_DEFAULT_MIN_CLUSTER_SIZE = 5
_DEFAULT_N_NEIGHBORS = 15
_DEFAULT_N_COMPONENTS = 10
_DEFAULT_RESCUE_PERCENTILE = 90.0  # p-th percentile of member distances used as per-cluster cap

# DETERMINISM CONTRACT: reproducible runs require CPU + fixed seeds.
# sentence-transformers on MPS (Apple GPU) uses non-deterministic floating-point
# reduction order, producing different embedding matrices run-to-run even on identical
# input — which then propagates through UMAP → HDBSCAN → different cluster counts.
# CPU + seeds below = bit-identical embeddings across runs.
# UMAP is already pinned via random_state=42. HDBSCAN is deterministic given fixed input.
np.random.seed(42)
try:
    import torch
    torch.manual_seed(42)
    # warn_only=True: lets cuBLAS/MPS ops proceed rather than hard-crash; we just want
    # the deterministic path on CPU, not to gate-block anything that can't be made
    # deterministic globally.
    torch.use_deterministic_algorithms(True, warn_only=True)
except ImportError:
    pass  # torch not yet importable at module load (lazy-loaded by sentence-transformers)

_model = None


def _get_model():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer  # lazy: torch import is heavy

        device = os.environ.get("EMBED_DEVICE", "cpu")
        if device != "cpu":
            # Anything other than CPU can use non-deterministic floating-point ops.
            # Eval baselines will drift between runs; do not compare cluster counts across runs.
            logger.warning(
                "EMBED_DEVICE=%s — embeddings are non-deterministic, eval baselines will drift",
                device,
            )
        logger.info("loading embedding model %s on device=%s", _MODEL_NAME, device)
        _model = SentenceTransformer(_MODEL_NAME, device=device)
    return _model


def embed(corpus: list[str]) -> np.ndarray:
    """N texts -> N x 384 normalized embeddings (cosine ~ dot product). Sync/CPU-bound."""
    matrix = _get_model().encode(corpus, normalize_embeddings=True)
    matrix = np.asarray(matrix, dtype=float)
    logger.info("embed: %d texts -> %s", len(corpus), matrix.shape)
    return matrix


def reduce(matrix: np.ndarray, n_neighbors: int, n_components: int) -> np.ndarray:
    """N x 384 -> N x n_components via UMAP (cosine). Skips UMAP for tiny batches."""
    n = len(matrix)
    if n < _UMAP_MIN_ITEMS:
        logger.info("reduce: %d items (<%d) — skipping UMAP, clustering on raw %dd",
                    n, _UMAP_MIN_ITEMS, matrix.shape[1])
        return matrix

    import umap  # lazy: numba import is heavy

    reducer = umap.UMAP(
        n_neighbors=min(n_neighbors, n - 1),
        n_components=n_components,
        metric="cosine",
        random_state=42,
    )
    reduced = np.asarray(reducer.fit_transform(matrix), dtype=float)
    logger.info("reduce: UMAP %dd -> %dd on %d items", matrix.shape[1], reduced.shape[1], n)
    return reduced


def cluster(reduced: np.ndarray, min_cluster_size: int) -> np.ndarray:
    """HDBSCAN on the reduced vectors (euclidean is correct after umap-cosine). -1 = noise."""
    if len(reduced) < 2:
        return np.full(len(reduced), -1, dtype=int)
    labels = hdbscan.HDBSCAN(
        min_cluster_size=min_cluster_size,
        min_samples=1,
        metric="euclidean",
        cluster_selection_method="eom",
    ).fit_predict(reduced)
    return labels


def rescue_noise(
    reduced: np.ndarray, labels: np.ndarray, rescue_percentile: float
) -> tuple[np.ndarray, int]:
    """Pull HDBSCAN noise points (-1) into their nearest cluster centroid, using
    euclidean distance (consistent with how HDBSCAN clustered). The distance cap per
    cluster is calibrated from that cluster's own member distances to its centroid,
    so a noise point must be *at least as close* as the p-th percentile member — i.e.
    we only rescue if the point would plausibly belong given the cluster's own spread.
    Returns (updated_labels, rescued_count)."""
    labels = labels.copy()
    real = [lab for lab in set(labels) if lab != -1]
    noise_idx = np.where(labels == -1)[0]
    if not real or len(noise_idx) == 0:
        return labels, 0

    centroids = {lab: reduced[labels == lab].mean(axis=0) for lab in real}

    # Per-cluster distance cap: p-th percentile of member-to-centroid euclidean distances.
    # A single-member cluster has all-zero distances -> cap 0 -> only an exact coincident
    # point would be rescued, which is the correct conservative behaviour.
    caps: dict[int, float] = {}
    for lab in real:
        members = reduced[labels == lab]
        dists = np.linalg.norm(members - centroids[lab], axis=1)
        caps[lab] = float(np.percentile(dists, rescue_percentile)) if len(dists) > 0 else 0.0

    cmat = np.array([centroids[lab] for lab in real])

    rescued = 0
    for i in noise_idx:
        dists_to_centroids = np.linalg.norm(cmat - reduced[i], axis=1)
        best_idx = int(np.argmin(dists_to_centroids))
        best_lab = real[best_idx]
        if dists_to_centroids[best_idx] <= caps[best_lab]:
            labels[i] = best_lab
            rescued += 1

    remaining = int(np.sum(labels == -1))
    total_noise = len(noise_idx)
    logger.info(
        "rescue_noise: %d/%d noise points rescued (%.0f%%), %d remain unassignable",
        rescued, total_noise, 100 * rescued / total_noise, remaining,
    )
    return labels, rescued


async def _persist(updates: dict[str, tuple[list[float], str | None]]) -> None:
    """updates: item_id -> (384-dim embedding, cluster_id). Marks the row processed.
    The reduced vector is intentionally NOT persisted — it's an in-memory intermediate."""
    async with AsyncSessionFactory() as db:
        rows = await db.execute(
            select(FeedbackItemORM).where(FeedbackItemORM.id.in_(list(updates)))
        )
        for row in rows.scalars():
            emb, cid = updates[row.id]
            row.embedding = emb
            row.cluster_id = cid
            row.processed = True
        await db.commit()


async def clusterer_node(state: PipelineState) -> dict:
    texts = state.get("texts", {})
    if not texts:
        return {"embeddings": {}, "clusters": {}}

    # Sort by id so embed→reduce→cluster sees a canonical row order regardless of
    # dict insertion order (which varies across DB fetches and Python versions).
    # Item ids are deterministic sha256-derived strings, so lexicographic sort is stable.
    ids = sorted(texts)
    corpus = [texts[i] for i in ids]

    p = state.get("params", {}) or {}
    min_cluster_size = p.get("min_cluster_size", _DEFAULT_MIN_CLUSTER_SIZE)
    n_neighbors = p.get("n_neighbors", _DEFAULT_N_NEIGHBORS)
    n_components = p.get("n_components", _DEFAULT_N_COMPONENTS)
    rescue_percentile = p.get("rescue_percentile", _DEFAULT_RESCUE_PERCENTILE)

    matrix = await asyncio.to_thread(embed, corpus)
    embeddings = {ids[k]: matrix[k].tolist() for k in range(len(ids))}

    reduced = await asyncio.to_thread(reduce, matrix, n_neighbors, n_components)
    labels = await asyncio.to_thread(cluster, reduced, min_cluster_size)
    labels, rescued_count = await asyncio.to_thread(rescue_noise, reduced, labels, rescue_percentile)
    clusters = {
        ids[k]: (None if lab == -1 else f"cluster_{lab}") for k, lab in enumerate(labels)
    }

    await _persist({i: (embeddings[i], clusters[i]) for i in ids})

    sizes = Counter(c for c in clusters.values() if c)
    noise = sum(1 for c in clusters.values() if c is None)
    logger.info(
        "cluster: %d items, %d clusters, sizes=%s, noise=%d (rescued=%d, min_cluster_size=%d)",
        len(ids), len(sizes), dict(sizes), noise, rescued_count, min_cluster_size,
    )
    return {"embeddings": embeddings, "clusters": clusters}
