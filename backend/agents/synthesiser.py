from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any

import numpy as np
from sqlalchemy import delete, select

from agents.state import PipelineState
from db.database import AsyncSessionFactory
from db.models import ClusterORM, FeedbackItemORM, InsightReportORM

logger = logging.getLogger(__name__)

_MODEL = "claude-sonnet-4-5"
_CONCURRENCY = 5          # bound simultaneous Claude calls
_CENTROID_SAMPLE = 20     # items closest to the centroid
_EXTREME_SAMPLE = 5       # items with most extreme star ratings (low + high)
_TEXT_TRUNCATE = 400

# Patterns for the version/date heuristic.  This is intentionally conservative —
# it only flags tokens that look like version numbers or calendar years.
# It catches invented "v1.2.3" or "iOS 17" references but CANNOT catch invented prose
# that has no numeric marker.  The real defence is evidence-id grounding; this is a
# supplementary signal.  Do NOT drop findings on this heuristic alone.
_VERSION_PATTERN = re.compile(r"\bv?\d+\.\d+(?:\.\d+)?\b|\biOS\s+\d+\b|\bAndroid\s+\d+\b|\b(19|20)\d{2}\b")

# Churn-language signals: words that users explicitly use when they are leaving or
# have already decided to leave.  Conservative list — misses passive unhappiness
# but keeps false-positive rate low.  Tune the BUCKETS in churn_signal, not here.
_CHURN_REGEX = re.compile(
    r"cancel|cancell|unsubscrib|switching?\s+to|moving\s+to|refund|"
    r"deleting|delete\s+the\s+app|uninstall|no\s+longer\s+using|"
    r"won'?t\s+be\s+using|leaving|gave\s+up\s+on",
    re.IGNORECASE,
)

# Severity-4 indicators: patterns that signal access-blocking / data-loss / security.
# These are evaluated against theme label + summary, not raw items.
_SEV4_REGEX = re.compile(
    r"data\s?loss|lost\s+.{0,20}(?:note|data|work)|deleted|"
    r"logg?ed\s+out|log\s?in|sign\s?in|can'?t\s+(?:log|sign)|locked\s+out|"
    r"crash\s+on\s+launch|won'?t\s+open|breach|security",
    re.IGNORECASE,
)


def _get_client():
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set — Synthesiser needs it")
    from anthropic import AsyncAnthropic  # lazy import so module can be imported without key

    return AsyncAnthropic(api_key=key)


def _report_id(cluster_id: str) -> str:
    """Deterministic report id keyed on cluster_id so upserts are idempotent."""
    return hashlib.sha256(f"report:{cluster_id}".encode()).hexdigest()[:24]


# ---------------------------------------------------------------------------
# Representative item selection
# ---------------------------------------------------------------------------

def _select_centroid_reps(
    item_ids: list[str],
    embeddings: dict[str, list[float]],
    k: int = _CENTROID_SAMPLE,
) -> list[str]:
    """Return up to k item ids closest to the cluster centroid by cosine similarity.

    Mirrors labeller.select_representatives but keeps only the closest — no outlier
    slots — because synthesiser wants the most representative voice, not edge cases.
    """
    # Only items with an embedding can be ranked by centroid distance. Items missing
    # one (nullable column) can't be scored — keep all ids if too few are embeddable.
    embeddable = [i for i in item_ids if i in embeddings]
    if len(embeddable) <= k:
        return list(item_ids)
    mat = np.array([embeddings[i] for i in embeddable], dtype=float)
    centroid = mat.mean(axis=0)
    centroid /= np.linalg.norm(centroid) + 1e-12
    norms = mat / (np.linalg.norm(mat, axis=1, keepdims=True) + 1e-12)
    sims = norms @ centroid
    top_indices = np.argsort(sims)[-k:]  # ascending order, take from the end
    return [embeddable[i] for i in top_indices]


