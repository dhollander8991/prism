from __future__ import annotations

import numpy as np

import agents.clusterer as clusterer


class _DummyModel:
    """Deterministic stand-in for the real embedding model: puts 'login' texts near one
    axis and 'dark mode' texts near another, so clustering is hermetic and fast."""

    def encode(self, texts, **_kw):
        out = []
        for i, t in enumerate(texts):
            v = np.zeros(8)
            v[0] = 1.0 if "login" in t.lower() else 0.0
            v[1] = 1.0 if "dark mode" in t.lower() else 0.0
            v[2] = i * 1e-3  # tiny jitter so points aren't identical
            out.append(v)
        return np.array(out)


async def test_clusterer_separates_two_topics(monkeypatch):
    monkeypatch.setattr(clusterer, "_get_model", lambda: _DummyModel())

    async def _noop(_updates):
        return None

    monkeypatch.setattr(clusterer, "_persist", _noop)

    texts = {
        "a": "login is broken",
        "b": "cannot login at all",
        "c": "the login page fails",
        "d": "love the new dark mode",
        "e": "dark mode looks great",
        "f": "great dark mode theme",
    }
    # 6 items (<15) so UMAP is skipped; min_cluster_size=2 so groups of 3 form clusters.
    state = {"item_ids": list(texts), "texts": texts, "params": {"min_cluster_size": 2}}
    out = await clusterer.clusterer_node(state)
    clusters = out["clusters"]

    login = {clusters["a"], clusters["b"], clusters["c"]}
    dark = {clusters["d"], clusters["e"], clusters["f"]}

    assert login == {clusters["a"]} and None not in login   # login items share one cluster
    assert dark == {clusters["d"]} and None not in dark     # dark-mode items share one cluster
    assert login != dark                                    # and the two topics differ
    assert len(out["embeddings"]) == 6


def test_reduce_384_to_10():
    # N above the UMAP threshold so it actually reduces (not the raw-passthrough path).
    rng = np.random.default_rng(0)
    matrix = rng.random((20, 384))
    reduced = clusterer.reduce(matrix, n_neighbors=15, n_components=10)
    assert reduced.shape == (20, 10)


def test_reduce_skips_umap_when_tiny():
    matrix = np.random.default_rng(0).random((6, 384))
    reduced = clusterer.reduce(matrix, n_neighbors=15, n_components=10)
    assert reduced.shape == (6, 384)  # untouched — too few items to reduce


# ---------------------------------------------------------------------------
# rescue_noise — euclidean-distance / percentile-cap logic
# ---------------------------------------------------------------------------
#
# Geometry for all tests below (2-D so distances are trivial to compute by hand):
#
#   Cluster 0 members: (0,0), (1,0), (2,0) — centroid (1,0)
#   Distances to centroid: 1, 0, 1  -> p90 ≈ 1.0 (same value at any percentile >= 50)
#
#   Cluster 1 members: (0,5), (0,6), (0,7) — centroid (0,6)
#   Distances to centroid: 1, 0, 1  -> p90 ≈ 1.0
#
# Noise point A = (1.5, 0) — distance to cluster-0 centroid = 0.5 < cap 1.0  -> rescued
# Noise point B = (5,  0) — distance to cluster-0 centroid = 4.0 > cap 1.0  -> stays noise

def _make_base_fixture():
    """Return (reduced, labels) for the geometry described above."""
    reduced = np.array([
        [0.0, 0.0],  # idx 0  cluster 0
        [1.0, 0.0],  # idx 1  cluster 0
        [2.0, 0.0],  # idx 2  cluster 0
        [0.0, 5.0],  # idx 3  cluster 1
        [0.0, 6.0],  # idx 4  cluster 1
        [0.0, 7.0],  # idx 5  cluster 1
    ], dtype=float)
    labels = np.array([0, 0, 0, 1, 1, 1], dtype=int)
    return reduced, labels


def test_rescue_noise_far_outlier_stays_noise():
    """A point far outside any cluster's spread must NOT be rescued.

    This is the core regression: the old cosine-based code rescued everything;
    the new percentile-cap code must leave a genuine outlier as -1.
    """
    reduced_base, labels_base = _make_base_fixture()
    # Append noise point B = (5, 0): distance to cluster-0 centroid = 4.0, cap ~1.0
    reduced = np.vstack([reduced_base, [5.0, 0.0]])
    labels = np.append(labels_base, -1)

    new_labels, rescued = clusterer.rescue_noise(reduced, labels, rescue_percentile=90.0)

    assert new_labels[-1] == -1   # still noise
    assert rescued == 0


