"""faithfulness.py — LLM-as-judge faithfulness evaluation.

For each golden finding: send ONLY the claim + the actual text of its cited reviews
+ the theme's system-verified aggregate stats to claude-sonnet-4-5.  The judge
decides whether the claim is entailed.

Strict isolation: the judge sees no theme label, category, summary, or other findings.
This guards against the LLM using context to rationalise a claim rather than checking
it against evidence.  Stats (item_count, star_distribution, country_breakdown,
date_range, last_30_days, prior_30_days, trend) are factual, pre-computed numbers —
not LLM opinions — so passing them does not break isolation.

Run from backend/:
    python -m eval.faithfulness [--golden-dir eval/golden] [--concurrency 5]

Cost estimate: ~137 calls × ~600 input tokens × $3/1M = ~$0.25
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

_MODEL = "claude-sonnet-4-5"
# Pricing as of 2025: input $3/1M, output $15/1M tokens.
_INPUT_COST_PER_M = 3.0
_OUTPUT_COST_PER_M = 15.0

_JUDGE_SYSTEM = """\
You verify whether a claim is faithfully supported by source review text or \
system-verified aggregate statistics.

You are given:
  1. A CLAIM to evaluate.
  2. SOURCE REVIEWS — the exact review texts cited as evidence.
  3. SYSTEM-VERIFIED STATISTICS — aggregate numbers computed in Python over all \
items in the theme (item_count, star_distribution, country_breakdown, date_range, \
last_30_days, prior_30_days, trend). These are ground truth; treat them as facts.

A claim is SUPPORTED if its assertions are entailed by the SOURCE REVIEWS or by \
the SYSTEM-VERIFIED STATISTICS.

CRITICAL: flag ANY numeric, temporal, or quantitative specific in the claim \
(a number, date, year, quantity, duration, timeframe, count) that does NOT appear \
verbatim in the source review text AND does NOT appear as a literal value in the \
SYSTEM-VERIFIED STATISTICS. Do NOT accept derived arithmetic (e.g. a percentage \
computed from the stats) unless that exact percentage is a literal value in the \
statistics block. A number that appears in the stats supports the claim ONLY if its \
meaning matches the claim's use of it — e.g. a star-distribution count is not evidence \
for a count of cancellations, and a country tally is not evidence for a version number.

Return STRICT JSON only:
{
  "verdict": "supported" | "partially_supported" | "unsupported",
  "unsupported_details": ["list of specific things not supported"],
  "confidence": 0.0 to 1.0
}

- "supported": claim is fully entailed by source reviews or verified stats; \
no invented specifics.
- "partially_supported": core claim is in source/stats but some specifics are not.
- "unsupported": claim contradicts source and stats, or most specifics are invented.

