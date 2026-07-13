"""freeze_candidate.py — write a candidate eval set for before/after comparison.

Copies the FROZEN inputs (themes.json, samples.json, stats.json) from eval/golden/
byte-for-byte into eval/candidate/, then regenerates findings.json from whatever is
currently in the insight_reports table.

This lets you score the tightened synthesiser's new output with:

    python -m eval.run_eval --golden-dir eval/candidate

against the IDENTICAL frozen review text + stats, making before/after faithfulness
rates directly comparable.

Do NOT modify eval/golden/ — the baseline stays frozen.
Do NOT re-run the synthesiser before calling this; freeze_candidate just reads
whatever insight_reports currently contains.

Run from backend/:
    DATABASE_URL=postgresql://prism:prism@localhost:5433/prism python -m eval.freeze_candidate
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# Must set DATABASE_URL before importing db modules.
if "DATABASE_URL" not in os.environ:
    os.environ["DATABASE_URL"] = "postgresql://prism:prism@localhost:5433/prism"

from db.database import AsyncSessionFactory  # noqa: E402
from db.models import InsightReportORM  # noqa: E402
from sqlalchemy import select  # noqa: E402

_EVAL_DIR = Path(__file__).parent
_GOLDEN_DIR = _EVAL_DIR / "golden"
_CANDIDATE_DIR = _EVAL_DIR / "candidate"

# Files that are inputs to the eval — copied verbatim so the comparison holds them
# constant.  findings.json is the OUTPUT and is regenerated from the live DB.
_INPUT_FILES = ("themes.json", "samples.json", "stats.json")


def _json_default(obj: object) -> object:
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Not serialisable: {type(obj)}")


def _copy_inputs() -> None:
    """Copy frozen input files byte-for-byte.  Abort if any source is missing."""
    for name in _INPUT_FILES:
        src = _GOLDEN_DIR / name
        if not src.exists():
            print(
                f"ERROR: {src} not found — run eval.freeze_golden first.",
                file=sys.stderr,
            )
            sys.exit(1)
        dst = _CANDIDATE_DIR / name
        shutil.copy2(src, dst)
        print(f"Copied {name} ({dst.stat().st_size:,} bytes)")


async def _regenerate_findings() -> None:
    """Read current insight_reports and write findings.json in the same shape as golden."""
    async with AsyncSessionFactory() as db:
        report_rows = (await db.execute(select(InsightReportORM))).scalars().all()

    if not report_rows:
        print(
            "WARNING: insight_reports table is empty — findings.json will have no findings.",
            file=sys.stderr,
        )

    findings_data: list[dict] = []
    actions_data: list[dict] = []

    for r in report_rows:
        for f in (r.findings or []):
            findings_data.append({
                "cluster_id": r.cluster_id,
                "claim": f.get("claim", ""),
                "evidence_item_ids": f.get("evidence_item_ids", []),
            })
        for act in (r.recommended_actions or []):
            actions_data.append({
                "cluster_id": r.cluster_id,
                "action": act.get("action", ""),
                "urgency": act.get("urgency", ""),
            })

    combined = {
        "findings": findings_data,
        "recommended_actions": actions_data,
    }

    dst = _CANDIDATE_DIR / "findings.json"
    dst.write_text(json.dumps(combined, indent=2, default=_json_default))
    print(
        f"Wrote findings.json ({dst.stat().st_size:,} bytes) — "
        f"{len(findings_data)} findings, {len(actions_data)} recommended_actions"
    )


async def freeze_candidate() -> None:
    _CANDIDATE_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Candidate dir: {_CANDIDATE_DIR}")
    print()

    # Step 1: copy frozen inputs (these MUST be identical to golden so the
    # comparison scores new output against the same review text and stats).
    print("--- Copying frozen inputs ---")
    _copy_inputs()
    print()

    # Step 2: regenerate findings from whatever insight_reports contains now.
    # This is the ONLY file that changes between the baseline and the candidate.
    print("--- Regenerating findings from live insight_reports ---")
    await _regenerate_findings()
    print()

    print("Done. Run the eval against the candidate set with:")
    print("  python -m eval.run_eval --golden-dir eval/candidate")


if __name__ == "__main__":
    asyncio.run(freeze_candidate())
