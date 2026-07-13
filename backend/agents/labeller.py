from __future__ import annotations

import asyncio
import json
import logging
import os
from collections import defaultdict
from datetime import datetime, timezone

import numpy as np
from sqlalchemy import delete, select, update

from agents.state import PipelineState
from db.database import AsyncSessionFactory
from db.models import ClusterORM, FeedbackItemORM

logger = logging.getLogger(__name__)

_MODEL = "claude-sonnet-4-5"          # cost-tier: cheapest model good enough for labelling
_DEDUPE_MODEL = "claude-sonnet-4-5"   # same tier; one call for the whole dedupe pass
_SAMPLE_SIZE = 15                     # items sent to Claude per cluster (representatives)
_TEXT_TRUNCATE = 300
_CONCURRENCY = 5                      # bound simultaneous API calls
_CATEGORIES = {"bug", "feature_request", "praise", "complaint", "pricing", "ux", "other"}

_SYSTEM = (
    "You label clusters of user feedback for a product team. You are given a sample of "
    "reviews from one cluster. Respond with ONLY a JSON object, no prose, no markdown "
    "fences, with exactly these keys:\n"
    '  "label": a 3-6 word human-readable theme name\n'
    '  "category": one of bug, feature_request, praise, complaint, pricing, ux, other\n'
    '  "sentiment": one of positive, negative, mixed, neutral\n'
    '  "summary": one sentence describing what these users are saying\n\n'
    "CATEGORY DEFINITIONS — use these precisely:\n"
    "  bug: something is broken or behaves incorrectly. "
    "HARD CONSTRAINT: a bug label MUST name one specific root cause AND one specific "
    "symptom in the label itself (e.g. 'Notes fail to sync on iPad', "
    "'Keyboard dismisses mid-input on iPhone'). Labels like 'iPad issues', "
    "'quality problems', or 'needs improvement' are NOT valid bug labels. "
    "If the cluster has no single identifiable root cause, do NOT use category=bug. "
    "Instead use category=other and set label to EXACTLY 'Mixed/unclear feedback'.\n"
    "  feature_request: users want something that does not exist yet.\n"
    "  ux: the product works but is confusing, hard to use, or inaccessible. "
    "Accessibility complaints (font size, contrast, screen-reader gaps) and "
    "usability friction belong here, or in feature_request if users are asking for "
    "a new control — NOT in other.\n"
    "  complaint: dissatisfaction that is not a specific bug (e.g. pricing friction, "
    "slow support responses, product direction disagreement).\n"
    "  pricing: feedback specifically about cost, plans, or value for money.\n"
    "  praise: positive sentiment about the product or team.\n"
    "  other: ONLY genuinely unclassifiable or mixed feedback. Using 'other' as a "
    "default when a better category fits is a failure. Mixed-root-cause bug clusters "
    "that cannot meet the bug hard constraint above are the primary legitimate use of "
    "'other' (label them 'Mixed/unclear feedback')."
)


def _get_client():
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set — the Labeller needs it to call Claude")
    from anthropic import AsyncAnthropic  # lazy so importing this module never needs the key

    return AsyncAnthropic(api_key=key)


def select_representatives(ids: list[str], embeddings: dict[str, list[float]], k: int = _SAMPLE_SIZE) -> list[str]:
    """The k-2 items closest to the cluster centroid (most representative) plus the 2
    farthest (for range). Returns all ids if the cluster is smaller than k."""
    if len(ids) <= k:
        return list(ids)
    mat = np.array([embeddings[i] for i in ids], dtype=float)
    centroid = mat.mean(axis=0)
    centroid /= np.linalg.norm(centroid) + 1e-12
    norms = mat / (np.linalg.norm(mat, axis=1, keepdims=True) + 1e-12)
    sims = norms @ centroid
    order = np.argsort(sims)  # ascending: front = farthest, back = closest
    closest = [ids[i] for i in order[-(k - 2):]]
    outliers = [ids[i] for i in order[:2]]
    return closest + outliers