def _select_extreme_stars(
    items: list[dict],
    excluded_ids: set[str],
    k: int = _EXTREME_SAMPLE,
) -> list[str]:
    """Return up to k item ids with the most extreme star ratings (lowest first, then highest).

    Excludes ids already chosen by centroid selection.  This catches clear praise / clear
    frustration that might sit far from the centroid and be missed otherwise.
    """
    candidates = [it for it in items if it["id"] not in excluded_ids]
    if not candidates:
        return []
    # Sort by stars ascending; take first k//2 (lowest stars) and last k//2 (highest stars).
    by_stars = sorted(candidates, key=lambda x: x.get("stars", 3))
    half = max(1, k // 2)
    low = [it["id"] for it in by_stars[:half]]
    high = [it["id"] for it in by_stars[-half:]]
    # Remove duplicates while preserving order.
    seen: set[str] = set()
    result: list[str] = []
    for iid in low + high:
        if iid not in seen:
            seen.add(iid)
            result.append(iid)
    return result[:k]


# ---------------------------------------------------------------------------
# Aggregate statistics — computed entirely in Python, never by the LLM
# ---------------------------------------------------------------------------

def _compute_stats(items: list[dict]) -> dict:
    """Return aggregate stats over ALL items in a theme (not the sample).

    These numbers are the only numbers the synthesiser is allowed to quote —
    the system prompt forbids Claude from inventing statistics not passed here.
    """
    now = datetime.now(timezone.utc)
    window_30 = now - timedelta(days=30)
    window_60 = now - timedelta(days=60)

    star_dist: dict[int, int] = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}
    country_counts: dict[str, int] = {}
    dates: list[datetime] = []
    last_30 = 0
    prior_30 = 0  # 30–60 days ago

    for it in items:
        stars = it.get("stars")
        if isinstance(stars, (int, float)) and 1 <= int(stars) <= 5:
            star_dist[int(stars)] += 1

        country = it.get("country", "unknown") or "unknown"
        country_counts[country] = country_counts.get(country, 0) + 1

        created = it.get("created_at")
        if isinstance(created, datetime):
            dates.append(created)
            if created >= window_30:
                last_30 += 1
            elif created >= window_60:
                prior_30 += 1

    return {
        "item_count": len(items),
        "star_distribution": star_dist,
        "country_breakdown": dict(sorted(country_counts.items(), key=lambda x: -x[1])[:10]),
        "date_range": {
            "min": min(dates).isoformat() if dates else None,
            "max": max(dates).isoformat() if dates else None,
        },
        "last_30_days": last_30,
        "prior_30_days": prior_30,
        "trend": "increasing" if last_30 > prior_30 else ("decreasing" if last_30 < prior_30 else "stable"),
    }


# ---------------------------------------------------------------------------
# Python-anchored churn and priority signals
# ---------------------------------------------------------------------------

def churn_signal(items: list[dict], stats: dict) -> str:
    """Compute churn risk from countable evidence — no LLM involvement.

    Two independent signals drive the bucket:
      - one_star_ratio    : fraction of items rated 1★ (proxy for strong dissatisfaction)
      - churn_language_ratio : fraction of items whose text explicitly mentions leaving/cancelling

    NOTE: this function takes no category — the praise/other -> "none" override is
    enforced at the call site in _synthesise_theme, not here.

    Buckets (lower bound, first match wins):
      none   — both ratios below minimum thresholds
      low    — one_star_ratio >= 0.08 OR any churn language present
      medium — one_star_ratio >= 0.25 OR churn_language_ratio >= 0.07
      high   — one_star_ratio >= 0.50 OR churn_language_ratio >= 0.15

    These thresholds are tunable; they encode "how bad is bad enough to call it high churn".
    Document changes in eval/README.md.
    """
    item_count = stats.get("item_count", len(items))
    if item_count == 0:
        return "none"

    star_dist = stats.get("star_distribution", {})
    one_star_count = star_dist.get(1, 0)
    one_star_ratio = one_star_count / item_count

    churn_language_count = sum(
        1 for it in items if _CHURN_REGEX.search(it.get("text") or "")
    )
    churn_language_ratio = churn_language_count / item_count

    if one_star_ratio >= 0.50 or churn_language_ratio >= 0.15:
        return "high"
    if one_star_ratio >= 0.25 or churn_language_ratio >= 0.07:
        return "medium"
    if one_star_ratio >= 0.08 or churn_language_ratio > 0:
        return "low"
    return "none"


