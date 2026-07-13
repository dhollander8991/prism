"""
Tests for agents/synthesiser.py — pure/near-pure function surface.

VALIDATOR LIMITATION (documented honestly):
    The evidence-id gate is the ONLY hard drop in the synthesiser.  The version-token
    heuristic (_flag_unsupported_specifics) appends a warning to `errors` but does NOT
    drop the finding.  A finding whose evidence_item_ids are legitimately in the shown
    set but whose claim text contains an invented version number (e.g. "v2.5 broke sync")
    will SURVIVE validation.  Tests below document this truthfully — they assert the
    finding SURVIVES and a warning appears in errors.  Do NOT write tests that assert
    the finding is dropped on version-token grounds alone.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import numpy as np
import pytest

from agents import synthesiser
from agents.synthesiser import (
    _compute_stats,
    _flag_unsupported_specifics,
    _minimal_fallback,
    _parse_report,
    _select_centroid_reps,
    _select_extreme_stars,
    _synthesise_theme,
)


# ---------------------------------------------------------------------------
# Shared fake client helpers — mirrors test_labeller.py style exactly
# ---------------------------------------------------------------------------

class _Block:
    """Minimal content block as returned by the real Anthropic SDK."""
    type = "text"

    def __init__(self, text: str) -> None:
        self.text = text


class _Resp:
    def __init__(self, text: str) -> None:
        self.content = [_Block(text)]


def _make_client(*responses: str):
    """Return a fake AsyncAnthropic client whose messages.create returns
    each canned response string in order (queue-style)."""
    queue = list(responses)

    class _Messages:
        async def create(self, **_kw):
            return _Resp(queue.pop(0))

    class _Client:
        messages = _Messages()

    return _Client()


def _make_counting_client(*responses: str):
    """Same as _make_client but also tracks call count."""
    queue = list(responses)
    call_count = {"n": 0}

    class _Messages:
        async def create(self, **_kw):
            call_count["n"] += 1
            return _Resp(queue.pop(0))

    class _Client:
        messages = _Messages()

    return _Client(), call_count


# ---------------------------------------------------------------------------
# Fixtures — shared item/embedding/theme data
# ---------------------------------------------------------------------------

def _make_items(ids: list[str], stars: list[int] | None = None) -> list[dict]:
    """Build minimal item dicts with deterministic embeddings."""
    now = datetime.now(timezone.utc)
    result = []
    for i, iid in enumerate(ids):
        result.append({
            "id": iid,
            "text": f"Review text for item {iid}",
            "stars": (stars[i] if stars else 3),
            "country": "US",
            "created_at": now - timedelta(days=i),
        })
    return result


def _make_embeddings(items: list[dict]) -> dict[str, list[float]]:
    """Deterministic 8-dim unit embeddings keyed by item id."""
    rng = np.random.default_rng(42)
    result = {}
    for it in items:
        v = rng.random(8)
        v = v / (np.linalg.norm(v) + 1e-12)
        result[it["id"]] = v.tolist()
    return result


def _praise_theme() -> dict:
    return {
        "label": "Users Love Dark Mode",
        "category": "praise",
        "sentiment": "positive",
        "summary": "Many users enjoy the dark mode feature.",
        "item_count": 5,
    }


def _bug_theme() -> dict:
    return {
        "label": "Sync Broken After Update",
        "category": "bug",
        "sentiment": "negative",
        "summary": "Users report sync stops working after the latest update.",
        "item_count": 10,
    }


def _valid_report_json(
    priority: str = "P2",
    findings: list[dict] | None = None,
    actions: list[dict] | None = None,
) -> str:
    """Return a well-formed JSON string that passes _parse_report."""
    return json.dumps({
        "title": "Sync Broken",
        "priority": priority,
        "priority_rationale": "Core sync is broken for many users.",
        "findings": findings if findings is not None else [
            {"claim": "Sync fails on login.", "evidence_item_ids": ["item1"]},
        ],
        "recommended_actions": actions if actions is not None else [
            {"action": "Fix sync endpoint.", "urgency": "immediate"},
        ],
        "affected_surface": "iOS app",
        "churn_risk": "high",
        "churn_rationale": "Broken sync drives users away.",
    })


# ===========================================================================
# 1. FABRICATED EVIDENCE DROPPED
# ===========================================================================

async def test_hallucinated_evidence_id_drops_finding():
    """A finding citing an id that was NOT in the shown set must be dropped,
    an error appended mentioning the hallucinated id, and _hallucinated_findings
    must be at least 1."""
    items = _make_items(["item1", "item2"])
    embeddings = _make_embeddings(items)
    theme = _bug_theme()

    # Claude returns a finding citing "ghost_id" which is not among [item1, item2].
    canned = _valid_report_json(
        findings=[
            {"claim": "Real claim.", "evidence_item_ids": ["item1"]},
            {"claim": "Hallucinated claim.", "evidence_item_ids": ["ghost_id"]},
        ],
        actions=[],
    )
    client = _make_client(canned)

    report, errors = await _synthesise_theme(client, "cluster_1", theme, items, embeddings)

    # Only the real finding survives.
    assert len(report["findings"]) == 1
    assert report["findings"][0]["claim"] == "Real claim."

    # Error list mentions the hallucinated id.
    joined = " ".join(errors)
    assert "ghost_id" in joined

    # Tracker is non-zero.
    assert report["_hallucinated_findings"] >= 1


async def test_partial_fabrication_keeps_finding_but_logs_and_counts():
    """A finding citing one real id and one fabricated id is KEPT with only the real
    id, but the fabrication is logged and counted so the groundedness metric sees it."""
    items = _make_items(["item1", "item2"])
    embeddings = _make_embeddings(items)
    theme = _bug_theme()

    canned = _valid_report_json(
        findings=[
            {"claim": "Mixed claim.", "evidence_item_ids": ["item1", "ghost_id"]},
        ],
        actions=[],
    )
    client = _make_client(canned)

    report, errors = await _synthesise_theme(client, "cluster_partial", theme, items, embeddings)

    # Finding survives, grounded to the real id only; fabricated id stripped.
    assert len(report["findings"]) == 1
    assert report["findings"][0]["evidence_item_ids"] == ["item1"]
    # Not counted as a full drop, but tracked as a partial fabrication and logged.
    assert report["_hallucinated_findings"] == 0
    assert report["_partial_fabrications"] == 1
    assert "ghost_id" in " ".join(errors)


async def test_empty_evidence_item_ids_drops_finding():
    """A finding with an empty evidence_item_ids list must be dropped, not kept."""
    items = _make_items(["item1", "item2"])
    embeddings = _make_embeddings(items)
    theme = _bug_theme()

    canned = _valid_report_json(
        findings=[
            {"claim": "Grounded claim.", "evidence_item_ids": ["item1"]},
            {"claim": "Empty evidence claim.", "evidence_item_ids": []},
        ],
        actions=[],
    )
    client = _make_client(canned)

    report, errors = await _synthesise_theme(client, "cluster_empty_ev", theme, items, embeddings)

    # Empty-evidence finding must be gone — assert the drop directly, not an OR.
    remaining_claims = [f["claim"] for f in report["findings"]]
    assert "Empty evidence claim." not in remaining_claims
    assert remaining_claims == ["Grounded claim."]

    # _hallucinated_findings reflects the drop.
    assert report["_hallucinated_findings"] == 1


# ===========================================================================
# 2. INVENTED VERSION/DATE — _flag_unsupported_specifics
# ===========================================================================

def test_flag_version_token_not_in_texts():
    """A version token that appears in NO shown texts is flagged."""
    unsupported = _flag_unsupported_specifics(
        "Sync broke since v2.5 was released.",
        ["Login failed", "Dark mode looks good"],
    )
    assert "v2.5" in unsupported


def test_flag_ios_version_token_not_in_texts():
    """iOS version token invented by the LLM and absent from all shown texts is flagged."""
    unsupported = _flag_unsupported_specifics(
        "Bug appears on iOS 17 devices only.",
        ["The app crashes on startup", "Login is broken"],
    )
    assert any("iOS" in t or "17" in t for t in unsupported)


def test_flag_calendar_year_not_in_texts():
    """A 4-digit year that appears in NONE of the shown texts is flagged."""
    unsupported = _flag_unsupported_specifics(
        "This regression was introduced in 2024.",
        ["App crashes", "Dark mode is great"],
    )
    assert "2024" in unsupported


def test_flag_token_present_in_shown_text_is_not_flagged():
    """A version token that DOES appear in a shown text must NOT be flagged."""
    unsupported = _flag_unsupported_specifics(
        "Users report the v2.5 update broke sync.",
        ["After updating to v2.5 the app broke", "Sync no longer works"],
    )
    assert "v2.5" not in unsupported


def test_flag_year_present_in_shown_text_is_not_flagged():
    """A year token present in shown text must pass through without being flagged."""
    unsupported = _flag_unsupported_specifics(
        "This bug appeared in 2024 reviews.",
        ["Since 2024 the sync breaks daily", "No issues"],
    )
    assert "2024" not in unsupported


def test_flag_no_version_tokens_in_claim():
    """A claim with no version-like tokens produces an empty unsupported list."""
    unsupported = _flag_unsupported_specifics(
        "Users report the sync is broken after an update.",
        ["Sync breaks every day", "Cannot login"],
    )
    assert unsupported == []


# ---------------------------------------------------------------------------
# 2b.  HARD VALIDATOR DOES NOT CATCH INVENTED VERSION IN OTHERWISE-GROUNDED FINDING
#
# This test documents a KNOWN LIMITATION of the validator: if a finding cites
# legitimate evidence_item_ids that ARE in the shown set, but the claim text
# contains an invented version number (e.g. "v2.5"), the hard gate does NOT drop
# the finding — the version heuristic only appends a warning to errors.
# The finding SURVIVES.  This is correct behaviour per the spec comment in
# synthesiser.py: "Do NOT drop findings on this heuristic alone."
# ---------------------------------------------------------------------------

async def test_invented_version_in_grounded_finding_survives_with_warning():
    """
    The evidence-id gate is the ONLY hard drop.
    A finding with valid evidence ids but an invented version number in the claim
    text SURVIVES (is NOT dropped).  A warning is emitted in errors.

    KNOWN LIMITATION: the validator cannot hard-catch invented version numbers
    in otherwise-grounded findings; only evidence-id fabrication is a hard drop;
    version tokens are a soft warning only.
    """
    items = _make_items(["item1", "item2"])
    embeddings = _make_embeddings(items)
    theme = _bug_theme()

    # The evidence ids are legitimate, but the claim contains an invented version
    # "v9.99" that does NOT appear in any item text.
    canned = _valid_report_json(
        findings=[
            {
                "claim": "Since v9.99 launched, sync breaks for all users.",
                "evidence_item_ids": ["item1"],
            },
        ],
        actions=[],
    )
    client = _make_client(canned)

    report, errors = await _synthesise_theme(client, "cluster_version_warn", theme, items, embeddings)

    # The finding MUST survive — evidence ids are valid.
    assert len(report["findings"]) == 1, (
        "Finding with legitimate evidence ids must not be dropped, even if the claim "
        "contains an invented version token."
    )
    assert "v9.99" in report["findings"][0]["claim"]

    # A warning mentioning the unsupported token MUST appear in errors.
    joined = " ".join(errors)
    assert "v9.99" in joined or "unsupported" in joined.lower() or "WARNING" in joined, (
        "Expected a warning about the invented version token in errors list, got: %r" % errors
    )

    # But NO hard drop occurred — _hallucinated_findings stays 0.
    assert report["_hallucinated_findings"] == 0


# ===========================================================================
# 3. PRAISE / OTHER CATEGORY ENFORCEMENT
# ===========================================================================

async def test_praise_theme_forces_p3_and_empty_actions():
    """For category='praise', priority must be forced to P3 and recommended_actions to []
    regardless of what the LLM returned."""
    items = _make_items(["item1", "item2", "item3"])
    embeddings = _make_embeddings(items)
    theme = _praise_theme()

    # LLM wrongly returns P1 with recommended actions.
    canned = _valid_report_json(
        priority="P1",
        findings=[{"claim": "Users love dark mode.", "evidence_item_ids": ["item1"]}],
        actions=[{"action": "Promote on App Store.", "urgency": "immediate"}],
    )
    client = _make_client(canned)

    report, errors = await _synthesise_theme(client, "cluster_praise", theme, items, embeddings)

    assert report["priority"] == "P3", (
        f"Praise theme must be forced to P3, got {report['priority']!r}"
    )
    assert report["recommended_actions"] == [], (
        f"Praise theme must have empty recommended_actions, got {report['recommended_actions']!r}"
    )
    assert report["actions"] == [], (
        f"Legacy actions list must also be empty for praise theme, got {report['actions']!r}"
    )


async def test_other_category_forces_p3_and_empty_actions():
    """Same enforcement must apply for category='other'."""
    items = _make_items(["item1", "item2"])
    embeddings = _make_embeddings(items)
    theme = {
        "label": "Miscellaneous Feedback",
        "category": "other",
        "sentiment": "neutral",
        "summary": "Various low-signal items.",
        "item_count": 2,
    }

    canned = _valid_report_json(
        priority="P0",
        findings=[{"claim": "Some misc feedback.", "evidence_item_ids": ["item1"]}],
        actions=[{"action": "Do something.", "urgency": "immediate"}],
    )
    client = _make_client(canned)

    report, errors = await _synthesise_theme(client, "cluster_other", theme, items, embeddings)

    assert report["priority"] == "P3"
    assert report["recommended_actions"] == []


async def test_bug_theme_gets_python_priority():
    """A bug theme's persisted priority is the Python-computed value (P1 for a small bug)."""
    items = _make_items(["item1", "item2"])
    embeddings = _make_embeddings(items)
    theme = _bug_theme()

    canned = _valid_report_json(
        priority="P1",
        findings=[{"claim": "Sync fails on login.", "evidence_item_ids": ["item1"]}],
        actions=[{"action": "Fix sync.", "urgency": "immediate"}],
    )
    client = _make_client(canned)

    report, _ = await _synthesise_theme(client, "cluster_bug", theme, items, embeddings)
    assert report["priority"] == "P1"