def parse_label_response(text: str) -> dict | None:
    """Strip markdown fences and parse strict JSON. Returns the validated dict or None."""
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[-1] if "\n" in t else t
        t = t.rsplit("```", 1)[0] if "```" in t else t
        if t.lstrip().startswith("json"):
            t = t.lstrip()[4:]
    try:
        obj = json.loads(t)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(obj, dict) or not {"label", "category", "sentiment", "summary"} <= obj.keys():
        return None
    if obj["category"] not in _CATEGORIES:
        obj["category"] = "other"
    return {k: obj[k] for k in ("label", "category", "sentiment", "summary")}


def _prompt(sample_texts: list[str]) -> str:
    lines = [f"{i + 1}. {t[:_TEXT_TRUNCATE]}" for i, t in enumerate(sample_texts)]
    return "Cluster sample:\n" + "\n".join(lines)


async def _label_cluster(client, sample_texts: list[str]) -> tuple[dict, str | None]:
    """One Claude call per cluster. Retries once on malformed JSON, then falls back."""
    prompt = _prompt(sample_texts)
    for attempt in range(2):
        resp = await client.messages.create(
            model=_MODEL,
            max_tokens=400,
            system=_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
        text = next((b.text for b in resp.content if b.type == "text"), "")
        parsed = parse_label_response(text)
        if parsed:
            return parsed, None
    fallback = {"label": "unlabelled", "category": "other", "sentiment": "neutral", "summary": ""}
    return fallback, "label parse failed after retry"


async def _label_all(client, cluster_samples: dict[str, list[str]], concurrency: int = _CONCURRENCY):
    sem = asyncio.Semaphore(concurrency)

    async def one(cid: str, texts: list[str]):
        async with sem:
            return cid, await _label_cluster(client, texts)

    results = await asyncio.gather(*(one(cid, t) for cid, t in cluster_samples.items()))
    return dict(results)


_DEDUPE_SYSTEM = (
    "You are deduplicating user-feedback cluster labels for a product team.\n\n"
    "MERGE TOLERANCE IS ASYMMETRIC BY CATEGORY — follow these rules precisely:\n\n"
    "PRAISE / POSITIVE SENTIMENT — merge AGGRESSIVELY.\n"
    "Users loving the product for broadly similar reasons is ONE theme. Do not produce "
    "multiple flavours of 'I love it' or 'great app.' Consolidate all general positive "
    "sentiment into a single theme unless the praise is specifically about a distinct, "
    "concrete feature (e.g. 'love the offline mode' is separable from generic praise).\n\n"
    "BUG — merge CONSERVATIVELY.\n"
    "Two bug clusters merge ONLY IF a single engineer would fix them with a single code "
    "change AND they share one root cause. Crashes, input/keyboard bugs, sync failures, "
    "and won't-load failures are DIFFERENT bugs and MUST stay separate even if they all "
    "surface on the same platform (e.g. 'iOS'). NEVER produce a bug theme that spans "
    "multiple distinct root causes. When in doubt, keep them separate.\n\n"
    "VAGUE / MIXED-ROOT-CAUSE CLUSTERS — fold, do not preserve.\n"
    "If a cluster label is 'Mixed/unclear feedback', a generic 'quality issues', "
    "'needs improvement', or any other label that does not name a specific root cause, "
    "FOLD it into the most similar SPECIFIC neighbouring theme when one clearly exists. "
    "Only leave it as a standalone theme if no specific neighbour is a good fit.\n\n"
    "FEATURE REQUEST — merge only if it is literally the same requested feature. "
    "'Add dark mode' and 'add widget support' are different.\n\n"
    "COMPLAINT / UX — moderate tolerance. Merge only if items share the same root cause "
    "and a single product decision would address all of them.\n\n"
    "NAMING: prefer a SPECIFIC, actionable theme name over a broad one. "
    "'Notes fail to sync on iPad' beats 'iPad issues.' "
    "'Keyboard dismisses mid-input on iPhone' beats 'iOS input problems.'\n\n"
    "DISTINCT-PROBLEM GUARDRAIL: clusters describing separate user-visible outcomes must "
    "stay separate regardless of shared surface. 'Login broken' and 'data loss after logout' "
    "are related but DIFFERENT and must not be merged.\n\n"
    "The correct number of output themes is determined by correct merging — do not target "
    "any specific count.\n\n"
    "Respond with ONLY strict JSON, no prose, no markdown fences:\n"
    '{"themes": [{"canonical_label": "...", "category": "...", "member_cluster_ids": ["cluster_0", ...]}]}'
)


def build_dedupe_prompt(labels: dict[str, dict], counts: dict[str, int]) -> str:
    """Pure: formats all cluster labels into a single numbered list for the dedupe call."""
    lines = []
    for cid, meta in labels.items():
        n = counts.get(cid, 0)
        summary_snippet = (meta.get("summary") or "")[:120]
        lines.append(
            f"- id={cid}  label=\"{meta['label']}\"  category={meta['category']}"
            f"  n={n}  summary=\"{summary_snippet}\""
        )
    return "Clusters to deduplicate:\n" + "\n".join(lines)


def parse_dedupe_response(text: str, valid_cids: set[str]) -> list[dict] | None:
    """Strip markdown fences, parse JSON, validate shape. Drops member ids not in valid_cids.
    Returns the cleaned themes list or None on any structural failure."""
    t = text.strip()
    # Strip fences using the same approach as parse_label_response.
    if t.startswith("```"):
        t = t.split("\n", 1)[-1] if "\n" in t else t
        t = t.rsplit("```", 1)[0] if "```" in t else t
        if t.lstrip().startswith("json"):
            t = t.lstrip()[4:]
    try:
        obj = json.loads(t)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(obj, dict) or "themes" not in obj or not isinstance(obj["themes"], list):
        return None
    cleaned: list[dict] = []
    # Track globally which cluster_ids have already been assigned to a theme so that
    # a cluster the LLM lists under two themes is only kept in the first one it appears
    # in.  Without this guard, the second assignment silently overwrites the first in
    # merge_map, corrupting item_count in _persist.
    seen: set[str] = set()
    for theme in obj["themes"]:
        if not isinstance(theme, dict):
            continue
        if not {"canonical_label", "category", "member_cluster_ids"} <= theme.keys():
            continue
        if not isinstance(theme["canonical_label"], str) or not isinstance(theme["category"], str):
            continue
        if not isinstance(theme["member_cluster_ids"], list):
            continue
        # Silently drop ids the LLM hallucinated AND ids already claimed by an earlier theme.
        members = [
            m for m in theme["member_cluster_ids"]
            if isinstance(m, str) and m in valid_cids and m not in seen
        ]
        if not members:
            continue
        seen.update(members)
        cleaned.append({
            "canonical_label": theme["canonical_label"],
            "category": theme["category"] if theme["category"] in _CATEGORIES else "other",
            "member_cluster_ids": members,
        })
    return cleaned if cleaned else None


async def dedupe_clusters(
    client,
    labels: dict[str, dict],
    counts: dict[str, int],
) -> tuple[dict[str, str], dict[str, dict]]:
    """One Claude call to group all clusters into canonical themes.
    Falls back to identity map (every cluster is its own theme) on total failure.
    Returns (merge_map: old_cid -> canonical_cid, merged_meta: canonical_cid -> label dict).
    The canonical cid for each theme is the LARGEST member cluster by item count, so that
    feedback_items.cluster_id remapping and _persist stay identical."""
    valid_cids = set(labels)
    prompt = build_dedupe_prompt(labels, counts)

    themes: list[dict] | None = None
    for attempt in range(2):
        resp = await client.messages.create(
            model=_DEDUPE_MODEL,
            max_tokens=4000,
            system=_DEDUPE_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
        text = next((b.text for b in resp.content if b.type == "text"), "")
        themes = parse_dedupe_response(text, valid_cids)
        if themes is not None:
            break
        logger.warning("dedupe_clusters: parse failure on attempt %d", attempt + 1)

    if themes is None:
        # Fall back: identity map, no merges. Every cluster survives as-is.
        logger.warning("dedupe_clusters: falling back to identity map (no merges)")
        merge_map = {c: c for c in valid_cids}
        merged_meta = {c: labels[c] for c in valid_cids}
        return merge_map, merged_meta

    # Build merge_map and merged_meta from the theme list.
    # Clusters the LLM omitted entirely survive as single-member themes.
    assigned: set[str] = set()
    merge_map: dict[str, str] = {}
    merged_meta: dict[str, dict] = {}

    for theme in themes:
        members = theme["member_cluster_ids"]
        # Canonical = largest by count (ties broken by cid sort for determinism).
        canon = max(members, key=lambda c: (counts.get(c, 0), c))
        assigned.update(members)
        for m in members:
            merge_map[m] = canon
        # Carry sentiment + summary from the canonical cluster's original label dict,
        # because ClusterORM requires them NOT NULL and the dedupe LLM doesn't return them.
        canon_label_dict = labels[canon]
        merged_meta[canon] = {
            "label": theme["canonical_label"],
            "category": theme["category"],
            "sentiment": canon_label_dict["sentiment"],
            "summary": canon_label_dict["summary"],
        }

    # Any cluster the LLM omitted is its own theme (never silently dropped).
    for c in valid_cids - assigned:
        merge_map[c] = c
        merged_meta[c] = labels[c]

    return merge_map, merged_meta


async def _persist(merged_meta: dict[str, dict], merge_map: dict[str, str], counts: dict[str, int]) -> None:
    now = datetime.now(timezone.utc)
    async with AsyncSessionFactory() as db:
        # Remap feedback_items.cluster_id for any non-canonical member.
        for old, new in merge_map.items():
            if old != new:
                await db.execute(
                    update(FeedbackItemORM)
                    .where(FeedbackItemORM.cluster_id == old)
                    .values(cluster_id=new)
                )
        # Rewrite the clusters table with the final merged set.
        await db.execute(delete(ClusterORM))
        for cid, meta in merged_meta.items():
            merged_count = sum(counts.get(o, 0) for o, n in merge_map.items() if n == cid)
            db.add(ClusterORM(
                cluster_id=cid,
                label=meta["label"],
                category=meta["category"],
                sentiment=meta["sentiment"],
                summary=meta["summary"],
                item_count=merged_count,
                created_at=now,
            ))
        await db.commit()


async def labeller_node(state: PipelineState) -> dict:
    texts = state.get("texts", {})
    embeddings = state.get("embeddings", {})
    clusters = state.get("clusters", {})

    members: dict[str, list[str]] = defaultdict(list)
    for item_id, cid in clusters.items():
        if cid:
            members[cid].append(item_id)
    if not members:
        return {"labels": {}}

    counts = {cid: len(ids) for cid, ids in members.items()}
    samples = {
        cid: [texts[i] for i in select_representatives(ids, embeddings)]
        for cid, ids in members.items()
    }

    client = _get_client()
    labelled = await _label_all(client, samples)
    labels = {cid: res[0] for cid, res in labelled.items()}
    errors = [f"labeller {cid}: {res[1]}" for cid, res in labelled.items() if res[1]]

    # One Claude call to group all labelled clusters into canonical themes.
    # This replaces the old label-embedding cosine approach which under-merged because
    # short synonym strings share little vocabulary at the embedding level.
    merge_map, merged_meta = await dedupe_clusters(client, labels, counts)
    await _persist(merged_meta, merge_map, counts)

    merges = {o: n for o, n in merge_map.items() if o != n}
    logger.info(
        "labeller: %d clusters -> %d themes (%d merged away); merge_map=%s",
        len(labels), len(merged_meta), len(merges), merges,
    )
    return {"labels": labels, "errors": state.get("errors", []) + errors}
