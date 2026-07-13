"""numeric_guard.py — deterministic, zero-LLM check for numeric faithfulness.

For every finding claim and every recommended_action text in the golden fixtures,
extract all numeric/date/duration tokens and verify each one is supported by either:
  (a) the cited review text (samples.json for that finding's evidence_item_ids), or
  (b) the aggregate stats for the theme (stats.json).

A token is a VIOLATION if it appears in neither source.

Run from backend/:
    python -m eval.numeric_guard [--golden-dir eval/golden]

Why deterministic first: this is free (no LLM), fast (<1s), and catches an entire class
of hallucination (invented numbers) with zero cost.  Run it in CI on every PR.
"""
from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

# ---------------------------------------------------------------------------
# Token extraction
# ---------------------------------------------------------------------------

# Matches the numeric/temporal token types that are easy to hallucinate:
#   integers, decimals, 4-digit years, "N days/weeks/months/years", ranges
#   like "2021-2026", "N-star" / "N★", percentages.
# Order matters: put longer/more-specific patterns first so "2021-2026" is
# caught as a range before "2021" is caught as a bare year.
_TOKEN_PATTERNS = [
    # Year range:  "2021-2026", "2020–2025"
    re.compile(r"\b((?:19|20)\d{2})[–\-]((?:19|20)\d{2})\b"),
    # Duration phrases: "2 days", "3 weeks", "1 month", "5+ years"
    re.compile(r"\b\d+\+?\s*(?:days?|weeks?|months?|years?)\b", re.IGNORECASE),
    # "within 2 days", "over 3 weeks" — same pattern catches this via preceding text
    # N-star rating reference: "1-star", "5★", "3 stars"
    re.compile(r"\b\d+\s*[-–]?\s*(?:star[s]?|★)\b", re.IGNORECASE),
    # Percentage: "45%", "12.5%"
    re.compile(r"\b\d+(?:\.\d+)?\s*%"),
    # 4-digit years
    re.compile(r"\b(19|20)\d{2}\b"),
    # Plain integers and decimals (must come AFTER more specific patterns)
    re.compile(r"\b\d+(?:[,_]\d{3})*(?:\.\d+)?\b"),
]


def _extract_tokens(text: str) -> list[str]:
    """Extract all numeric/date/duration tokens from text, deduplicated, longest first."""
    found: dict[int, str] = {}  # start_pos -> token string
    for pat in _TOKEN_PATTERNS:
        for m in pat.finditer(text):
            start = m.start()
            tok = m.group(0).strip()
            # Keep the longest match at any given start position.
            if start not in found or len(tok) > len(found[start]):
                found[start] = tok
    return list(found.values())


# ---------------------------------------------------------------------------
# Token normalisation + support check
# ---------------------------------------------------------------------------

def _normalize(token: str) -> str:
    """Strip cosmetic noise so "1,000" matches "1000", "5★" matches "5 star", etc."""
    t = token.lower()
    t = t.replace(",", "").replace("_", "")
    t = re.sub(r"[★☆]", " star", t)
    t = re.sub(r"\s+", " ", t).strip()
    # Ordinal suffixes: "3rd" -> "3"
    t = re.sub(r"(\d+)(?:st|nd|rd|th)\b", r"\1", t)
    return t


def _token_in_text(token: str, text: str) -> bool:
    """Case-insensitive substring search after normalisation."""
    return _normalize(token) in _normalize(text)


# A "N star"/"N stars" token where N is 1-5 is a reference to a point on the
# 5-star rating scale, not a quantitative claim to verify. The scale inherently
# exists, so these are always supported — otherwise "25 of 29 are 1-star reviews"
# false-positives on the label even though the count (25) is checked separately.
_STAR_LABEL = re.compile(r"^([1-5])\s*[-–]?\s*stars?$", re.IGNORECASE)


def _is_star_scale_reference(token: str) -> bool:
    return bool(_STAR_LABEL.match(_normalize(token)))


def _stat_leaf_values(stats: dict) -> tuple[set[str], list[str]]:
    """Split stats leaves into exact-match scalars (counts) and free-text values (dates).

    Counts (item_count, star_distribution values, country_breakdown values,
    last_30/prior_30) must match a token EXACTLY — otherwise "4" spuriously matches
    inside "49". Date strings (date_range min/max ISO stamps) are matched by substring
    so a year token like "2026" is supported by "2026-03-22T...".
    """
    exact: set[str] = set()
    text_vals: list[str] = []

    def walk(node, in_dates: bool) -> None:
        if isinstance(node, bool):
            return
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, in_dates or k == "date_range")
        elif isinstance(node, list):
            for v in node:
                walk(v, in_dates)
        elif isinstance(node, (int, float)):
            exact.add(_normalize(str(node)))
        elif isinstance(node, str):
            if in_dates:
                text_vals.append(node)
            else:
                exact.add(_normalize(node))

    walk(stats, False)
    return exact, text_vals


def _token_in_stats(token: str, stats: dict) -> bool:
    """Check whether a token is supported by aggregate stats for the theme.

    A star-scale reference ("N star", N in 1-5) is always allowed. Otherwise the
    token must EXACTLY equal a scalar stat value (count) or be a substring of a
    date-range string (so a year matches an ISO timestamp). Exact match on counts
    closes the "4 matches inside 49" false-negative hole.
    """
    if _is_star_scale_reference(token):
        return True
    exact, date_texts = _stat_leaf_values(stats)
    norm = _normalize(token)
    if norm in exact:
        return True
    return any(_token_in_text(token, dt) for dt in date_texts)