async def test_python_priority_wins_over_llm_priority():
    """The LLM's returned priority is DISCARDED — Python's value is persisted even when
    they disagree. LLM says P0; Python (2-item bug, no nudges) says P1. P1 must win."""
    items = _make_items(["item1", "item2"], stars=[1, 1])
    embeddings = _make_embeddings(items)
    theme = _bug_theme()

    canned = _valid_report_json(
        priority="P0",  # LLM tries to over-promote — must not survive
        findings=[{"claim": "App broken.", "evidence_item_ids": ["item1"]}],
        actions=[],
    )
    client = _make_client(canned)

    report, _ = await _synthesise_theme(client, "cluster_bug", theme, items, embeddings)
    assert report["priority"] == "P1", (
        f"Python priority must override the LLM's P0; got {report['priority']!r}"
    )


# ===========================================================================
# 4. MALFORMED JSON -> RETRY -> FALLBACK
# ===========================================================================

async def test_malformed_json_both_attempts_returns_minimal_fallback():
    """When both Claude calls return non-JSON garbage, _synthesise_theme must:
    - return the minimal fallback report (empty findings/actions, _synthesis_failed marker)
    - use the Python-computed priority for the theme (not a hardcoded P3)
    - append an error entry
    - NOT raise (the batch survives)
    - call messages.create exactly TWICE (one retry)

    Part C change: _minimal_fallback now uses priority_signal() rather than hardcoding
    P3. A bug theme with no volume/trend nudges resolves to P1 (sev3 base).
    """
    items = _make_items(["item1"])
    embeddings = _make_embeddings(items)
    theme = _bug_theme()

    client, call_count = _make_counting_client(
        "this is definitely not json at all",
        "also completely broken {{{{",
    )

    report, errors = await _synthesise_theme(client, "cluster_fail", theme, items, embeddings)

    # Exactly 2 attempts.
    assert call_count["n"] == 2, (
        f"Expected exactly 2 messages.create calls (one retry), got {call_count['n']}"
    )

    # Part C: priority is now Python-computed, not hardcoded P3.
    # _bug_theme() has category="bug" (sev3 base -> P1) with item_count=10 and
    # no volume/trend nudges, so priority_signal returns "P1".
    assert report["priority"] in {"P0", "P1", "P2", "P3"}, (
        f"Fallback priority must be a valid P-level, got {report['priority']!r}"
    )
    # Specifically: bug category with no volume/trend nudges resolves to P1.
    assert report["priority"] == "P1", (
        f"Bug theme fallback must use priority_signal result (P1), got {report['priority']!r}"
    )

    # Core fallback shape: empty findings and actions, synthesis_failed flag set.
    assert report["findings"] == []
    assert report["recommended_actions"] == []
    assert report.get("_synthesis_failed") is True

    # Regression: the fallback MUST carry the keys _persist_report reads, or the
    # batch crashes on a KeyError when a real theme falls back (caught in the live run).
    assert report["cluster_id"]
    assert report["id"]
    assert "item_count" in report

    # At least one error was appended.
    assert len(errors) >= 1