def test_rescue_noise_near_point_is_rescued():
    """A noise point sitting within the cluster's spread IS rescued into that cluster."""
    reduced_base, labels_base = _make_base_fixture()
    # Append noise point A = (1.5, 0): distance to cluster-0 centroid = 0.5, cap ~1.0
    reduced = np.vstack([reduced_base, [1.5, 0.0]])
    labels = np.append(labels_base, -1)

    new_labels, rescued = clusterer.rescue_noise(reduced, labels, rescue_percentile=90.0)

    assert new_labels[-1] == 0    # rescued into cluster 0
    assert rescued == 1


def test_rescue_noise_percentile_is_monotone():
    """A higher percentile (looser cap) must rescue at least as many points as a lower one.

    Uses a noise point that is just outside the p50 cap but inside the p90 cap.
    Cluster members: (0,0), (3,0), (6,0) -> centroid (3,0)
    Distances: 3, 0, 3  -> p50 = 3, p90 ≈ 3 (same), but with another arrangement:

    Members: (0,0), (2,0), (4,0) -> centroid (2,0), distances 2, 0, 2
    p50 = 2.0 (the median distance)
    p90 = 2.0 as well with only 3 points, so we need a shape where p50 < p90.

    Members: (0,0), (1,0), (4,0) -> centroid (5/3, 0)
    Distances: ~1.67, ~0.67, ~2.33  -> sorted: 0.67, 1.67, 2.33
    p50 ≈ 1.67,  p90 ≈ 2.26

    Noise at (2.0, 0): distance to centroid = 2.0 - 5/3 = 0.333 — that's < p50, always rescued.

    Use: noise at (3.5, 0): distance = 3.5 - 1.667 = 1.833
      p50 cap ≈ 1.67  -> 1.833 > 1.67 -> NOT rescued at p50
      p90 cap ≈ 2.26  -> 1.833 < 2.26 -> RESCUED at p90
    """
    reduced = np.array([
        [0.0, 0.0],   # cluster 0
        [1.0, 0.0],   # cluster 0
        [4.0, 0.0],   # cluster 0
        [3.5, 0.0],   # noise
    ], dtype=float)
    labels = np.array([0, 0, 0, -1], dtype=int)

    _, rescued_low = clusterer.rescue_noise(reduced.copy(), labels.copy(), rescue_percentile=50.0)
    _, rescued_high = clusterer.rescue_noise(reduced.copy(), labels.copy(), rescue_percentile=90.0)

    assert rescued_high >= rescued_low   # monotone: looser cap rescues at least as many


def test_rescue_noise_no_real_clusters_returns_unchanged():
    """All-noise input (no cluster): nothing changes, rescued_count = 0."""
    reduced = np.array([[1.0, 0.0], [0.0, 1.0], [0.5, 0.5]], dtype=float)
    labels = np.array([-1, -1, -1], dtype=int)

    new_labels, rescued = clusterer.rescue_noise(reduced, labels, rescue_percentile=90.0)

    np.testing.assert_array_equal(new_labels, labels)
    assert rescued == 0


def test_rescue_noise_no_noise_returns_unchanged():
    """No noise points: nothing to rescue, rescued_count = 0."""
    reduced = np.array([[0.0, 0.0], [1.0, 0.0], [0.0, 1.0]], dtype=float)
    labels = np.array([0, 0, 1], dtype=int)

    new_labels, rescued = clusterer.rescue_noise(reduced, labels, rescue_percentile=90.0)

    np.testing.assert_array_equal(new_labels, labels)
    assert rescued == 0


def test_rescue_noise_single_member_cluster_does_not_rescue_non_coincident():
    """A cluster with one member has cap = 0 (all-zero distances -> p-th percentile = 0).
    Only an exact coincident point would qualify, so a non-coincident noise point stays -1."""
    reduced = np.array([
        [0.0, 0.0],   # cluster 0 (sole member -> centroid = [0,0], cap = 0)
        [0.1, 0.0],   # noise: distance = 0.1 > 0 -> NOT rescued
    ], dtype=float)
    labels = np.array([0, -1], dtype=int)

    new_labels, rescued = clusterer.rescue_noise(reduced, labels, rescue_percentile=90.0)

    assert new_labels[1] == -1   # still noise
    assert rescued == 0


# ---------------------------------------------------------------------------
# Determinism — embed → reduce → cluster must be bit-identical across model reloads
#
# Root cause of the 107 vs 109 cluster regression: MPS (Apple GPU) floating-point
# reductions are non-deterministic across invocations, so two encodes of the same
# text produced slightly different matrices, which HDBSCAN resolved into different
# cluster counts.  The fix (EMBED_DEVICE=cpu) makes CPU inference repeatable with
# fixed weights.  The tests below verify bit-identity (np.array_equal, not allclose)
# after clearing the module-level model cache within one process; cross-process
# identity follows from that same CPU determinism, not from RNG seeds.
# ---------------------------------------------------------------------------

