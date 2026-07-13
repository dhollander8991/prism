# PRISM Synthesiser Eval

This directory contains the evaluation harness for the Synthesiser agent.

## Why a golden-freeze approach

The Synthesiser calls an LLM (Claude), which is non-deterministic: re-running it
against the same input will produce different wording each time.  Scoring "quality"
against live output is therefore not reproducible — the score changes with every run.

The solution: freeze one fixed evaluation set (the "golden") and always score against
that.  The golden captures both the *inputs* (which reviews Claude was shown) and the
*outputs* (the claims Claude produced).  Downstream metrics are deterministic given
the golden, so the eval score is stable across re-runs.

The golden is frozen once (`eval/freeze_golden.py`).  It must only be regenerated
deliberately (delete the files and rerun), never automatically.

---

## Directory layout

```
eval/
  freeze_golden.py     — one-shot script to snapshot DB state into golden/
  freeze_candidate.py  — copies frozen inputs + regenerates findings from live DB into candidate/
  numeric_guard.py     — deterministic token-level numeric faithfulness check
  faithfulness.py      — LLM-as-judge finding-level faithfulness check
  run_eval.py          — master scorecard (runs both, writes results/)
  thresholds.yaml      — gate values for CI
  README.md            — this file
  golden/
    themes.json        — 22 cluster metadata + member item ids
    samples.json       — per-cluster deterministic item sample (frozen eval input)
    stats.json         — per-cluster aggregate stats (frozen eval input)
    findings.json      — 137 baseline findings + 106 recommended_actions (frozen eval output)
  candidate/           — created by freeze_candidate.py; same inputs as golden, new output
    themes.json        — byte-for-byte copy of golden/themes.json
    samples.json       — byte-for-byte copy of golden/samples.json
    stats.json         — byte-for-byte copy of golden/stats.json
    findings.json      — regenerated from current insight_reports (new synthesiser output)
  results/
    <timestamp>.json   — one file per eval run
```

---

## Running the eval

From `backend/`:

```bash
# Free/offline run: numeric guard only (no API key, no cost)
python -m eval.run_eval --numeric-only

# Full run: numeric guard + LLM faithfulness (~$0.25, ~137 API calls)
python -m eval.run_eval

# Just the numeric guard by itself
python -m eval.numeric_guard
```

CI (`eval.yml`) runs `--numeric-only` when `ANTHROPIC_API_KEY` is absent, and the full
eval when the secret is present.  Add the secret under Settings → Secrets and variables →
Actions → `ANTHROPIC_API_KEY`.

---

## Before/after comparison (candidate eval)

To measure the impact of a synthesiser prompt change against the SAME frozen inputs:

1. Re-run the synthesiser against the live DB (so `insight_reports` is updated).
2. Run `python -m eval.freeze_candidate` — this copies the frozen inputs byte-for-byte
   and regenerates `findings.json` from whatever is now in `insight_reports`.
3. Score the candidate set:
   ```bash
   python -m eval.run_eval --golden-dir eval/candidate
   ```

The comparison is valid because `themes.json`, `samples.json`, and `stats.json` are
identical to the golden set — only `findings.json` differs (new synthesiser output).
`eval/golden/` is never touched by `freeze_candidate`.

> **Only compare before/after when the DB item set is unchanged since the golden was
> frozen.** If items were added or re-clustered between `freeze_golden` and the
> synthesiser re-run, the new findings may cite `evidence_item_ids` that aren't in the
> frozen `samples.json`, inflating `hallucinated_citation_rate` — that reflects DB drift,
> not a model regression.

---

## Metrics and thresholds

| Metric | Threshold | What it measures |
|---|---|---|
| `faithfulness_rate` | >= 0.90 | Fraction of findings rated "supported" by the LLM judge |
| `hallucinated_citation_rate` | <= 0.02 | Findings citing a review id not in the shown sample |
| `numeric_guard_violations` | 0 | Numeric tokens in claims/actions unsupported by cited text or stats |

Thresholds live in `thresholds.yaml` — edit there, not in code.

---

## LLM faithfulness judge

The judge (`faithfulness.py`) calls `claude-sonnet-4-5` once per finding.  Each call
receives exactly three blocks, in this order:

```
CLAIM:
<the finding claim text>

SOURCE REVIEWS:
[Review 1]: <full text of cited review 1>
[Review 2]: <full text of cited review 2>
...

SYSTEM-VERIFIED STATISTICS (treat as ground truth):
{
  "item_count": ...,
  "star_distribution": {...},
  "country_breakdown": {...},
  "date_range": {"min": ..., "max": ...},
  "last_30_days": ...,
  "prior_30_days": ...,
  "trend": ...
}
```

**What the judge receives:** the claim, the cited review texts, and the numeric stats dict
(item_count, star_distribution, country_breakdown, date_range, last_30_days,
prior_30_days, trend) — all factual, Python-computed values.

