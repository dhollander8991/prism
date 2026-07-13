"""run_eval.py — master eval scorecard.

Runs numeric_guard (free, always) and optionally faithfulness (LLM, ~$0.20).
Writes eval/results/<timestamp>.json and prints a readable table.

Usage from backend/:
    python -m eval.run_eval                        # full eval (LLM + numeric)
    python -m eval.run_eval --numeric-only         # free/offline run
    python -m eval.run_eval --timestamp 20260712T120000   # pin timestamp (for tests)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Path constants
# ---------------------------------------------------------------------------

_BACKEND_DIR = Path(__file__).parent.parent
_EVAL_DIR = _BACKEND_DIR / "eval"
_GOLDEN_DIR = _EVAL_DIR / "golden"
_RESULTS_DIR = _EVAL_DIR / "results"
_THRESHOLDS_PATH = _EVAL_DIR / "thresholds.yaml"


# ---------------------------------------------------------------------------
# Threshold loading
# ---------------------------------------------------------------------------

def _load_thresholds() -> dict:
    """Load thresholds.yaml — fallback to defaults if pyyaml absent or file missing."""
    defaults = {
        "faithfulness_rate": 0.90,
        "hallucinated_citation_rate": 0.02,
        "numeric_guard_violations": 0,
    }
    if not _THRESHOLDS_PATH.exists():
        return defaults
    try:
        import yaml  # optional dep; only needed for run_eval
        raw = yaml.safe_load(_THRESHOLDS_PATH.read_text()) or {}
        return {**defaults, **raw}
    except ImportError:
        # pyyaml not installed — parse the simple YAML manually.
        result = dict(defaults)
        for line in _THRESHOLDS_PATH.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if ":" in line:
                k, _, v = line.partition(":")
                k = k.strip()
                v = v.strip()
                if k in result:
                    try:
                        result[k] = float(v)
                    except ValueError:
                        pass
        return result


# ---------------------------------------------------------------------------
# Hallucinated citation rate from golden findings
# ---------------------------------------------------------------------------

def _compute_hallucinated_citation_rate(golden_dir: Path) -> tuple[float, int, int]:
    """Findings whose evidence_item_ids include an id not in the theme's golden sample.

    The synthesiser already gates these at runtime; this re-checks the PERSISTED findings
    to measure the gate's effectiveness on the golden set.

    Returns (rate, hallucinated_count, total_with_evidence_count).
    """
    findings_path = golden_dir / "findings.json"
    samples_path = golden_dir / "samples.json"

    if not findings_path.exists() or not samples_path.exists():
        return 0.0, 0, 0

    golden = json.loads(findings_path.read_text())
    findings: list[dict] = golden["findings"]
    samples: dict[str, list[dict]] = json.loads(samples_path.read_text())

    cluster_sample_ids: dict[str, set[str]] = {
        cid: {it["id"] for it in items}
        for cid, items in samples.items()
    }

    hallucinated = 0
    total_with_evidence = 0

    for f in findings:
        cid = f["cluster_id"]
        evidence_ids = f.get("evidence_item_ids", [])
        if not evidence_ids:
            continue
        total_with_evidence += 1
        sample_ids = cluster_sample_ids.get(cid, set())
        if any(eid not in sample_ids for eid in evidence_ids):
            hallucinated += 1

    rate = hallucinated / total_with_evidence if total_with_evidence else 0.0
    return rate, hallucinated, total_with_evidence


# ---------------------------------------------------------------------------
# Churn + priority distribution from live insight_reports
# ---------------------------------------------------------------------------

async def _load_report_distributions() -> tuple[dict, dict]:
    """Query live insight_reports for current churn/priority distribution.

    We read the live table (not golden) because these reflect the current synthesiser
    run that's being evaluated.
    """
    if "DATABASE_URL" not in os.environ:
        os.environ["DATABASE_URL"] = "postgresql://prism:prism@localhost:5433/prism"

    try:
        from db.database import AsyncSessionFactory
        from db.models import InsightReportORM
        from sqlalchemy import select

        async with AsyncSessionFactory() as db:
            rows = (await db.execute(select(InsightReportORM))).scalars().all()

        churn_dist: dict[str, int] = {}
        prio_dist: dict[str, int] = {}
        for r in rows:
            churn_dist[r.churn_risk or "none"] = churn_dist.get(r.churn_risk or "none", 0) + 1
            prio_dist[r.priority] = prio_dist.get(r.priority, 0) + 1

        return churn_dist, prio_dist
    except Exception as exc:
        print(f"  WARNING: could not load report distributions from DB: {exc}", file=sys.stderr)
        return {}, {}


# ---------------------------------------------------------------------------
# Scorecard table printer
# ---------------------------------------------------------------------------

def _print_scorecard(result: dict, thresholds: dict) -> list[str]:
    """Print a readable scorecard table.  Returns list of threshold breach messages."""
    print()
    print("=" * 72)
    print("PRISM SYNTHESISER EVAL SCORECARD")
    print("=" * 72)
    print(f"  Timestamp   : {result['timestamp']}")
    print(f"  Golden dir  : {result['golden_dir']}")
    print()

    def _row(name: str, value: object, threshold: float | None = None, lower_is_better: bool = False) -> str | None:
        """Print one metric row.  Returns breach message if threshold exceeded."""
        if threshold is None:
            print(f"  {name:<35} {value}")
            return None
        numeric = float(value) if isinstance(value, (int, float)) else None
        if numeric is None:
            print(f"  {name:<35} {value}")
            return None
        if lower_is_better:
            ok = numeric <= threshold
        else:
            ok = numeric >= threshold
        marker = "OK" if ok else "FAIL"
        thresh_str = f"(threshold: {'<=' if lower_is_better else '>='}{threshold})"
        print(f"  {name:<35} {numeric:<10.4f} {thresh_str}  [{marker}]")
        if not ok:
            return f"{name}: {numeric:.4f} {'>' if lower_is_better else '<'} threshold {threshold}"
        return None

    breaches: list[str] = []

    # Faithfulness
    fd = result.get("faithfulness", {})
    if fd:
        b = _row(
            "faithfulness_rate",
            fd.get("faithfulness_rate", 0.0),
            thresholds["faithfulness_rate"],
        )
        if b:
            breaches.append(b)
        print(f"    supported        : {fd.get('supported', 0)}")
        print(f"    partially        : {fd.get('partially_supported', 0)}")
        print(f"    unsupported      : {fd.get('unsupported', 0)}")
        print(f"    errors           : {fd.get('errors', 0)}")
        print(f"    cost_usd         : ${fd.get('cost_usd', 0):.4f}")
        print(f"    latency_seconds  : {fd.get('latency_seconds', 0):.1f}s")
    else:
        print(f"  {'faithfulness_rate':<35} SKIPPED (--numeric-only or no API key)")

    print()

    # Hallucinated citation rate
    hcr = result.get("hallucinated_citation_rate", 0.0)
    b = _row(
        "hallucinated_citation_rate",
        hcr,
        thresholds["hallucinated_citation_rate"],
        lower_is_better=True,
    )
    if b:
        breaches.append(b)
    print(f"    hallucinated_count : {result.get('hallucinated_citation_count', 0)}")
    print(f"    total_with_evidence: {result.get('total_with_evidence', 0)}")

    print()

    # Numeric guard
    ng = result.get("numeric_guard", {})
    ng_count = ng.get("total_tokens_with_violations", 0)
    b = _row(
        "numeric_guard_violations",
        ng_count,
        thresholds["numeric_guard_violations"],
        lower_is_better=True,
    )
    if b:
        breaches.append(b)
    print(f"    findings_checked : {ng.get('total_findings_checked', 0)}")
    print(f"    actions_checked  : {ng.get('total_actions_checked', 0)}")
    print(f"    clusters_affected: {ng.get('unique_clusters_with_violations', 0)}")

    print()

    # Distributions (informational, no threshold)
    _row("churn_distribution", json.dumps(result.get("churn_distribution", {})))
    _row("priority_distribution", json.dumps(result.get("priority_distribution", {})))

    print()

    if breaches:
        print(f"THRESHOLD BREACHES ({len(breaches)}):")
        for b in breaches:
            print(f"  FAIL: {b}")
    else:
        print("ALL THRESHOLDS PASSED.")

    print("=" * 72)
    return breaches


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main(args: argparse.Namespace) -> int:
    """Returns exit code: 0 = pass, 1 = threshold breach, 2 = skipped."""
    golden_dir = Path(args.golden_dir)
    _RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    thresholds = _load_thresholds()

    timestamp = args.timestamp or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    result: dict = {
        "timestamp": timestamp,
        "golden_dir": str(golden_dir),
    }

    # --- numeric guard (always runs, free) ---
    from eval.numeric_guard import run_numeric_guard  # noqa: E402
    violations, ng_summary = run_numeric_guard(golden_dir)
    result["numeric_guard"] = ng_summary
    result["numeric_violations"] = [
        {
            "cluster_id": v.cluster_id,
            "token": v.token,
            "where": v.where,
            "claim": v.claim[:200],
        }
        for v in violations
    ]

    # --- hallucinated citation rate (free, uses golden only) ---
    hcr, hall_count, total_ev = _compute_hallucinated_citation_rate(golden_dir)
    result["hallucinated_citation_rate"] = hcr
    result["hallucinated_citation_count"] = hall_count
    result["total_with_evidence"] = total_ev

    # --- distributions from live DB (best effort; skip in offline numeric-only mode) ---
    if args.numeric_only:
        churn_dist, prio_dist = {}, {}
    else:
        churn_dist, prio_dist = await _load_report_distributions()
    result["churn_distribution"] = churn_dist
    result["priority_distribution"] = prio_dist

    # --- faithfulness (LLM, optional) ---
    skipped_llm = False
    if args.numeric_only:
        print("  [--numeric-only] Skipping LLM faithfulness eval.")
        skipped_llm = True
    elif not os.environ.get("ANTHROPIC_API_KEY"):
        print("  SKIPPED: no ANTHROPIC_API_KEY — faithfulness eval requires it.")
        print("  Set the secret to run the full eval.")
        skipped_llm = True
    else:
        from eval.faithfulness import run_faithfulness  # noqa: E402
        faith_results, faith_summary = await run_faithfulness(
            golden_dir, concurrency=args.concurrency
        )
        result["faithfulness"] = faith_summary
        result["faithfulness_details"] = [
            {
                "cluster_id": r.cluster_id,
                "claim": r.claim[:200],
                "verdict": r.verdict,
                "unsupported_details": r.unsupported_details,
                "confidence": r.confidence,
            }
            for r in faith_results
        ]

    # --- write result JSON ---
    out_path = _RESULTS_DIR / f"{timestamp}.json"
    out_path.write_text(json.dumps(result, indent=2, default=str))
    print(f"\nResults written to {out_path}")

    # --- scorecard ---
    breaches = _print_scorecard(result, thresholds)

    if skipped_llm and not os.environ.get("ANTHROPIC_API_KEY"):
        # No key: the faithfulness gate is not evaluated (graceful skip). The free
        # numeric_guard + hallucinated-citation gates still ran — fail CI if they
        # breached, otherwise exit 0 (clean). A clean keyless run must be green, or
        # every keyless CI build is permanently red.
        print("\nskipped: faithfulness gate not evaluated (no ANTHROPIC_API_KEY).")
        return 1 if breaches else 0

    return 1 if breaches else 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="PRISM synthesiser eval scorecard")
    parser.add_argument("--golden-dir", default=str(_EVAL_DIR / "golden"))
    parser.add_argument("--numeric-only", action="store_true", help="Skip LLM faithfulness (free run)")
    parser.add_argument("--concurrency", type=int, default=5, help="Parallel judge calls")
    parser.add_argument("--timestamp", default=None, help="Pin timestamp for deterministic filenames")
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    exit_code = asyncio.run(main(args))
    sys.exit(exit_code)