# 30 short, varied feedback strings.  Deliberately chosen to span several distinct
# themes so HDBSCAN has real separation to work with across both runs.
_DETERMINISM_CORPUS: list[str] = [
    # Login / auth theme (10)
    "I cannot log in to my account anymore",
    "Login page shows a blank screen on iOS",
    "Two-factor authentication is completely broken",
    "Keeps logging me out every 30 minutes",
    "Password reset email never arrives",
    "The sign-in button does nothing when tapped",
    "Face ID login stopped working after the update",
    "My account got locked out for no reason",
    "OAuth with Google fails with an error 400",
    "Session expires too fast, I have to keep logging in",
    # Dark-mode / UI theme (10)
    "Dark mode text is impossible to read",
    "The dark theme colours are way too bright",
    "Please add a proper dark mode option",
    "App looks great in dark mode, love the design",
    "Night mode makes my eyes happy, well done",
    "The new dark UI is beautiful and sleek",
    "Dark mode toggle should be in settings, not buried",
    "White text on dark background is perfect for night",
    "Love the dark theme update, very professional look",
    "The dark mode contrast could use improvement",
    # Performance / crashes theme (10)
    "App crashes every time I open it on Android",
    "Takes forever to load the main dashboard",
    "Memory leak causes the app to slow down over time",
    "Freezes when I try to export a large file",
    "The widget crashes my home screen repeatedly",
    "Background sync makes everything else sluggish",
    "Crash on startup since the latest update rolled out",
    "Infinite spinner when loading my project list",
    "Force-close happens if I switch apps and come back",
    "Battery drain is massive since the last release",
]


def test_embed_is_bit_identical_after_model_reload():
    """embed() must return the exact same matrix regardless of model cache state.

    Clears the module-level model cache (_model = None) between the two calls so
    the encoder is re-initialised from its weight files within the same process.
    CPU inference with fixed weights is repeatable, so both runs must be
    bit-identical — not merely close. Cross-process identity then follows for free
    from that CPU determinism; it is NOT a consequence of the RNG seeds here.
    Asserts np.array_equal (bit-identical), not np.allclose.
    """
    import os
    os.environ["EMBED_DEVICE"] = "cpu"

    # First encode pass — model loads from disk (or HF cache).
    m1 = clusterer.embed(_DETERMINISM_CORPUS)

    # Clear the cache so the encoder re-initialises from weights on the next call.
    clusterer._model = None
    m2 = clusterer.embed(_DETERMINISM_CORPUS)

    assert m1.shape == m2.shape, (
        f"Shape mismatch: {m1.shape} vs {m2.shape}"
    )
    assert np.array_equal(m1, m2), (
        f"Embeddings are NOT bit-identical after model reload.\n"
        f"First mismatching index: {next(i for i in range(len(m1)) if not np.array_equal(m1[i], m2[i]))}\n"
        f"Max abs diff: {np.abs(m1 - m2).max():.6e}"
    )


def test_end_to_end_determinism_through_umap_and_hdbscan():
    """Full pipeline embed→reduce→cluster must produce identical label arrays when
    the model cache is cleared between the two runs within one process.

    What this proves: CPU inference with fixed weights is bit-identical after a
    model-cache reset; that bit-identical embedding matrix propagates through
    UMAP (pinned via random_state=42) and HDBSCAN (deterministic given fixed
    input) to produce the same cluster labels both times.  Cross-process identity
    follows from the same CPU determinism guarantee — the RNG seeds here are not
    the source of that property.

    Corpus is ≥15 items, so UMAP runs (not bypassed). Parameters match the
    values the original bug manifested with (n_neighbors=15, n_components=10,
    min_cluster_size=5).
    """
    import os
    os.environ["EMBED_DEVICE"] = "cpu"

    # Run 1.
    m1 = clusterer.embed(_DETERMINISM_CORPUS)
    r1 = clusterer.reduce(m1, n_neighbors=15, n_components=10)
    l1 = clusterer.cluster(r1, min_cluster_size=5)

    # Clear the cache so the encoder re-initialises from weights on the next call.
    clusterer._model = None

    # Run 2.
    m2 = clusterer.embed(_DETERMINISM_CORPUS)
    r2 = clusterer.reduce(m2, n_neighbors=15, n_components=10)
    l2 = clusterer.cluster(r2, min_cluster_size=5)

    # Embedding matrices must be bit-identical (prerequisite for the rest).
    assert np.array_equal(m1, m2), (
        "Embedding matrices differ — fix did not take effect.\n"
        f"Max abs diff: {np.abs(m1 - m2).max():.6e}"
    )

    # Reduced matrices must be bit-identical (UMAP random_state=42 + identical input).
    assert np.array_equal(r1, r2), (
        "UMAP reduced matrices differ — non-deterministic UMAP output.\n"
        f"Max abs diff: {np.abs(r1 - r2).max():.6e}"
    )

    # Cluster label arrays must be identical (HDBSCAN is deterministic given fixed input).
    assert np.array_equal(l1, l2), (
        "HDBSCAN label arrays differ — this is the 107 vs 109 regression.\n"
        f"Run-1 unique labels: {sorted(set(l1.tolist()))}\n"
        f"Run-2 unique labels: {sorted(set(l2.tolist()))}\n"
        f"First differing index: {next(i for i in range(len(l1)) if l1[i] != l2[i])}"
    )