def priority_signal(theme: dict, items: list[dict], stats: dict) -> str:
    """Compute priority from category + keyword scan — no LLM involvement.

    Severity levels (highest wins):
      sev4 — access-blocking / data-loss / security (regex on label+summary)
      sev3 — category == "bug" (crash/freeze/broken)
      sev2 — category in {ux, complaint, pricing}
      sev1 — category == "feature_request"
      sev0 — praise / other

    Base priority map:
      sev4 → P0 | sev3 → P1 | sev2 → P2 | sev1 → P2 | sev0 → P3

    Volume nudge: item_count >= 80 bumps up one level (caps at P0).
    Trend nudge : last_30_days > prior_30_days bumps up one level (caps at P0).
    Each nudge is independent; both can apply (max two-level bump from P2 → P0).

    Document formula changes in eval/README.md.
    """
    category = (theme.get("category") or "").lower()
    label = theme.get("label") or ""
    summary = theme.get("summary") or ""
    scan_text = f"{label} {summary}"

    item_count = stats.get("item_count", len(items))
    last_30 = stats.get("last_30_days", 0)
    prior_30 = stats.get("prior_30_days", 0)

    # Determine severity level.
    if _SEV4_REGEX.search(scan_text):
        sev = 4
    elif category == "bug":
        sev = 3
    elif category in ("ux", "complaint", "pricing"):
        sev = 2
    elif category == "feature_request":
        sev = 1
    else:
        # praise, other, unknown
        sev = 0

    # Base priority.
    base = {4: "P0", 3: "P1", 2: "P2", 1: "P2", 0: "P3"}[sev]

    # Nudge function: bump one level up, capped at P0.
    def _bump(p: str) -> str:
        return {"P3": "P2", "P2": "P1", "P1": "P0", "P0": "P0"}[p]

    # sev3 base is already P1; volume nudge to P0 only if item_count >= 80.
    if item_count >= 80:
        base = _bump(base)

    # Growing trend in the last 30 days signals acceleration — bump once more.
    # Require at least 3 items in the prior window to avoid spurious nudges from
    # tiny clusters where prior_30=0 makes every cluster look "accelerating".
    if last_30 > prior_30 and prior_30 >= 3:
        base = _bump(base)

    return base


# ---------------------------------------------------------------------------
# Groundedness validation
# ---------------------------------------------------------------------------

def _flag_unsupported_specifics(claim: str, shown_texts: list[str]) -> list[str]:
    """Heuristic: find version/date tokens in `claim` that appear in NONE of shown_texts.

    This is a HEURISTIC — it catches invented version or date TOKENS (e.g. "v1.2",
    "iOS 17", "2024") that Claude inserted without evidence, but it CANNOT catch
    invented prose claims that carry no numeric marker.  The real defence is
    evidence-id grounding (hard gate) and the system prompt.  Do NOT gate findings
    on this result alone; use it only for logging/monitoring.
    """
    # Use finditer + group(0): the year alternative has a capture group, so findall
    # would return tuples. group(0) always yields the whole matched token.
    unsupported: list[str] = []
    for m in _VERSION_PATTERN.finditer(claim):
        token = m.group(0)
        if not any(token.lower() in txt.lower() for txt in shown_texts):
            unsupported.append(token)
    return unsupported


# ---------------------------------------------------------------------------
# Prompt + Claude call
# ---------------------------------------------------------------------------

