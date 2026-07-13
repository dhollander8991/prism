from __future__ import annotations

# HISTORICAL DETECTION NOTICE:
# This module performs retrospective volume-spike analysis over a static scrape of
# historical feedback data (items spanning ~2017–2026). It is NOT a real-time monitor.
# "Spike" here means: a specific past week had abnormally high volume relative to the
# preceding baseline window. No streaming, no cron — the node runs once per pipeline
# invocation over the full corpus currently in the DB.

import logging
from datetime import date, datetime, timedelta, timezone
from statistics import mean
from typing import Any

from sqlalchemy import delete, select

from agents.state import PipelineState
from db.database import AsyncSessionFactory
from db.models import ClusterORM, FeedbackItemORM, ThemeTrendORM

logger = logging.getLogger(__name__)

# Default tuning parameters — all overridable from state["params"].
_DEFAULT_BASELINE_WEEKS: int = 8
_DEFAULT_Z_THRESHOLD: float = 2.5
_DEFAULT_MIN_ABSOLUTE: int = 5

# When the baseline std is exactly 0 (perfectly flat), a true z-score is undefined
# (division by zero). A spike still qualifies if the count is this many times the
# min_absolute floor AND exceeds the (flat) baseline mean. This prevents trivially
# sparse weeks (0→1 count) from triggering alerts on data-poor themes while still
# catching genuine step-changes on flat baselines.
WIDE_MARGIN_FACTOR: int = 3

# When std == 0, we report z using a display floor of 1.0 so the magnitude is
# comparable across themes. This is NOT a true sigma — it is labelled clearly in
# the returned dict and in comments below.
_STD_DISPLAY_FLOOR: float = 1.0


# ---------------------------------------------------------------------------
# Pure statistical functions (no DB, no async, fully unit-testable)
# ---------------------------------------------------------------------------

def weekly_series(dates: list[datetime]) -> list[tuple[date, int]]:
    """Bucket timestamps into contiguous weekly counts keyed by the Monday of each week.

    The span runs from the theme's earliest item-week to its latest item-week, inclusive.
    Weeks with zero items are zero-filled rather than omitted — a gap in feedback is
    signal (possibly a data-pipeline outage or seasonal lull), not missing data.

    Returns ordered oldest-to-newest.
    """
    if not dates:
        return []

    # Monday anchor: subtract weekday() days (Mon=0, Sun=6) to get the week's Monday.
    def _monday(d: datetime) -> date:
        return (d.date() - timedelta(days=d.weekday()))

    week_counts: dict[date, int] = {}
    for d in dates:
        w = _monday(d)
        week_counts[w] = week_counts.get(w, 0) + 1

    earliest = min(week_counts)
    latest = max(week_counts)

    # Walk week-by-week from earliest to latest, zero-filling gaps.
    # A missing week is not dropped — it is a genuine zero that should be part of
    # the baseline (a run of zeros lowers the mean, which can trigger spikes after
    # a sustained quiet period).
    result: list[tuple[date, int]] = []
    current = earliest
    while current <= latest:
        result.append((current, week_counts.get(current, 0)))
        current += timedelta(weeks=1)

    return result