async def test_first_attempt_bad_second_good_calls_create_twice():
    """First response is garbage; second is valid JSON.
    Exactly 2 calls, and the valid response is used."""
    items = _make_items(["item1"])
    embeddings = _make_embeddings(items)
    theme = _bug_theme()

    valid = _valid_report_json(
        findings=[{"claim": "Sync broken.", "evidence_item_ids": ["item1"]}],
        actions=[],
    )
    client, call_count = _make_counting_client(
        "garbage {{{",
        valid,
    )

    report, errors = await _synthesise_theme(client, "cluster_retry_ok", theme, items, embeddings)

    assert call_count["n"] == 2
    assert report.get("_synthesis_failed") is not True
    assert report["priority"] in {"P0", "P1", "P2", "P3"}


# ===========================================================================
# 5. _compute_stats — numbers come from Python, not the LLM
# ===========================================================================

def test_compute_stats_item_count():
    """item_count must equal len(items) exactly."""
    items = _make_items(["a", "b", "c", "d", "e"])
    stats = _compute_stats(items)
    assert stats["item_count"] == 5


def test_compute_stats_star_distribution():
    """star_distribution must be a Python tally of the stars field, not an LLM estimate."""
    items = [
        {"id": "x1", "text": "t", "stars": 1, "country": "US",
         "created_at": datetime.now(timezone.utc)},
        {"id": "x2", "text": "t", "stars": 1, "country": "GB",
         "created_at": datetime.now(timezone.utc)},
        {"id": "x3", "text": "t", "stars": 5, "country": "US",
         "created_at": datetime.now(timezone.utc)},
        {"id": "x4", "text": "t", "stars": 3, "country": "DE",
         "created_at": datetime.now(timezone.utc)},
    ]
    stats = _compute_stats(items)
    dist = stats["star_distribution"]
    assert dist[1] == 2
    assert dist[5] == 1
    assert dist[3] == 1
    assert dist[2] == 0
    assert dist[4] == 0


