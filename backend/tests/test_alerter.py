from __future__ import annotations

# Tests for the two pure functions in agents/alerter.py:
#   weekly_series(dates)
#   detect_spike(series, n=8, threshold=2.5, min_absolute=5)
#
# No DB, no network, no mocks needed — these functions have no side-effects.
# alerter_node is DB-bound and is NOT tested here; its test is skipped with
# an explicit reason below.

import math
import pytest
from datetime import date, datetime, timedelta, timezone

from agents.alerter import (
    WIDE_MARGIN_FACTOR,
    _STD_DISPLAY_FLOOR,
    detect_spike,
    weekly_series,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MONDAY_0 = date(2026, 1, 5)  # confirmed Monday


def _monday(offset_weeks: int) -> date:
    """Return the Monday that is offset_weeks after _MONDAY_0."""
    return _MONDAY_0 + timedelta(weeks=offset_weeks)


def _series(counts: list[int], start_week: int = 0) -> list[tuple[date, int]]:
    """Build a series of (Monday, count) tuples from a list of counts."""
    return [(_monday(start_week + i), c) for i, c in enumerate(counts)]


def _datetimes_for_week(monday: date, n: int = 1) -> list[datetime]:
    """Return n UTC datetimes in the week starting on monday (Wednesday noon)."""
    dt = datetime(monday.year, monday.month, monday.day, 12, 0, tzinfo=timezone.utc) + timedelta(days=2)
    return [dt] * n


# ---------------------------------------------------------------------------
# Skipped integration placeholder
# ---------------------------------------------------------------------------

@pytest.mark.skip(reason="alerter_node requires a live Postgres DB and is not a pure-function test.")
async def test_alerter_node_integration():
    """alerter_node is a DB-bound LangGraph node. Run it against a real DB separately."""
    pass


# ---------------------------------------------------------------------------
# Test 1 — injected spike is detected
# ---------------------------------------------------------------------------

def test_injected_spike_detected():
    """A week with count=60 in a ~20-week baseline (std > 0, counts 8-11) is detected."""
    # 20 baseline weeks with mild variation so std > 0
    baseline_pattern = [8, 9, 10, 11, 9, 8, 10, 11, 9, 8, 10, 9, 11, 8, 10, 9, 11, 8, 9, 10]
    spike_week_idx = len(baseline_pattern)  # index 20 in the full series
    spike_count = 60
    spike_date = _monday(spike_week_idx)

    full_counts = baseline_pattern + [spike_count]
    series = _series(full_counts)

    result = detect_spike(series, n=8, threshold=2.5, min_absolute=5)

    assert result["has_sufficient_history"] is True
    spike = result["spike"]
    assert spike is not None, "Expected a spike to be detected"
    assert spike["week"] == spike_date
    assert spike["count"] == spike_count
    assert spike["z"] > 2.5   # well above threshold
    assert spike["baseline_mean"] < spike_count


# ---------------------------------------------------------------------------
# Test 2 — flat series does not spike
# ---------------------------------------------------------------------------

def test_flat_series_no_spike():
    """All counts equal (all 8, length 15) → no spike. std==0 path: count=8 < 3*5=15."""
    # WIDE_MARGIN_FACTOR * min_absolute = 3 * 5 = 15; 8 < 15 so no spike
    series = _series([8] * 15)
    result = detect_spike(series, n=8, threshold=2.5, min_absolute=5)

    assert result["has_sufficient_history"] is True
    assert result["spike"] is None


# ---------------------------------------------------------------------------
# Test 3 — sparse series: lone count=2 does not spike (min_absolute floor)
# ---------------------------------------------------------------------------

def test_sparse_lone_count_no_spike():
    """Mostly-zero baseline, one week with count=2. 2 < min_absolute=5 so no spike."""
    # 9 zeros then a lone week=2 — total 10 weeks, so n=8 is satisfiable
    counts = [0] * 9 + [2]
    series = _series(counts)
    result = detect_spike(series, n=8, threshold=2.5, min_absolute=5)

    assert result["has_sufficient_history"] is True
    # The lone count=2 fails min_absolute=5; no spike
    assert result["spike"] is None, (
        f"Expected no spike (count=2 < min_absolute=5), got spike={result['spike']}"
    )


# ---------------------------------------------------------------------------
# Test 4 — std==0 baseline: no divide-by-zero; wide-margin rule applies
# ---------------------------------------------------------------------------

def test_flat_zero_baseline_spike_above_margin():
    """Flat-zero baseline then week=15 (>= WIDE_MARGIN_FACTOR*min_absolute=15).
    Must be detected with z == (15 - 0) / _STD_DISPLAY_FLOOR == 15.0.
    No ZeroDivisionError should be raised."""
    # 8 zeros (the baseline window), then spike at 15 = 3 * 5 = WIDE_MARGIN_FACTOR * min_absolute
    counts = [0] * 8 + [15]
    series = _series(counts)

    result = detect_spike(series, n=8, threshold=2.5, min_absolute=5)

    assert result["has_sufficient_history"] is True
    spike = result["spike"]
    assert spike is not None, "Expected spike (count=15 >= 3*5 and > mean=0)"
    assert math.isclose(spike["z"], 15.0 / _STD_DISPLAY_FLOOR, rel_tol=1e-9)


def test_flat_zero_baseline_no_spike_below_margin():
    """Flat-zero baseline then week=14 (< 15 = WIDE_MARGIN_FACTOR*min_absolute).
    Must NOT be detected. No ZeroDivisionError."""
    counts = [0] * 8 + [14]
    series = _series(counts)

    result = detect_spike(series, n=8, threshold=2.5, min_absolute=5)

    assert result["has_sufficient_history"] is True
    assert result["spike"] is None, (
        f"Expected no spike (count=14 < 3*5=15), got spike={result['spike']}"
    )


# ---------------------------------------------------------------------------
# Test 5 — insufficient history
# ---------------------------------------------------------------------------

def test_insufficient_history_returns_not_evaluated():
    """A series of length 5 with default n=8 → has_sufficient_history False AND spike None."""
    series = _series([10] * 5)
    result = detect_spike(series, n=8)

    assert result["has_sufficient_history"] is False
    assert result["spike"] is None


def test_exactly_n_plus_1_weeks_is_evaluated():
    """A series of exactly n+1=9 weeks must be evaluated (has_sufficient_history True)."""
    # counts 8+spike: 8 baseline zeros, then a spike at 15 (=3*5)
    counts = [0] * 8 + [15]
    series = _series(counts)
    assert len(series) == 9  # exactly n+1

    result = detect_spike(series, n=8, threshold=2.5, min_absolute=5)

    assert result["has_sufficient_history"] is True
    # The spike at 15 should be detected (flat-zero baseline path)
    assert result["spike"] is not None


# ---------------------------------------------------------------------------
# Test 6 — no baseline leakage (critical correctness guard)
# ---------------------------------------------------------------------------

def test_no_baseline_leakage():
    """The spike week must NOT be included in its own baseline window.

    Fixture: baseline [10,11,9,10,12,9,11,10] (mild std ~1.0) then spike=100.
    If the implementation used series[w-n : w+1] (leakage), the 100 would join the
    baseline, inflating the std so that the reported z drops below detection threshold
    or to a much lower value. Specifically:
      - Correct  (no leakage): baseline=[10,11,9,10,12,9,11,10], mean=10.25, std≈1.0 → z≈90.
      - Leakage  (w+1 window): baseline includes 100, mean≈20.25, std≈28+ → z drops to ~3.
    We assert z is high (>50) — only achievable when 100 is excluded from its baseline.
    We also assert the spike is detected at all, which would fail under extreme leakage.
    """
    baseline = [10, 11, 9, 10, 12, 9, 11, 10]
    spike_count = 100
    series = _series(baseline + [spike_count])

    result = detect_spike(series, n=8, threshold=2.5, min_absolute=5)

    assert result["has_sufficient_history"] is True
    spike = result["spike"]
    assert spike is not None, "Spike should be detected with clean baseline"
    assert spike["count"] == spike_count

    # If leakage occurred (series[w-n:w+1]), the std would explode and z would collapse.
    # Correct pre-w baseline: mean=10.25, std≈1.0 → z ≈ (100-10.25)/1.0 ≈ 89.75
    # Leaky baseline (includes 100): mean≈20.25, std≈28 → z ≈ (100-20.25)/28 ≈ 2.8
    # Threshold is 2.5, so leakage narrowly passes but z should be MUCH higher if correct.
    # Assert z > 50 — this is impossible if 100 inflated the baseline std.
    assert spike["z"] > 50, (
        f"z={spike['z']:.2f} is too low — suggests baseline includes the spike week (leakage). "
        "Correct implementation: baseline = series[w-n : w], NOT series[w-n : w+1]."
    )

    # Cross-check: manually compute what z should be with clean baseline
    expected_mean = sum(baseline) / len(baseline)
    expected_std = (sum((c - expected_mean) ** 2 for c in baseline) / len(baseline)) ** 0.5
    expected_z = (spike_count - expected_mean) / expected_std
    assert math.isclose(spike["z"], expected_z, rel_tol=1e-6), (
        f"z={spike['z']:.6f} != expected {expected_z:.6f} — baseline window is wrong"
    )


# ---------------------------------------------------------------------------
# Test 7 — weekly_series behaviour
# ---------------------------------------------------------------------------

def test_weekly_series_same_iso_week_merged():
    """Two datetimes in the same ISO week → one bucket with count 2."""
    monday = _monday(0)  # 2026-01-05
    # Wednesday and Thursday of the same week
    dt_wed = datetime(monday.year, monday.month, monday.day, 12, 0, tzinfo=timezone.utc) + timedelta(days=2)
    dt_thu = datetime(monday.year, monday.month, monday.day, 12, 0, tzinfo=timezone.utc) + timedelta(days=3)

    result = weekly_series([dt_wed, dt_thu])

    assert len(result) == 1
    assert result[0][0] == monday   # keyed by Monday
    assert result[0][1] == 2


def test_weekly_series_gap_week_zero_filled():
    """A gap week between two active weeks must be zero-filled (count=0), not omitted."""
    week0 = _monday(0)
    week1 = _monday(1)  # gap — no events
    week2 = _monday(2)

    dt_w0 = datetime(week0.year, week0.month, week0.day, 10, 0, tzinfo=timezone.utc)
    dt_w2 = datetime(week2.year, week2.month, week2.day, 10, 0, tzinfo=timezone.utc)

    result = weekly_series([dt_w0, dt_w2])

    assert len(result) == 3, f"Expected 3 weeks (with zero-fill), got {len(result)}: {result}"
    weeks = [r[0] for r in result]
    counts = [r[1] for r in result]
    assert week0 in weeks
    assert week1 in weeks
    assert week2 in weeks
    assert counts[weeks.index(week1)] == 0   # the gap week is zero, not absent


def test_weekly_series_output_is_oldest_to_newest():
    """Output must be ordered oldest → newest regardless of input order."""
    week5 = _monday(5)
    week0 = _monday(0)
    week2 = _monday(2)

    dt5 = datetime(week5.year, week5.month, week5.day, 10, 0, tzinfo=timezone.utc)
    dt0 = datetime(week0.year, week0.month, week0.day, 10, 0, tzinfo=timezone.utc)
    dt2 = datetime(week2.year, week2.month, week2.day, 10, 0, tzinfo=timezone.utc)

    # Deliberately out of order
    result = weekly_series([dt5, dt0, dt2])

    output_dates = [r[0] for r in result]
    assert output_dates == sorted(output_dates), (
        f"Output is not sorted oldest→newest: {output_dates}"
    )
    assert output_dates[0] == week0
    assert output_dates[-1] == week5


def test_weekly_series_empty_input():
    """Empty input → empty output, no exception."""
    result = weekly_series([])
    assert result == []


# ---------------------------------------------------------------------------
# Test 8 — tunability: threshold and min_absolute control detection
# ---------------------------------------------------------------------------

def test_tunability_threshold():
    """Same series that spikes at threshold=2.5 returns no spike at threshold=10.0."""
    # Baseline with std ~1, spike week = baseline_mean + 3*std → z ≈ 3 (> 2.5, < 10)
    baseline = [10, 11, 9, 10, 12, 9, 11, 10]
    # z = (spike - 10.25) / std ≈ 3.0 → spike ≈ 10.25 + 3*std
    # std ≈ sqrt( sum of squared diffs / 8 )
    baseline_mean = sum(baseline) / 8
    baseline_std = (sum((c - baseline_mean) ** 2 for c in baseline) / 8) ** 0.5
    # Target z ≈ 3.5 (above 2.5, below 10): spike = mean + 3.5 * std
    spike_count = int(baseline_mean + 3.5 * baseline_std) + 1
    # Make sure it satisfies min_absolute
    spike_count = max(spike_count, 5)

    series = _series(baseline + [spike_count])

    result_low = detect_spike(series, n=8, threshold=2.5, min_absolute=5)
    result_high = detect_spike(series, n=8, threshold=10.0, min_absolute=5)

    assert result_low["spike"] is not None, (
        f"Expected spike at threshold=2.5 with count={spike_count}"
    )
    assert result_high["spike"] is None, (
        f"Expected no spike at threshold=10.0 with count={spike_count}"
    )


def test_tunability_min_absolute():
    """A week with count=6 qualifies at min_absolute=5 but not at min_absolute=10."""
    # Build a series where baseline is zeros (flat, std==0) and spike count = 6.
    # With min_absolute=5: 6 >= WIDE_MARGIN_FACTOR*5=15? No! Flat-zero path needs 15.
    # So use a non-flat baseline (std > 0) to exercise the z-score path.
    # baseline mean ~2, std ~0.5, spike=6 → z = (6-2)/0.5 = 8 (well above 2.5)
    baseline = [2, 2, 3, 2, 2, 3, 2, 2]  # mean=2.25, mild std
    spike_count = 6

    series = _series(baseline + [spike_count])

    result_min5 = detect_spike(series, n=8, threshold=2.5, min_absolute=5)
    result_min10 = detect_spike(series, n=8, threshold=2.5, min_absolute=10)

    # spike_count=6 >= min_absolute=5 → should spike
    assert result_min5["spike"] is not None, (
        f"Expected spike with count={spike_count} >= min_absolute=5"
    )
    # spike_count=6 < min_absolute=10 → must not spike
    assert result_min10["spike"] is None, (
        f"Expected no spike with count={spike_count} < min_absolute=10"
    )