_SYSTEM = """\
You are a senior product analyst writing structured insight reports for a B2B SaaS product team.

You will be given:
- A theme label, category, sentiment, summary, and item_count
- Aggregate statistics computed over ALL items (star distribution, geography, trends)
- A SAMPLE of representative user reviews (up to 25)
- The system-computed priority and churn_risk you MUST echo back unchanged

Your output is STRICT JSON. No prose, no markdown fences, no code blocks. Return ONLY the JSON object.

REQUIRED JSON SHAPE:
{
  "title": "Short action-oriented title (max 10 words)",
  "priority": "<echo back the SYSTEM-ASSIGNED value exactly>",
  "priority_rationale": "1-2 sentences of EVIDENCE that justifies the priority — state the facts (e.g. '18 of 32 items are 1-2 star reviews'). FORBIDDEN: any sentence that starts with or contains 'This is a P[0-3]', 'assigned P', 'because it is a P', or any other meta-statement that names or explains the priority label itself.",
  "findings": [
    {
      "claim": "Specific, falsifiable claim supported by evidence",
      "evidence_item_ids": ["id1", "id2"]
    }
  ],
  "recommended_actions": [
    {
      "action": "Concrete, assignable action",
      "urgency": "immediate" | "this_sprint" | "next_quarter"
    }
  ],
  "affected_surface": "e.g. iOS app / web dashboard / notifications / null",
  "churn_risk": "<echo back the SYSTEM-ASSIGNED value exactly>",
  "churn_rationale": "1 sentence of EVIDENCE justifying the churn_risk — state the facts (e.g. '12 of 67 items mention cancelling or uninstalling'). FORBIDDEN: any phrasing that names the assigned label in the sentence (e.g. 'This is high churn because', 'churn is rated high because')."
}

HARD RULES — violating these is a correctness failure:

1. EVIDENCE REQUIRED: Every finding MUST include at least one evidence_item_id from the list of item IDs shown to you. A finding with no evidence_item_ids is forbidden. A finding citing an id NOT in the provided list will be discarded.

2. NO INVENTED NUMBERS: Do NOT invent version numbers, dates, release names, user counts, or revenue figures. The ONLY numbers permitted in your report are those in the provided aggregate stats. Never estimate or extrapolate.

3. NO SPECULATIVE ROOT CAUSE: Do not speculate about engineering root causes unless a user review explicitly states it (e.g. "the API throws a 404" — fine to quote; "probably caused by a race condition" — forbidden).

4. PRAISE / OTHER CATEGORIES: If the theme category is "praise" or "other", produce a SHORT report:
   - Keep findings minimal (1-2 max, citing positive evidence)
   - recommended_actions should be empty []

5. QUANTIFY CLAIMS: Prefer "N users report X" using the item_count from aggregate stats over vague quantifiers like "many" or "some".

6. SYSTEM-ASSIGNED VALUES: The priority and churn_risk values are pre-computed by the system.
   Return them verbatim so your JSON validates — a value outside the allowed set fails the
   parse and forces a retry. Write rationales that justify these exact assigned values.

7. RATIONALE STYLE — EVIDENCE, NOT META-NARRATION:
   priority_rationale and churn_rationale must STATE THE EVIDENCE, never narrate or explain
   the decision.
   GOOD: "18 of 32 items are 1-2 star reviews indicating strong dissatisfaction."
   FORBIDDEN (any variant of the following patterns):
     "This is a P1 because ..."
     "This is P1 because ..."
     "assigned P1 because ..."
     "because it is a P0 ..."
     "churn is rated high because ..."
     "This is high churn because ..."
   The rationale must read as if the label did not exist — pure evidence, no self-reference
   to the priority or churn bucket that was assigned.

---

FAITHFULNESS — HARD RULES (violations make the report worthless):

These rules map to real observed eval failures. Each produces claims that the faithfulness
judge will flag, causing the report to be downgraded or discarded.

F1. NO INVENTED SPECIFICS: Never state a number, duration, timeframe, percentage, quantity,
    or date that does not appear verbatim in the cited review text or the provided statistics.
    Bad examples: "within 2 days", "1 month before finals", "3+ years", "breaks after
    2-3 characters", "10-15 seconds". If you cannot find the specific in the evidence, omit it.

F2. NO DERIVED ARITHMETIC: Do not compute percentages or sums yourself, even if your
    calculation is correct. Quote the raw counts you were given instead.
    Bad: "43% of users report crashes" (derived). Good: "29 of 67 items are 1-star reviews".

F3. NO ROOT-CAUSE SPECULATION: Never hypothesise an engineering cause. State the
    user-visible symptom only.
    Bad: "indicating a potential backend or data model problem". Good: "users report
    that notes disappear after saving".

F4. NO GENERALISING FROM ONE REVIEW: If only one of several cited reviews mentions
    something, do not state it as the theme's behaviour.
    Bad: "the editor breaks after 2-3 characters" (said by 1 of 3 cited reviews).
    Good: "one user reports the editor breaks after typing a few characters".

F5. NO UNSUPPORTED DEVICE/PLATFORM/PLAN ATTRIBUTION: Do not say "on iPad",
    "paid users", "higher-tier plans" unless the cited review text explicitly says so.

F6. NO FUSING SEPARATE REVIEWS into one compound fact. Each review describes one
    user's experience; combining two independent observations into a single causal
    chain is fabrication.
    Bad: "page load times of 10-15 seconds before crashes occur" (two unrelated reviews).

F7. NO EXTERNAL STANDARDS OR FRAMEWORKS invented in claims or actions.
    Bad: "the UI does not meet WCAG 2.1 AA contrast requirements" (unless a review says so).

F8. PREFER HEDGED, FAITHFUL PHRASING over confident specificity. Vague but accurate
    beats precise but invented. "Several users report crashes when editing long notes"
    is better than "crashes occur after 10-15 seconds on notes over 2,000 words."
    When in doubt about a specific, say less.
"""