# ---------------------------------------------------------------------------
# Violation record
# ---------------------------------------------------------------------------

@dataclass
class Violation:
    cluster_id: str
    claim: str                              # or action text
    token: str
    where: Literal["claim", "action"]
    evidence_ids: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Main guard function
# ---------------------------------------------------------------------------

def run_numeric_guard(
    golden_dir: Path,
) -> tuple[list[Violation], dict]:
    """Run the guard against frozen golden fixtures.  Returns (violations, summary).

    For FINDINGS: tokens are checked against cited evidence text + theme stats.
    For ACTIONS: tokens are checked against ALL sample text for the theme + theme stats,
    because recommended_actions aren't tied to specific evidence ids.
    """
    findings_path = golden_dir / "findings.json"
    samples_path = golden_dir / "samples.json"
    stats_path = golden_dir / "stats.json"

    if not all(p.exists() for p in [findings_path, samples_path, stats_path]):
        print("ERROR: golden fixtures missing — run eval.freeze_golden first.", file=sys.stderr)
        sys.exit(1)

    golden = json.loads(findings_path.read_text())
    findings: list[dict] = golden["findings"]
    actions: list[dict] = golden["recommended_actions"]

    samples: dict[str, list[dict]] = json.loads(samples_path.read_text())
    stats: dict[str, dict] = json.loads(stats_path.read_text())

    # Build per-cluster text lookup: {cluster_id: {item_id: full_text}}
    cluster_texts: dict[str, dict[str, str]] = {}
    for cid, items in samples.items():
        cluster_texts[cid] = {it["id"]: it.get("text", "") for it in items}

    violations: list[Violation] = []

    # --- FINDINGS ---
    for finding in findings:
        cid = finding["cluster_id"]
        claim = finding.get("claim", "")
        evidence_ids = finding.get("evidence_item_ids", [])

        # Gather cited review texts (only the items the judge was shown).
        cited_texts: list[str] = []
        id_map = cluster_texts.get(cid, {})
        for eid in evidence_ids:
            if eid in id_map:
                cited_texts.append(id_map[eid])

        theme_stats = stats.get(cid, {})
        tokens = _extract_tokens(claim)

        for tok in tokens:
            supported = (
                any(_token_in_text(tok, txt) for txt in cited_texts)
                or _token_in_stats(tok, theme_stats)
            )
            if not supported:
                violations.append(Violation(
                    cluster_id=cid,
                    claim=claim,
                    token=tok,
                    where="claim",
                    evidence_ids=evidence_ids,
                ))

    # --- ACTIONS ---
    # Actions aren't tied to specific evidence ids, so we check against the full
    # theme sample text.  This is necessarily looser than finding-level checking.
    for act in actions:
        cid = act["cluster_id"]
        action_text = act.get("action", "")

        all_texts_for_theme = list(cluster_texts.get(cid, {}).values())
        theme_stats = stats.get(cid, {})
        tokens = _extract_tokens(action_text)

        for tok in tokens:
            supported = (
                any(_token_in_text(tok, txt) for txt in all_texts_for_theme)
                or _token_in_stats(tok, theme_stats)
            )
            if not supported:
                violations.append(Violation(
                    cluster_id=cid,
                    claim=action_text,
                    token=tok,
                    where="action",
                    evidence_ids=[],
                ))

    total_claims = len(findings) + len(actions)
    summary = {
        "total_findings_checked": len(findings),
        "total_actions_checked": len(actions),
        "total_tokens_with_violations": len(violations),
        "unique_clusters_with_violations": len({v.cluster_id for v in violations}),
    }

    return violations, summary


def print_report(violations: list[Violation], summary: dict) -> None:
    print("=" * 72)
    print("NUMERIC GUARD REPORT")
    print("=" * 72)
    print(f"Findings checked : {summary['total_findings_checked']}")
    print(f"Actions checked  : {summary['total_actions_checked']}")
    print(f"Violations found : {summary['total_tokens_with_violations']}")
    print(f"Clusters affected: {summary['unique_clusters_with_violations']}")
    print()

    if not violations:
        print("NO VIOLATIONS — all numeric tokens are grounded in cited text or stats.")
        return

    for v in violations:
        print(f"[{v.where.upper()}] cluster={v.cluster_id}")
        print(f"  token    : {v.token!r}")
        claim_snip = v.claim[:120].replace("\n", " ")
        print(f"  text     : {claim_snip!r}")
        if v.evidence_ids:
            print(f"  evidence : {v.evidence_ids}")
        print()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Numeric guard — no LLM, run free")
    parser.add_argument(
        "--golden-dir",
        default=str(Path(__file__).parent / "golden"),
        help="Path to golden fixtures directory (default: eval/golden)",
    )
    args = parser.parse_args()

    golden_dir = Path(args.golden_dir)
    violations, summary = run_numeric_guard(golden_dir)
    print_report(violations, summary)

    # Exit 1 if violations exist so CI can gate on it.
    sys.exit(1 if violations else 0)