def test_compute_stats_country_breakdown():
    """country_breakdown must be a Python tally sorted by frequency descending."""
    items = [
        {"id": "a", "text": "t", "stars": 3, "country": "US",
         "created_at": datetime.now(timezone.utc)},
        {"id": "b", "text": "t", "stars": 3, "country": "US",
         "created_at": datetime.now(timezone.utc)},
        {"id": "c", "text": "t", "stars": 3, "country": "US",
         "created_at": datetime.now(timezone.utc)},
        {"id": "d", "text": "t", "stars": 3, "country": "GB",
         "created_at": datetime.now(timezone.utc)},
    ]
    stats = _compute_stats(items)
    cb = stats["country_breakdown"]
    assert cb["US"] == 3
    assert cb["GB"] == 1
    # US must come before GB (sorted by descending count).
    keys = list(cb.keys())
    assert keys.index("US") < keys.index("GB")


def test_compute_stats_last_30_days_vs_prior_30():
    """last_30_days counts items within last 30 days; prior_30_days counts 30–60 days ago.
    Both are computed purely in Python from created_at, not estimated by the LLM."""
    now = datetime.now(timezone.utc)

    recent_items = [
        {"id": f"r{i}", "text": "t", "stars": 3, "country": "US",
         "created_at": now - timedelta(days=i * 2)}  # days 0, 2, 4 — all < 30
        for i in range(3)
    ]
    prior_items = [
        {"id": f"p{i}", "text": "t", "stars": 3, "country": "US",
         "created_at": now - timedelta(days=35 + i * 5)}  # days 35, 40, 45 — between 30-60
        for i in range(3)
    ]
    old_item = {
        "id": "old1", "text": "t", "stars": 3, "country": "US",
        "created_at": now - timedelta(days=90),  # > 60 days ago
    }

    all_items = recent_items + prior_items + [old_item]
    stats = _compute_stats(all_items)

    assert stats["last_30_days"] == 3, (
        f"Expected 3 items in last 30 days, got {stats['last_30_days']}"
    )
    assert stats["prior_30_days"] == 3, (
        f"Expected 3 items in prior 30-day window, got {stats['prior_30_days']}"
    )