Return ONLY the JSON object — no preamble, no markdown fences, no text before or after.\
"""

_REQUIRED_KEYS = {"verdict", "unsupported_details", "confidence"}
_VALID_VERDICTS = {"supported", "partially_supported", "unsupported"}


# ---------------------------------------------------------------------------
# Result record
# ---------------------------------------------------------------------------

@dataclass
class FindingResult:
    cluster_id: str
    claim: str
    verdict: str                               # "supported" | "partially_supported" | "unsupported" | "error"
    unsupported_details: list[str] = field(default_factory=list)
    confidence: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    error: str | None = None


# ---------------------------------------------------------------------------
# Parsing — reuses the strip-fence + retry approach from synthesiser
# ---------------------------------------------------------------------------

def _strip_fences(text: str) -> str:
    """Remove markdown code fences — mirrors synthesiser._strip_fences."""
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```(?:json)?", "", t).strip()
        if t.endswith("```"):
            t = t[:-3].strip()
    return t.strip()


def _parse_verdict(text: str) -> dict | None:
    t = _strip_fences(text)
    try:
        obj = json.loads(t)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(obj, dict):
        return None
    if not _REQUIRED_KEYS <= obj.keys():
        return None
    if obj.get("verdict") not in _VALID_VERDICTS:
        return None
    return obj


# ---------------------------------------------------------------------------
# Single-finding judge call
# ---------------------------------------------------------------------------

async def _judge_finding(
    client: Any,
    sem: asyncio.Semaphore,
    finding: dict,
    cited_texts: list[str],
    cluster_stats: dict,
) -> FindingResult:
    """One judge call, with one retry on parse failure.  Never raises.

    cluster_stats is the raw stats dict from stats.json (item_count, star_distribution,
    country_breakdown, date_range, last_30_days, prior_30_days, trend).  It contains
    ONLY factual, Python-computed numbers — no label, summary, category, or sentiment —
    so passing it does not break judge isolation.  When empty (cluster not in stats.json),
    the judge falls back to text-only evaluation for that finding.
    """
    cid = finding["cluster_id"]
    claim = finding.get("claim", "")

    if not cited_texts:
        # No cited text available in golden samples — can't evaluate.
        return FindingResult(
            cluster_id=cid,
            claim=claim,
            verdict="error",
            error="no cited review text available in golden samples",
        )

    sources_block = "\n\n".join(
        f"[Review {i+1}]: {txt}" for i, txt in enumerate(cited_texts)
    )
    # Stats block is placed AFTER the source reviews so the judge reads the primary
    # evidence first.  An empty stats dict becomes "{}", which is harmless — the judge
    # system prompt already handles the no-stats case gracefully.
    stats_block = json.dumps(cluster_stats, indent=2, default=str)
    user_msg = (
        f"CLAIM:\n{claim}\n\n"
        f"SOURCE REVIEWS:\n{sources_block}\n\n"
        f"SYSTEM-VERIFIED STATISTICS (treat as ground truth):\n{stats_block}"
    )

    raw: dict | None = None
    total_input = 0
    total_output = 0

    async with sem:
        for attempt in range(2):
            try:
                resp = await client.messages.create(
                    model=_MODEL,
                    # Raised from 512 — truncation caused ~6.6% parse failures on longer
                    # verdicts that included unsupported_details lists.
                    max_tokens=1024,
                    system=_JUDGE_SYSTEM,
                    messages=[{"role": "user", "content": user_msg}],
                )
                total_input += resp.usage.input_tokens
                total_output += resp.usage.output_tokens
                text = next((b.text for b in resp.content if b.type == "text"), "")
                raw = _parse_verdict(text)
                if raw is not None:
                    break
                logger.warning("faithfulness: parse fail cluster=%s attempt=%d", cid, attempt + 1)
            except Exception as exc:
                logger.warning("faithfulness: API error cluster=%s attempt=%d: %s", cid, attempt + 1, exc)

    if raw is None:
        return FindingResult(
            cluster_id=cid,
            claim=claim,
            verdict="error",
            input_tokens=total_input,
            output_tokens=total_output,
            error="parse failed after retry",
        )

    return FindingResult(
        cluster_id=cid,
        claim=claim,
        verdict=str(raw["verdict"]),
        unsupported_details=list(raw.get("unsupported_details") or []),
        confidence=float(raw.get("confidence") or 0.0),
        input_tokens=total_input,
        output_tokens=total_output,
    )


# ---------------------------------------------------------------------------
# Main entrypoint
# ---------------------------------------------------------------------------

async def run_faithfulness(
    golden_dir: Path,
    concurrency: int = 5,
) -> tuple[list[FindingResult], dict]:
    """Evaluate all golden findings for faithfulness.  Returns (results, summary)."""
    findings_path = golden_dir / "findings.json"
    samples_path = golden_dir / "samples.json"
    stats_path = golden_dir / "stats.json"

    if not findings_path.exists() or not samples_path.exists():
        print("ERROR: golden fixtures missing — run eval.freeze_golden first.", file=sys.stderr)
        sys.exit(1)

    golden = json.loads(findings_path.read_text())
    findings: list[dict] = golden["findings"]
    samples: dict[str, list[dict]] = json.loads(samples_path.read_text())

    # stats.json is optional: if absent (e.g. older golden without it), pass {} for
    # every finding and the judge falls back to text-only evaluation.
    stats_by_cluster: dict[str, dict] = {}
    if stats_path.exists():
        stats_by_cluster = json.loads(stats_path.read_text())

    # Build item_id -> text lookup per cluster.
    cluster_texts: dict[str, dict[str, str]] = {
        cid: {it["id"]: it.get("text", "") for it in items}
        for cid, items in samples.items()
    }

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY not set", file=sys.stderr)
        sys.exit(1)

    from anthropic import AsyncAnthropic  # lazy — same pattern as synthesiser._get_client
    client = AsyncAnthropic(api_key=api_key)
    sem = asyncio.Semaphore(concurrency)

    start = time.monotonic()
    tasks = []
    for f in findings:
        cid = f["cluster_id"]
        evidence_ids = f.get("evidence_item_ids", [])
        id_map = cluster_texts.get(cid, {})
        cited_texts = [id_map[eid] for eid in evidence_ids if eid in id_map]
        # Pass only the raw stats dict (factual counts/dates). If the cluster has
        # no entry in stats.json, pass {} so the judge falls back to text-only.
        cluster_stats = stats_by_cluster.get(cid, {})
        tasks.append(_judge_finding(client, sem, f, cited_texts, cluster_stats))

    results: list[FindingResult] = await asyncio.gather(*tasks)
    elapsed = time.monotonic() - start

    # Compute summary metrics.
    total = len(results)
    supported = sum(1 for r in results if r.verdict == "supported")
    partial = sum(1 for r in results if r.verdict == "partially_supported")
    unsupported = sum(1 for r in results if r.verdict == "unsupported")
    errors = sum(1 for r in results if r.verdict == "error")

    total_input_tokens = sum(r.input_tokens for r in results)
    total_output_tokens = sum(r.output_tokens for r in results)
    cost_usd = (
        total_input_tokens / 1_000_000 * _INPUT_COST_PER_M
        + total_output_tokens / 1_000_000 * _OUTPUT_COST_PER_M
    )

    # faithfulness_rate: only "supported" counts as fully faithful; partially_supported
    # and unsupported both count against. "error" verdicts are UNEVALUATED (API/parse
    # failure), not unsupported — exclude them from the denominator so a flaky judge
    # call doesn't masquerade as a faithfulness failure. Errors are surfaced separately.
    evaluated = total - errors
    faithfulness_rate = supported / evaluated if evaluated else 0.0

    summary = {
        "total": total,
        "evaluated": evaluated,
        "supported": supported,
        "partially_supported": partial,
        "unsupported": unsupported,
        "errors": errors,
        "error_rate": round(errors / total, 4) if total else 0.0,
        "faithfulness_rate": faithfulness_rate,
        "total_input_tokens": total_input_tokens,
        "total_output_tokens": total_output_tokens,
        "cost_usd": round(cost_usd, 4),
        "latency_seconds": round(elapsed, 2),
    }

    return results, summary


def print_faithfulness_report(results: list[FindingResult], summary: dict) -> None:
    print("=" * 72)
    print("FAITHFULNESS REPORT")
    print("=" * 72)
    print(f"Total findings   : {summary['total']}")
    print(f"Supported        : {summary['supported']}")
    print(f"Partially        : {summary['partially_supported']}")
    print(f"Unsupported      : {summary['unsupported']}")
    print(f"Errors           : {summary['errors']}")
    print(f"Faithfulness rate: {summary['faithfulness_rate']:.1%}")
    print(f"Cost             : ${summary['cost_usd']:.4f}")
    print(f"Latency          : {summary['latency_seconds']:.1f}s")
    print()

    non_supported = [r for r in results if r.verdict not in ("supported", "error")]
    if non_supported:
        print(f"NON-SUPPORTED FINDINGS ({len(non_supported)}):")
        print("-" * 72)
        for r in non_supported:
            print(f"  cluster  : {r.cluster_id}")
            print(f"  verdict  : {r.verdict} (confidence={r.confidence:.2f})")
            print(f"  claim    : {r.claim[:120]}")
            if r.unsupported_details:
                for d in r.unsupported_details:
                    print(f"    - {d}")
            print()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="LLM-as-judge faithfulness eval")
    parser.add_argument("--golden-dir", default=str(Path(__file__).parent / "golden"))
    parser.add_argument("--concurrency", type=int, default=5)
    args = parser.parse_args()

    results, summary = asyncio.run(
        run_faithfulness(Path(args.golden_dir), concurrency=args.concurrency)
    )
    print_faithfulness_report(results, summary)