def detect_spike(
    series: list[tuple[date, int]],
    n: int = _DEFAULT_BASELINE_WEEKS,
    threshold: float = _DEFAULT_Z_THRESHOLD,
    min_absolute: int = _DEFAULT_MIN_ABSOLUTE,
) -> dict[str, Any]:
    """Rolling z-score spike detection over a weekly count series.

    For each candidate week w (index >= n), the baseline is STRICTLY the n weeks
    before w: series[w-n : w]. Week w is NEVER included in its own baseline — that
    would be data leakage and is treated as a hard correctness bug.

    Returns a dict with three keys:
      "series"               : the input series as [{"week": date, "count": int}, ...]
      "spike"                : the most significant qualifying week, or None
      "has_sufficient_history": False when len(series) < n+1; caller must
                               distinguish "evaluated, no spike" from "not enough data".

    std == 0 rule (flat baseline):
      A spike requires count >= WIDE_MARGIN_FACTOR * min_absolute AND count > mean.
      Magnitude is reported with z = (count - mean) / _STD_DISPLAY_FLOOR.
      This value is a display magnitude, NOT a true sigma.

    has_sufficient_history == False rule:
      Returned as {"series": [...], "spike": None, "has_sufficient_history": False}.
      spike=None here means "not evaluated", not "evaluated, no spike found".
    """
    series_out = [{"week": w, "count": c} for w, c in series]

    if len(series) < n + 1:
        # Caller must not interpret spike=None as "all clear" when this is False.
        return {"series": series_out, "spike": None, "has_sufficient_history": False}

    best: dict[str, Any] | None = None

    for w_idx in range(n, len(series)):
        week, count = series[w_idx]
        # Baseline: strictly the n weeks before w — never includes w itself.
        baseline_counts = [series[i][1] for i in range(w_idx - n, w_idx)]

        baseline_mean = mean(baseline_counts)

        # population std (statistics.stdev is sample std — use manual calculation).
        baseline_std = (
            sum((c - baseline_mean) ** 2 for c in baseline_counts) / len(baseline_counts)
        ) ** 0.5

        if baseline_std > 0:
            # Normal path: true z-score.
            z = (count - baseline_mean) / baseline_std
            qualifies = z >= threshold and count >= min_absolute
        else:
            # Flat baseline: z is undefined. Use the wide-margin rule to guard against
            # spurious alerts on themes with near-zero activity.
            # The returned z is a DISPLAY magnitude using a std floor of 1.0; it is
            # NOT a true sigma and must not be compared to z-scores from non-flat weeks.
            qualifies = count >= WIDE_MARGIN_FACTOR * min_absolute and count > baseline_mean
            z = (count - baseline_mean) / _STD_DISPLAY_FLOOR  # display magnitude only

        if not qualifies:
            continue

        # Most significant = highest z; ties broken by highest count, then latest week.
        if best is None or (z, count, week) > (best["z"], best["count"], best["week"]):
            best = {
                "week": week,
                "count": count,
                "baseline_mean": baseline_mean,
                "z": z,
            }

    return {"series": series_out, "spike": best, "has_sufficient_history": True}


# ---------------------------------------------------------------------------
# Alerter node (standalone-runnable, reads DB directly like synthesiser_node)
# ---------------------------------------------------------------------------