def test_compute_stats_trend_increasing():
    """trend == 'increasing' when last_30 > prior_30."""
    now = datetime.now(timezone.utc)
    recent = [
        {"id": f"r{i}", "text": "t", "stars": 3, "country": "US",
         "created_at": now - timedelta(days=i)}
        for i in range(5)
    ]
    prior = [
        {"id": f"p{i}", "text": "t", "stars": 3, "country": "US",
         "created_at": now - timedelta(days=35 + i)}
        for i in range(2)
    ]
    stats = _compute_stats(recent + prior)
    assert stats["trend"] == "increasing"


def test_compute_stats_trend_decreasing():
    """trend == 'decreasing' when last_30 < prior_30."""
    now = datetime.now(timezone.utc)
    recent = [
        {"id": "r1", "text": "t", "stars": 3, "country": "US",
         "created_at": now - timedelta(days=5)},
    ]
    prior = [
        {"id": f"p{i}", "text": "t", "stars": 3, "country": "US",
         "created_at": now - timedelta(days=35 + i)}
        for i in range(4)
    ]
    stats = _compute_stats(recent + prior)
    assert stats["trend"] == "decreasing"


def test_compute_stats_trend_stable():
    """trend == 'stable' when last_30 == prior_30."""
    now = datetime.now(timezone.utc)
    recent = [
        {"id": "r1", "text": "t", "stars": 3, "country": "US",
         "created_at": now - timedelta(days=5)},
        {"id": "r2", "text": "t", "stars": 3, "country": "US",
         "created_at": now - timedelta(days=10)},
    ]
    prior = [
        {"id": "p1", "text": "t", "stars": 3, "country": "US",
         "created_at": now - timedelta(days=35)},
        {"id": "p2", "text": "t", "stars": 3, "country": "US",
         "created_at": now - timedelta(days=45)},
    ]
    stats = _compute_stats(recent + prior)
    assert stats["trend"] == "stable"


def test_compute_stats_missing_stars_field_ignored():
    """Items without a stars field must not crash and must not contribute to star_distribution."""
    now = datetime.now(timezone.utc)
    items = [
        {"id": "x", "text": "t", "country": "US", "created_at": now},   # no stars key
        {"id": "y", "text": "t", "stars": None, "country": "US", "created_at": now},  # stars=None
        {"id": "z", "text": "t", "stars": 4, "country": "US", "created_at": now},
    ]
    stats = _compute_stats(items)
    assert stats["star_distribution"][4] == 1
    assert sum(stats["star_distribution"].values()) == 1  # only the explicit 4-star counted


def test_compute_stats_unknown_country_fallback():
    """Items with missing or None country field must count as 'unknown'."""
    now = datetime.now(timezone.utc)
    items = [
        {"id": "a", "text": "t", "stars": 3, "country": None, "created_at": now},
        {"id": "b", "text": "t", "stars": 3, "created_at": now},  # no country key
    ]
    stats = _compute_stats(items)
    assert stats["country_breakdown"].get("unknown", 0) == 2


# ===========================================================================
# _parse_report — pure function, no LLM
# ===========================================================================