**What the judge does NOT receive:** theme label, summary, category, sentiment, or any
other finding.  These are withheld deliberately.  Label/summary/category are LLM opinions
written by earlier agents; letting the judge see them would allow it to rationalise a
claim from contextual priors rather than checking it against hard evidence.  Stats are
factual aggregates, not opinions, so they are safe to pass.

**Why stats help:** claims grounded in Python-computed aggregates — e.g. "25 of 29 reviews
are 1-star" — were previously judged "unsupported" because the count did not appear
verbatim in any cited review text.  Now the judge can confirm it against the stats block.
Derived arithmetic (e.g. "86%") that is NOT a literal stats value still fails.

**max_tokens:** raised from 512 to 1024 — truncation caused ~6.6% parse failures on
responses with long `unsupported_details` lists.

---

## Numeric guard rules

The guard extracts every numeric/date/duration token from each finding claim and each
recommended action, then checks whether the token appears in:

1. The actual text of the cited reviews (for findings), or all sample text (for actions).
2. The aggregate stats for the theme (item_count, star distribution, country counts,
   date_range min/max, last_30_days, prior_30_days).

Token patterns matched (in priority order):
- Year ranges: `2021-2026`, `2020–2025`
- Duration phrases: `2 days`, `3 weeks`, `1 month`, `5+ years`
- Star rating refs: `1-star`, `5★`, `3 stars`
- Percentages: `45%`, `12.5%`
- 4-digit years: `2024`
- Plain integers and decimals

Limitation: the guard catches *invented tokens* but cannot catch invented prose that
contains no number.  The LLM faithfulness judge handles that case.

---

## Churn signal formula

`churn_signal(items, stats) -> "high" | "medium" | "low" | "none"`

Computed in Python from two independent ratio signals:

| Signal | Definition |
|---|---|
| `one_star_ratio` | `star_distribution[1] / item_count` |
| `churn_language_ratio` | items matching churn regex / item_count |

Churn regex (case-insensitive):
`cancel|cancell|unsubscrib|switching? to|moving to|refund|deleting|delete the app|uninstall|no longer using|won't be using|leaving|gave up on`

Bucket thresholds (first match wins, none forces if category == "praise"):

| Bucket | Condition |
|---|---|
| `high` | `one_star_ratio >= 0.50` OR `churn_language_ratio >= 0.15` |
| `medium` | `one_star_ratio >= 0.25` OR `churn_language_ratio >= 0.07` |
| `low` | `one_star_ratio >= 0.08` OR `churn_language_ratio > 0` |
| `none` | neither threshold met |

Praise/other category always maps to `none` (hard override).

---

## Priority signal formula

`priority_signal(theme, items, stats) -> "P0" | "P1" | "P2" | "P3"`

### Severity levels (highest applicable wins)

| Severity | Condition |
|---|---|
| sev4 (P0 base) | label+summary matches access-blocking/data-loss/security regex |
| sev3 (P1 base) | `category == "bug"` |
| sev2 (P2 base) | `category in {ux, complaint, pricing}` |
| sev1 (P2 base) | `category == "feature_request"` |
| sev0 (P3 base) | praise / other / unknown |

Sev4 regex:
`data loss|lost .*(note|data|work)|deleted|logged out|log in|sign in|can't (log|sign)|locked out|crash on launch|won't open|breach|security`

### Volume and trend nudges

Applied after the base priority:

- **Volume nudge**: `item_count >= 80` bumps priority up one level (P3→P2, P2→P1, P1→P0).
- **Trend nudge**: `last_30_days > prior_30_days AND prior_30_days >= 3` bumps priority
  up one level.  The `prior_30_days >= 3` guard prevents spurious acceleration signals
  from tiny or brand-new clusters where `prior_30 = 0` would make every cluster look
  like it is accelerating.

Both nudges are independent and both may apply (max two-level bump).  All bumps cap at P0.

### Examples

| Theme | Category | item_count | Trend | Result |
|---|---|---|---|---|
| App crashes on launch | bug (sev3) | 37 | stable | P1 (base P1, no nudges) |
| App freezes | bug (sev3) | 82 | stable | P0 (P1 + volume) |
| Login data loss | bug + sev4 | 29 | stable | P0 (base P0) |
| Missing offline | feature_request | 30 | stable | P2 |
| Enthusiastic praise | praise | 551 | stable | P3 |

---

## Interpreting the scorecard

When you run `python -m eval.run_eval`, the output table shows:

- `[OK]` — metric passes the threshold.
- `[FAIL]` — threshold breached; CI will fail the build.
- `SKIPPED` — LLM eval skipped (no key or `--numeric-only`).

The results JSON written to `eval/results/<timestamp>.json` contains per-finding
faithfulness verdicts so you can inspect exactly which claims the judge flagged.

To diagnose a faithfulness failure: look at `faithfulness_details` in the JSON for
findings with `verdict != "supported"`, then check the cited review texts in
`golden/samples.json` to see what evidence the judge was working from.