async def alerter_node(state: PipelineState) -> dict:
    """LangGraph node: historical volume-spike detection across all canonical themes.

    state["clusters"] is the pre-dedupe artefact from the clusterer and is intentionally
    ignored — like synthesiser_node, we read directly from the DB so this node is
    runnable standalone against the current canonical theme set.

    Tuning params read from state["params"] (all optional, fall back to module defaults):
      alert_baseline_weeks  -> n (how many prior weeks form the baseline)
      alert_z_threshold     -> threshold (minimum z-score for a spike)
      alert_min_absolute    -> min_absolute (minimum absolute count to qualify)

    Persistence is idempotent: existing theme_trends rows for a cluster are deleted
    before re-inserting, so re-runs produce the same final state.
    """
    errors: list[str] = list(state.get("errors") or [])
    alerts: list[dict] = []

    params = state.get("params") or {}
    n = int(params.get("alert_baseline_weeks", _DEFAULT_BASELINE_WEEKS))
    threshold = float(params.get("alert_z_threshold", _DEFAULT_Z_THRESHOLD))
    min_absolute = int(params.get("alert_min_absolute", _DEFAULT_MIN_ABSOLUTE))

    # ------------------------------------------------------------------
    # 1. Load all canonical clusters and their feedback item timestamps.
    # ------------------------------------------------------------------
    async with AsyncSessionFactory() as db:
        cluster_rows = (await db.execute(select(ClusterORM))).scalars().all()

    if not cluster_rows:
        logger.warning("alerter: no clusters found — nothing to analyse")
        return {"alerts": alerts, "errors": errors}

    cluster_ids = [c.cluster_id for c in cluster_rows]

    async with AsyncSessionFactory() as db:
        item_rows = (
            await db.execute(
                select(FeedbackItemORM.cluster_id, FeedbackItemORM.created_at).where(
                    FeedbackItemORM.cluster_id.in_(cluster_ids)
                )
            )
        ).all()

    # Group created_at timestamps by cluster_id.
    cluster_dates: dict[str, list[datetime]] = {cid: [] for cid in cluster_ids}
    all_dates: list[datetime] = []
    for cid, created_at in item_rows:
        if cid in cluster_dates and created_at is not None:
            # Normalize to UTC-aware datetime in case DB returns naive.
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
            cluster_dates[cid].append(created_at)
            all_dates.append(created_at)

    # Log the corpus date range for operational visibility.
    if all_dates:
        corpus_min = min(all_dates).date()
        corpus_max = max(all_dates).date()
        logger.info(
            "alerter: corpus spans %s → %s (%d items across %d themes)",
            corpus_min, corpus_max, len(all_dates), len(cluster_ids),
        )

    # ------------------------------------------------------------------
    # 2. For each theme: compute weekly series → detect spike → persist.
    # ------------------------------------------------------------------
    themes_with_history = 0

    for cluster_row in cluster_rows:
        cid = cluster_row.cluster_id
        dates = cluster_dates.get(cid, [])

        try:
            series = weekly_series(dates)
            result = detect_spike(series, n=n, threshold=threshold, min_absolute=min_absolute)
        except Exception as exc:
            logger.exception("alerter: spike detection failed for cluster %s", cid)
            errors.append(f"alerter:{cid}:spike_detection: {exc}")
            continue

        if result["has_sufficient_history"]:
            themes_with_history += 1

        # ------------------------------------------------------------------
        # 3. Persist trends + spike columns in ONE transaction so the trend series
        #    and the spike summary never disagree after a partial failure. Trends are
        #    replace-then-insert (idempotent); spike columns are nulled when no spike.
        # ------------------------------------------------------------------
        spike = result.get("spike")
        try:
            async with AsyncSessionFactory() as db:
                await db.execute(
                    delete(ThemeTrendORM).where(ThemeTrendORM.cluster_id == cid)
                )
                for week, count in series:
                    db.add(ThemeTrendORM(cluster_id=cid, week=week, count=count))

                cluster_obj = (
                    await db.execute(
                        select(ClusterORM).where(ClusterORM.cluster_id == cid)
                    )
                ).scalar_one_or_none()
                if cluster_obj is not None:
                    cluster_obj.has_sufficient_history = result["has_sufficient_history"]
                    if spike is not None:
                        cluster_obj.spike_week = spike["week"]
                        cluster_obj.spike_z = spike["z"]
                        cluster_obj.spike_count = spike["count"]
                        cluster_obj.spike_baseline_mean = spike["baseline_mean"]
                    else:
                        # Null out stale spike columns from a previous run.
                        cluster_obj.spike_week = None
                        cluster_obj.spike_z = None
                        cluster_obj.spike_count = None
                        cluster_obj.spike_baseline_mean = None
                await db.commit()
        except Exception as exc:
            logger.exception("alerter: failed to persist trends/spike for cluster %s", cid)
            errors.append(f"alerter:{cid}:persist: {exc}")
            continue

        # ------------------------------------------------------------------
        # 5. Collect alert for state output.
        # ------------------------------------------------------------------
        if spike is not None:
            alerts.append({
                "cluster_id": cid,
                "week": spike["week"].isoformat(),
                "count": spike["count"],
                "baseline_mean": spike["baseline_mean"],
                "z": spike["z"],
            })

    logger.info(
        "alerter: %d themes analysed, %d with sufficient history (>= %d weeks), %d spikes detected",
        len(cluster_rows), themes_with_history, n + 1, len(alerts),
    )

    return {"alerts": alerts, "errors": errors}