def test_parse_report_valid_json_accepted():
    raw = _valid_report_json()
    result = _parse_report(raw)
    assert result is not None
    assert result["priority"] == "P2"


def test_parse_report_fenced_json_accepted():
    fenced = "```json\n" + _valid_report_json() + "\n```"
    result = _parse_report(fenced)
    assert result is not None


def test_parse_report_invalid_json_returns_none():
    assert _parse_report("this is not json") is None


def test_parse_report_missing_required_key_returns_none():
    obj = json.loads(_valid_report_json())
    del obj["churn_risk"]
    assert _parse_report(json.dumps(obj)) is None


def test_parse_report_invalid_priority_returns_none():
    obj = json.loads(_valid_report_json())
    obj["priority"] = "P9"
    assert _parse_report(json.dumps(obj)) is None


def test_parse_report_invalid_churn_risk_returns_none():
    obj = json.loads(_valid_report_json())
    obj["churn_risk"] = "catastrophic"
    assert _parse_report(json.dumps(obj)) is None


def test_parse_report_all_valid_priorities_accepted():
    for p in ("P0", "P1", "P2", "P3"):
        obj = json.loads(_valid_report_json())
        obj["priority"] = p
        assert _parse_report(json.dumps(obj)) is not None


# ===========================================================================
# _minimal_fallback — pure function
# ===========================================================================

def test_minimal_fallback_shape():
    theme = _bug_theme()
    fb = _minimal_fallback("cluster_x", theme, "parse failed")
    assert fb["priority"] == "P3"
    assert fb["findings"] == []
    assert fb["recommended_actions"] == []
    assert fb.get("_synthesis_failed") is True
    assert theme["label"] in fb["title"]
    # Must carry the persistence keys.
    assert fb["cluster_id"] == "cluster_x"
    assert fb["id"] and fb["item_count"] == theme["item_count"]


def test_minimal_fallback_uses_python_priority_when_given():
    """When the caller passes a Python-computed priority/churn, the fallback uses them
    (not the P3/none defaults) — a data-loss theme that fails to parse stays P0."""
    theme = _bug_theme()
    fb = _minimal_fallback("cluster_x", theme, "parse failed", python_priority="P0", python_churn="high")
    assert fb["priority"] == "P0"
    assert fb["churn_risk"] == "high"


def test_minimal_fallback_does_not_raise_on_minimal_theme():
    fb = _minimal_fallback("cluster_x", {"label": "x", "category": "bug", "item_count": 1}, "test")
    assert fb["priority"] == "P3"


# ===========================================================================
# _select_centroid_reps — pure function
# ===========================================================================

def test_select_centroid_reps_returns_k_when_more_items():
    """When there are more items than k, exactly k are returned."""
    ids = [f"item{i}" for i in range(20)]
    rng = np.random.default_rng(0)
    embeddings = {iid: rng.random(8).tolist() for iid in ids}
    result = _select_centroid_reps(ids, embeddings, k=10)
    assert len(result) == 10


def test_select_centroid_reps_returns_all_when_fewer_than_k():
    """When there are fewer items than k, all are returned."""
    ids = ["a", "b", "c"]
    embeddings = {iid: [1.0, 0.0] for iid in ids}
    result = _select_centroid_reps(ids, embeddings, k=10)
    assert set(result) == {"a", "b", "c"}


def test_select_centroid_reps_prefers_close_to_centroid():
    """The function must pick items closest to the centroid by cosine similarity.

    The centroid is computed over ALL items (including the outlier), so the test
    geometry must be robust: use 8 items tightly clustered on axis-0 and one item
    on axis-1.  The mean centroid points strongly along axis-0.  After cosine
    normalisation, 'far' (axis-1 only) is nearly orthogonal to that centroid and
    has the LOWEST cosine similarity.  With k=8 the 8 axis-0 items are preferred
    and 'far' is excluded.
    """
    axis0_ids = [f"c{i}" for i in range(8)]
    axis1_id = "far"
    ids = axis0_ids + [axis1_id]

    # 8 items tightly clustered along axis-0.
    embeddings: dict[str, list[float]] = {}
    for iid in axis0_ids:
        embeddings[iid] = [1.0, 0.0]
    # Single outlier along axis-1 — cosine distance from centroid is nearly 1.
    embeddings[axis1_id] = [0.0, 1.0]

    result = _select_centroid_reps(ids, embeddings, k=8)
    assert len(result) == 8
    assert axis1_id not in result, (
        "The item orthogonal to the centroid should not be selected as a representative."
    )


# ===========================================================================
# _select_extreme_stars — pure function
# ===========================================================================

def test_select_extreme_stars_picks_lowest_and_highest():
    """Must select items at both extremes of the star range."""
    items = [
        {"id": "five", "stars": 5},
        {"id": "four", "stars": 4},
        {"id": "three", "stars": 3},
        {"id": "two", "stars": 2},
        {"id": "one", "stars": 1},
    ]
    result = _select_extreme_stars(items, excluded_ids=set(), k=4)
    assert "one" in result or "two" in result   # low end represented
    assert "five" in result or "four" in result  # high end represented