def _build_prompt(
    theme: dict,
    stats: dict,
    sample_items: list[dict],
    python_priority: str,
    python_churn: str,
) -> str:
    header = (
        f"THEME: {theme['label']}\n"
        f"Category: {theme['category']} | Sentiment: {theme['sentiment']}\n"
        f"Summary: {theme['summary']}\n"
        f"Total items in theme: {stats['item_count']}\n\n"
        # Tell Claude the Python-computed values upfront so its rationale is calibrated.
        f"SYSTEM-ASSIGNED VALUES (do NOT change these; write rationales that justify them):\n"
        f"  priority   = {python_priority}\n"
        f"  churn_risk = {python_churn}\n\n"
        f"AGGREGATE STATS (computed over ALL {stats['item_count']} items):\n"
        f"{json.dumps(stats, indent=2, default=str)}\n\n"
        f"REPRESENTATIVE SAMPLE ({len(sample_items)} items):\n"
    )
    item_lines = []
    for it in sample_items:
        text_snip = (it["text"] or "")[:_TEXT_TRUNCATE]
        item_lines.append(
            f"[id={it['id']}] stars={it.get('stars','?')} country={it.get('country','?')} "
            f"date={it.get('created_at_str','?')}\n  \"{text_snip}\""
        )
    return header + "\n".join(item_lines)


def _strip_fences(text: str) -> str:
    """Strip markdown code fences — mirrors labeller.parse_label_response approach."""
    t = text.strip()
    if t.startswith("```"):
        # Handles both ```json\n{...}\n``` and one-line ```{...}``` (no newline after fence).
        t = re.sub(r"^```(?:json)?", "", t).strip()
        if t.endswith("```"):
            t = t[:-3].strip()
    return t.strip()


# Claude still returns priority and churn_risk (required for valid JSON shape), but
# their VALUES are discarded — Python-computed values always win (Part C).
# Keeping them required preserves the existing parse-level contract: a response missing
# these keys is structurally malformed and should retry rather than proceed.
_REQUIRED_KEYS = {
    "title", "priority", "priority_rationale", "findings",
    "recommended_actions", "churn_risk", "churn_rationale",
}
_VALID_PRIORITIES = {"P0", "P1", "P2", "P3"}
_VALID_CHURN = {"high", "medium", "low", "none"}
_VALID_URGENCY = {"immediate", "this_sprint", "next_quarter"}


def _parse_report(text: str) -> dict | None:
    """Strip fences and parse strict JSON.  Returns the dict or None on any structural failure.

    Claude still emits priority/churn_risk; we validate their format but DISCARD the
    values — _synthesise_theme always overwrites them with python_priority/python_churn.
    """
    t = _strip_fences(text)
    try:
        obj = json.loads(t)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(obj, dict):
        return None
    if not _REQUIRED_KEYS <= obj.keys():
        return None
    if obj.get("priority") not in _VALID_PRIORITIES:
        return None
    if obj.get("churn_risk") not in _VALID_CHURN:
        return None
    return obj


def _minimal_fallback(
    cluster_id: str,
    theme: dict,
    reason: str,
    python_priority: str | None = None,
    python_churn: str | None = None,
) -> dict:
    """Safe fallback report used when Claude returns unrecoverable JSON.

    Uses the Python-computed priority/churn so even the fallback is calibrated —
    never hardcodes P3/none unless that's what the signals say.

    python_priority and python_churn are optional for backward compatibility with
    callers that predate Part C.  When absent, they default to P3/none (the safe
    conservative default), which is what the old implementation did.
    """
    effective_priority = python_priority if python_priority is not None else "P3"
    effective_churn = python_churn if python_churn is not None else "none"
    return {
        "id": _report_id(cluster_id),
        "cluster_id": cluster_id,
        "title": theme["label"],
        "priority": effective_priority,
        "priority_rationale": f"Synthesis failed ({reason}); priority assigned by system signal.",
        "findings": [],
        "recommended_actions": [],
        "actions": [],
        "affected_surface": None,
        "churn_risk": effective_churn,
        "churn_rationale": "Unable to assess — synthesis failed; churn assigned by system signal.",
        "item_count": theme["item_count"],
        "_synthesis_failed": True,
        "_hallucinated_findings": 0,
        "_partial_fabrications": 0,
        "_total_findings": 0,
    }