# ---------------------------------------------------------------------------
# Canonicalization guard — sorted corpus order in clusterer_node
#
# Root cause of 43/42/38 cluster regression: UMAP+HDBSCAN are order-sensitive.
# The fix: `ids = sorted(texts)` so the corpus fed to embed→reduce→cluster is
# always in lexicographic id order regardless of dict insertion order or DB
# fetch order. This test inserts texts in DELIBERATELY NON-SORTED order and
# asserts that the embed stub receives them in sorted order. If someone reverts
# `sorted(texts)` to `list(texts)`, the assertion on received_corpus fails.
# ---------------------------------------------------------------------------

async def test_clusterer_node_feeds_embed_in_sorted_id_order(monkeypatch):
    """clusterer_node must sort by id before calling embed, regardless of texts dict order.

    Three ids chosen so that insertion order ('zz', 'aa', 'mm') differs from
    sorted order ('aa', 'mm', 'zz'). The fake embed records the corpus it
    receives. reduce and cluster are replaced with identity/trivial stubs so
    the test is fast, and _persist is a no-op. The assertions then check:
    1. embed received the corpus in sorted('aa', 'mm', 'zz') order.
    2. The returned embeddings and clusters dicts are keyed by ALL original ids.
    """
    received_corpus: list[str] = []

    # N = 3 texts; shape 3×8 (8-dim avoids any dimensionality issues and keeps it tiny).
    def fake_embed(corpus: list[str]) -> np.ndarray:
        received_corpus.clear()
        received_corpus.extend(corpus)
        n = len(corpus)
        return np.arange(n * 8, dtype=float).reshape(n, 8)

    # Stub reduce to identity so UMAP is never imported.
    def fake_reduce(matrix, n_neighbors, n_components):
        return matrix

    # Stub cluster: return all zeros (one cluster for all items) so that
    # no noise points exist and the label->cluster_id remap path is exercised.
    def fake_cluster(reduced, min_cluster_size):
        return np.zeros(len(reduced), dtype=int)

    # rescue_noise with all-zero labels: nothing to rescue, labels unchanged.
    # We do NOT monkeypatch rescue_noise — it's pure numpy and fast enough. But
    # to keep this test a pure unit test we stub it out too.
    def fake_rescue_noise(reduced, labels, rescue_percentile):
        return labels, 0

    async def fake_persist(updates):
        return None

    monkeypatch.setattr(clusterer, "embed", fake_embed)
    monkeypatch.setattr(clusterer, "reduce", fake_reduce)
    monkeypatch.setattr(clusterer, "cluster", fake_cluster)
    monkeypatch.setattr(clusterer, "rescue_noise", fake_rescue_noise)
    monkeypatch.setattr(clusterer, "_persist", fake_persist)

    # Insert texts in DELIBERATELY NON-SORTED insertion order.
    # sorted order is: 'aa', 'mm', 'zz'
    texts = {
        "zz": "zebra text that should arrive last",
        "aa": "alpha text that should arrive first",
        "mm": "middle text that should arrive second",
    }
    sorted_ids = sorted(texts)          # ['aa', 'mm', 'zz']
    expected_corpus = [texts[i] for i in sorted_ids]

    state: dict = {"texts": texts, "params": {"min_cluster_size": 2}}
    out = await clusterer.clusterer_node(state)

    # --- Core assertion: embed received corpus in sorted id order ---
    assert received_corpus == expected_corpus, (
        f"embed() received corpus in wrong order.\n"
        f"  expected (sorted): {expected_corpus}\n"
        f"  got:               {received_corpus}\n"
        "If this fails, `ids = sorted(texts)` was reverted to `list(texts)`."
    )

    # --- Keying assertions: all original ids appear in output dicts ---
    assert set(out["embeddings"].keys()) == {"aa", "mm", "zz"}, (
        f"embeddings keys wrong: {set(out['embeddings'].keys())}"
    )
    assert set(out["clusters"].keys()) == {"aa", "mm", "zz"}, (
        f"clusters keys wrong: {set(out['clusters'].keys())}"
    )

    # Each embedding must be a 8-element list (matching the fake matrix width).
    for iid in sorted_ids:
        assert len(out["embeddings"][iid]) == 8, (
            f"embedding for {iid!r} has wrong length {len(out['embeddings'][iid])}"
        )