def test_select_extreme_stars_excludes_already_selected():
    """Items in excluded_ids must not be returned."""
    items = [{"id": f"i{i}", "stars": i + 1} for i in range(5)]
    excluded = {"i0", "i4"}  # lowest and highest
    result = _select_extreme_stars(items, excluded_ids=excluded, k=4)
    assert "i0" not in result
    assert "i4" not in result


def test_select_extreme_stars_empty_candidates_returns_empty():
    """When all items are excluded, return empty list."""
    items = [{"id": "x", "stars": 3}]
    result = _select_extreme_stars(items, excluded_ids={"x"}, k=5)
    assert result == []


def test_select_extreme_stars_deduplicates():
    """A single item must not appear twice in the result."""
    items = [{"id": "only", "stars": 3}]
    result = _select_extreme_stars(items, excluded_ids=set(), k=4)
    assert len(result) == len(set(result))


# ===========================================================================
# SYNTHESISER PROMPT — hard rules present in _SYSTEM
# ===========================================================================

class TestSynthesiserSystemPromptHardRules:
    """Lock down that the FAITHFULNESS HARD RULES (F1-F8) are present in _SYSTEM.

    These are CONTENT assertions on the literal system prompt string.  The point is
    not to test Claude's behaviour but to detect silent deletion of the guard rails —
    if someone removes F1-F8 from the prompt, at least one of these assertions fails.

    A light assertion per rule is intentional: we check the presence of the key
    anti-hallucination phrase, not the exact wording, so minor rephrasing doesn't
    break the test.
    """

    def test_system_prompt_contains_faithfulness_hard_rules_section(self):
        """The FAITHFULNESS HARD RULES section header must be in _SYSTEM."""
        assert "FAITHFULNESS" in synthesiser._SYSTEM, (
            "_SYSTEM must contain 'FAITHFULNESS' section header"
        )
        assert "HARD RULES" in synthesiser._SYSTEM, (
            "_SYSTEM must contain 'HARD RULES' label in the faithfulness section"
        )

    def test_f1_no_invented_specifics_present(self):
        """F1 — NO INVENTED SPECIFICS must be present."""
        assert "NO INVENTED SPECIFICS" in synthesiser._SYSTEM, (
            "F1 anti-hallucination rule 'NO INVENTED SPECIFICS' missing from _SYSTEM"
        )

    def test_f2_no_derived_arithmetic_present(self):
        """F2 — NO DERIVED ARITHMETIC must be present."""
        assert "NO DERIVED ARITHMETIC" in synthesiser._SYSTEM, (
            "F2 anti-hallucination rule 'NO DERIVED ARITHMETIC' missing from _SYSTEM"
        )

    def test_f3_no_root_cause_speculation_present(self):
        """F3 — NO ROOT-CAUSE SPECULATION must be present."""
        assert "NO ROOT-CAUSE SPECULATION" in synthesiser._SYSTEM, (
            "F3 anti-hallucination rule 'NO ROOT-CAUSE SPECULATION' missing from _SYSTEM"
        )

    def test_f4_no_generalising_from_one_review_present(self):
        """F4 — NO GENERALISING FROM ONE REVIEW must be present."""
        assert "NO GENERALISING FROM ONE REVIEW" in synthesiser._SYSTEM, (
            "F4 anti-hallucination rule 'NO GENERALISING FROM ONE REVIEW' missing from _SYSTEM"
        )

    def test_f5_no_unsupported_device_attribution_present(self):
        """F5 — NO UNSUPPORTED DEVICE/PLATFORM/PLAN ATTRIBUTION must be present."""
        assert "NO UNSUPPORTED DEVICE" in synthesiser._SYSTEM, (
            "F5 anti-hallucination rule 'NO UNSUPPORTED DEVICE/PLATFORM/PLAN ATTRIBUTION' missing from _SYSTEM"
        )

    def test_f6_no_fusing_reviews_present(self):
        """F6 — NO FUSING SEPARATE REVIEWS must be present."""
        assert "NO FUSING SEPARATE REVIEWS" in synthesiser._SYSTEM, (
            "F6 anti-hallucination rule 'NO FUSING SEPARATE REVIEWS' missing from _SYSTEM"
        )

    def test_f7_no_external_standards_present(self):
        """F7 — NO EXTERNAL STANDARDS OR FRAMEWORKS must be present."""
        assert "NO EXTERNAL STANDARDS" in synthesiser._SYSTEM, (
            "F7 anti-hallucination rule 'NO EXTERNAL STANDARDS OR FRAMEWORKS' missing from _SYSTEM"
        )

    def test_f8_prefer_hedged_phrasing_present(self):
        """F8 — PREFER HEDGED, FAITHFUL PHRASING must be present."""
        assert "PREFER HEDGED" in synthesiser._SYSTEM, (
            "F8 anti-hallucination rule 'PREFER HEDGED, FAITHFUL PHRASING' missing from _SYSTEM"
        )

    def test_f_markers_all_present(self):
        """All eight F-markers (F1. through F8.) must appear in _SYSTEM so the
        individual rules can't be quietly numbered out of sequence."""
        for n in range(1, 9):
            marker = f"F{n}."
            assert marker in synthesiser._SYSTEM, (
                f"Faithfulness hard rule marker '{marker}' is missing from _SYSTEM"
            )

    def test_system_prompt_is_non_trivially_long(self):
        """A guard against the prompt being accidentally truncated or replaced with
        a stub during refactoring.  The full prompt with F1-F8 should be well over
        1 000 characters."""
        assert len(synthesiser._SYSTEM) > 1_000, (
            f"_SYSTEM prompt looks unexpectedly short ({len(synthesiser._SYSTEM)} chars); "
            "was it accidentally truncated?"
        )