async def _synthesise_theme(
    client: Any,
    cluster_id: str,
    theme: dict,
    all_items: list[dict],
    embeddings: dict[str, list[float]],
) -> tuple[dict, list[str]]:
    """One Claude call per theme.  Returns (report_dict, errors).

    Priority and churn_risk are computed in Python BEFORE the Claude call.
    Claude only writes justification prose; it cannot change the signal values.
    """
    errors: list[str] = []
    item_ids = [it["id"] for it in all_items]

    # Select centroid representatives using cosine similarity (same approach as labeller).
    centroid_ids = _select_centroid_reps(item_ids, embeddings, k=_CENTROID_SAMPLE)
    extreme_ids = _select_extreme_stars(all_items, excluded_ids=set(centroid_ids), k=_EXTREME_SAMPLE)

    # Deduplicate while preserving centroid order, then appending extremes.
    shown_ids_ordered: list[str] = list(centroid_ids)
    for eid in extreme_ids:
        if eid not in set(shown_ids_ordered):
            shown_ids_ordered.append(eid)

    id_to_item = {it["id"]: it for it in all_items}
    sample_items = [
        {
            **id_to_item[iid],
            "created_at_str": id_to_item[iid]["created_at"].isoformat()
            if isinstance(id_to_item[iid].get("created_at"), datetime)
            else str(id_to_item[iid].get("created_at", "")),
        }
        for iid in shown_ids_ordered
        if iid in id_to_item
    ]

    stats = _compute_stats(all_items)

    # -----------------------------------------------------------------------
    # CRITICAL: compute priority + churn in Python BEFORE calling Claude.
    # Claude only writes justification; it cannot change these values.
    # This eliminates the calibration bug where Claude assigned high churn to
    # praise themes and over-used P1 across all bug categories.
    # -----------------------------------------------------------------------
    python_churn = churn_signal(all_items, stats)
    python_priority = priority_signal(theme, all_items, stats)

    # praise/other: override churn to none + priority to P3 regardless of signal.
    # The signal formulas don't hard-code this; we enforce it here so the gate
    # is visible at the call site.
    category = theme.get("category", "")
    if category in ("praise", "other"):
        python_priority = "P3"
        python_churn = "none"

    prompt = _build_prompt(theme, stats, sample_items, python_priority, python_churn)

    # Retry once on malformed JSON before falling back.
    raw: dict | None = None
    for attempt in range(2):
        try:
            resp = await client.messages.create(
                model=_MODEL,
                max_tokens=2500,  # headroom: 1500 truncated larger reports into invalid JSON
                system=_SYSTEM,
                messages=[{"role": "user", "content": prompt}],
            )
            text = next((b.text for b in resp.content if b.type == "text"), "")
            raw = _parse_report(text)
            if raw is not None:
                break
            logger.warning("synthesiser: parse failure for %s attempt %d", cluster_id, attempt + 1)
        except Exception as exc:
            logger.warning("synthesiser: Claude call failed for %s attempt %d: %s", cluster_id, attempt + 1, exc)

    if raw is None:
        errors.append(f"synthesiser:{cluster_id}: JSON parse failed after retry — using fallback")
        return _minimal_fallback(cluster_id, theme, "parse failed after retry", python_priority, python_churn), errors

    # -----------------------------------------------------------------------
    # GROUNDEDNESS GATE: hard evidence-id validation
    # -----------------------------------------------------------------------
    shown_id_set = set(shown_ids_ordered)
    shown_texts = [it["text"] or "" for it in sample_items]
    validated_findings: list[dict] = []
    hallucinated_findings = 0     # findings dropped entirely (no valid evidence)
    partial_fabrications = 0      # findings kept but with some fabricated ids stripped
    total_findings = len(raw.get("findings") or [])

    for finding in (raw.get("findings") or []):
        if not isinstance(finding, dict):
            continue
        claim = finding.get("claim", "")
        evidence_ids = finding.get("evidence_item_ids", [])
        if not isinstance(evidence_ids, list):
            evidence_ids = []

        # Hard gate: drop any finding whose evidence ids are ALL outside shown set.
        valid_evidence = [eid for eid in evidence_ids if eid in shown_id_set]
        if not valid_evidence and evidence_ids:
            # Claude cited ids we never sent — hallucinated citation.
            hallucinated_findings += 1
            errors.append(
                f"synthesiser:{cluster_id}: dropped finding (hallucinated evidence ids "
                f"{evidence_ids!r} not in shown set) — claim: {claim[:80]!r}"
            )
            continue
        if not valid_evidence:
            # Claude gave no evidence at all — still drop (rule: evidence required).
            hallucinated_findings += 1
            errors.append(
                f"synthesiser:{cluster_id}: dropped finding (no evidence_item_ids) — "
                f"claim: {claim[:80]!r}"
            )
            continue

        # Kept, but some cited ids were fabricated (not in shown set). The finding is still
        # grounded to a real item, but log it so the groundedness metric isn't blind to it.
        fabricated = [eid for eid in evidence_ids if eid not in shown_id_set]
        if fabricated:
            partial_fabrications += 1
            errors.append(
                f"synthesiser:{cluster_id}: partial evidence fabrication — stripped "
                f"{fabricated!r} (kept {valid_evidence!r}) — claim: {claim[:80]!r}"
            )

        # Supplementary heuristic: flag version/date tokens not supported by shown texts.
        # This is a heuristic — it catches invented TOKENS but not invented prose.
        # The hard gate above is the real defence.  Do not drop the finding here.
        unsupported_tokens = _flag_unsupported_specifics(claim, shown_texts)
        if unsupported_tokens:
            errors.append(
                f"synthesiser:{cluster_id}: WARNING — claim may contain unsupported "
                f"specifics {unsupported_tokens!r} (heuristic, not a hard drop): {claim[:80]!r}"
            )

        validated_findings.append({"claim": claim, "evidence_item_ids": valid_evidence})

    # -----------------------------------------------------------------------
    # Enforce praise/other constraints in Python regardless of LLM output.
    # -----------------------------------------------------------------------
    if category in ("praise", "other"):
        raw["recommended_actions"] = []

    # Validate recommended_actions shape; drop malformed entries rather than crashing.
    clean_actions: list[dict] = []
    for act in (raw.get("recommended_actions") or []):
        if not isinstance(act, dict):
            continue
        if act.get("urgency") not in _VALID_URGENCY:
            act["urgency"] = "next_quarter"
        if "action" in act:
            clean_actions.append({"action": str(act["action"]), "urgency": act["urgency"]})

    hallucination_rate = hallucinated_findings / total_findings if total_findings else 0.0

    report = {
        "id": _report_id(cluster_id),
        "cluster_id": cluster_id,
        "title": str(raw.get("title") or theme["label"])[:255],
        # Python-assigned values — Claude's response is ignored even if it returned them.
        "priority": python_priority,
        "churn_risk": python_churn,
        "priority_rationale": str(raw.get("priority_rationale") or ""),
        "findings": validated_findings,
        "recommended_actions": clean_actions,
        # Keep legacy `actions` in sync with recommended_actions for back-compat.
        "actions": [a["action"] for a in clean_actions],
        "affected_surface": raw.get("affected_surface") or None,
        "churn_rationale": str(raw.get("churn_rationale") or ""),
        "item_count": theme["item_count"],
        "hallucination_rate": hallucination_rate,
        # Raw counts so the node can aggregate a corpus-wide groundedness metric.
        "_hallucinated_findings": hallucinated_findings,
        "_partial_fabrications": partial_fabrications,
        "_total_findings": total_findings,
    }
    return report, errors


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

async def _persist_report(report: dict) -> None:
    """UPSERT one InsightReport.  Delete-then-insert makes the logic DB-agnostic.

    Re-runs are idempotent: same cluster_id -> same report id -> same row.
    """
    now = datetime.now(timezone.utc)
    async with AsyncSessionFactory() as db:
        await db.execute(
            delete(InsightReportORM).where(InsightReportORM.cluster_id == report["cluster_id"])
        )
        db.add(InsightReportORM(
            id=report["id"],
            cluster_id=report["cluster_id"],
            title=report["title"],
            priority=report["priority"],
            priority_rationale=report.get("priority_rationale"),
            findings=report["findings"],
            actions=report.get("actions", []),
            recommended_actions=report.get("recommended_actions", []),
            affected_surface=report.get("affected_surface"),
            churn_risk=report.get("churn_risk"),
            churn_rationale=report.get("churn_rationale"),
            item_count=report["item_count"],
            generated_at=now,
        ))
        await db.commit()


# ---------------------------------------------------------------------------
# Main node
# ---------------------------------------------------------------------------

async def synthesiser_node(state: PipelineState) -> dict:
    """LangGraph node: one Claude call per canonical theme, never per item.

    Reads themes and items directly from the DB — state["clusters"] and state["labels"]
    are pre-dedupe artefacts and are intentionally ignored here.  This makes the node
    runnable standalone against the existing 22 canonical themes.
    """
    errors: list[str] = list(state.get("errors") or [])

    # -----------------------------------------------------------------------
    # 1. Load canonical themes from the clusters table (post-dedupe source of truth).
    # -----------------------------------------------------------------------
    async with AsyncSessionFactory() as db:
        cluster_rows = (await db.execute(select(ClusterORM))).scalars().all()

    if not cluster_rows:
        logger.warning("synthesiser: no clusters found — skipping")
        return {"reports": [], "errors": errors}

    # -----------------------------------------------------------------------
    # 2. Load all feedback items with embeddings for each cluster.
    # -----------------------------------------------------------------------
    cluster_ids = [c.cluster_id for c in cluster_rows]

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

    # Group items by cluster_id and build embedding lookup.
    cluster_items: dict[str, list[dict]] = {cid: [] for cid in cluster_ids}
    embeddings: dict[str, list[float]] = {}

    for row in item_rows:
        item_id, text, metadata, created_at, embedding, cid = row
        if cid not in cluster_items:
            continue
        meta = metadata or {}
        item_dict = {
            "id": item_id,
            "text": text or "",
            "stars": meta.get("stars"),
            "country": meta.get("country", "unknown"),
            "created_at": created_at,
        }
        cluster_items[cid].append(item_dict)
        if embedding is not None:
            # pgvector returns embeddings as a list or numpy-compatible; ensure list[float].
            embeddings[item_id] = list(embedding) if not isinstance(embedding, list) else embedding

    # -----------------------------------------------------------------------
    # 3. Run synthesis concurrently, bounded by semaphore.
    # -----------------------------------------------------------------------
    client = _get_client()
    sem = asyncio.Semaphore(_CONCURRENCY)

    async def one_theme(cluster_row: ClusterORM) -> tuple[dict | None, list[str]]:
        cid = cluster_row.cluster_id
        items = cluster_items.get(cid, [])
        if not items:
            logger.info("synthesiser: cluster %s has no items, skipping", cid)
            return None, []
        theme = {
            "label": cluster_row.label,
            "category": cluster_row.category,
            "sentiment": cluster_row.sentiment,
            "summary": cluster_row.summary,
            "item_count": cluster_row.item_count,
        }
        async with sem:
            try:
                report, errs = await _synthesise_theme(client, cid, theme, items, embeddings)
                return report, errs
            except Exception as exc:
                logger.exception("synthesiser: unexpected error for cluster %s", cid)
                return None, [f"synthesiser:{cid}: {exc}"]

    results = await asyncio.gather(*(one_theme(c) for c in cluster_rows))

    reports: list[dict] = []
    total_hallucinated = 0
    total_partial = 0
    total_findings = 0

    for report, errs in results:
        errors.extend(errs)
        if report is None:
            continue
        reports.append(report)
        # Aggregate corpus-wide groundedness from the raw per-theme counts.
        total_hallucinated += report.pop("_hallucinated_findings", 0)
        total_partial += report.pop("_partial_fabrications", 0)
        total_findings += report.pop("_total_findings", 0)
        report.pop("hallucination_rate", None)

    # -----------------------------------------------------------------------
    # 4. Persist each report (async, sequential to avoid connection pool pressure).
    # -----------------------------------------------------------------------
    for report in reports:
        try:
            await _persist_report(report)
        except Exception as exc:
            logger.exception("synthesiser: failed to persist report for cluster %s", report["cluster_id"])
            errors.append(f"synthesiser:persist:{report['cluster_id']}: {exc}")

    # Groundedness metric: any finding that cited an unshown id, whether dropped entirely
    # or kept after stripping the fabricated id. total_findings is the pre-validation count.
    ungrounded = total_hallucinated + total_partial
    hallucination_rate = ungrounded / total_findings if total_findings else 0.0
    logger.info(
        "synthesiser: %d themes processed, %d reports persisted, %d errors; "
        "ungrounded citations %d/%d findings (dropped=%d, partial=%d, rate=%.3f)",
        len(cluster_rows), len(reports), len(errors),
        ungrounded, total_findings, total_hallucinated, total_partial, hallucination_rate,
    )

    return {
        "reports": reports,
        "errors": errors,
        "synthesis_stats": {
            "themes": len(cluster_rows),
            "reports": len(reports),
            "total_findings": total_findings,
            "dropped_findings": total_hallucinated,
            "partial_fabrications": total_partial,
            "hallucination_rate": hallucination_rate,
        },
    }