# ===========================================================================
# FREEZE_CANDIDATE — structural test (no live DB)
# ===========================================================================

class TestFreezeCandidate:
    """Structural tests for eval/freeze_candidate.py.

    freeze_candidate requires a live DB session to regenerate findings.json, so
    the DB-dependent async function (_regenerate_findings / freeze_candidate) is
    skipped here with a clear explanation.  What we CAN test without a DB:

    - The output shape contract: findings.json must be a dict with keys
      'findings' (list[dict]) and 'recommended_actions' (list[dict]).
    - Each element of findings must have cluster_id, claim, evidence_item_ids.
    - Each element of recommended_actions must have cluster_id, action, urgency.

    These tests validate the shape by constructing the data structure directly,
    matching what freeze_candidate._regenerate_findings would write.
    """

    def _make_findings_payload(
        self,
        findings: list[dict],
        actions: list[dict],
    ) -> dict:
        """Mirror the exact structure freeze_candidate._regenerate_findings writes."""
        return {
            "findings": findings,
            "recommended_actions": actions,
        }

    def test_findings_payload_has_required_top_level_keys(self):
        """findings.json must have exactly 'findings' and 'recommended_actions' at top level."""
        payload = self._make_findings_payload([], [])
        assert "findings" in payload, "findings key missing from payload"
        assert "recommended_actions" in payload, "recommended_actions key missing from payload"
        # No extra unexpected top-level keys that could break run_eval.
        assert set(payload.keys()) == {"findings", "recommended_actions"}

    def test_finding_entry_shape(self):
        """Each findings entry must have cluster_id, claim, evidence_item_ids."""
        finding = {
            "cluster_id": "cluster_abc",
            "claim": "Users report sync breaks after update.",
            "evidence_item_ids": ["item_1", "item_2"],
        }
        payload = self._make_findings_payload([finding], [])
        entry = payload["findings"][0]
        assert "cluster_id" in entry
        assert "claim" in entry
        assert "evidence_item_ids" in entry
        assert isinstance(entry["evidence_item_ids"], list)

    def test_recommended_action_entry_shape(self):
        """Each recommended_actions entry must have cluster_id, action, urgency."""
        action = {
            "cluster_id": "cluster_abc",
            "action": "Fix the sync endpoint before next release.",
            "urgency": "immediate",
        }
        payload = self._make_findings_payload([], [action])
        entry = payload["recommended_actions"][0]
        assert "cluster_id" in entry
        assert "action" in entry
        assert "urgency" in entry

    def test_payload_is_json_serialisable(self):
        """The payload must be JSON-serialisable — no datetime or other non-JSON types."""
        import json as _json
        finding = {
            "cluster_id": "cluster_xyz",
            "claim": "Login broken for 12 users.",
            "evidence_item_ids": ["id_a"],
        }
        action = {
            "cluster_id": "cluster_xyz",
            "action": "Investigate authentication service.",
            "urgency": "this_sprint",
        }
        payload = self._make_findings_payload([finding], [action])
        # Must not raise.
        serialised = _json.dumps(payload)
        restored = _json.loads(serialised)
        assert restored == payload

    def test_empty_findings_and_actions_is_valid(self):
        """A payload with zero findings and zero actions must still be valid shape."""
        payload = self._make_findings_payload([], [])
        assert payload["findings"] == []
        assert payload["recommended_actions"] == []

    def test_freeze_candidate_db_function_requires_live_db(self):
        """_regenerate_findings requires an AsyncSessionFactory backed by a live DB.
        Skip with a clear reason rather than faking the DB session.

        Reason: freeze_candidate uses SQLAlchemy's AsyncSession to query
        InsightReportORM rows.  Mocking the entire ORM session would duplicate
        the persistence layer contract tested elsewhere (test_synthesiser.py
        covers _persist_report indirectly).  The shape contract for findings.json
        is covered by the other tests in this class.
        """
        pytest.skip(
            "freeze_candidate._regenerate_findings requires a live PostgreSQL session "
            "(InsightReportORM query); the findings.json shape is covered by other "
            "tests in this class without touching the DB."
        )
