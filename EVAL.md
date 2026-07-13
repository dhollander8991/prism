## The Alerter: a negative result

The Alerter detects anomalous spikes in per-theme review volume — "data-loss reports jumped
4σ above baseline the week of June 8th." It is pure statistics: a rolling Z-score over
weekly buckets, **no LLM anywhere in the module** (grep-verified by the reviewer).

It was built, tested, reviewed, and run on the real corpus. **It found no signal worth
surfacing, and it ships disabled.**

This section documents why, because the decision not to ship a working feature is the more
interesting engineering result.

---

### What was built

Weekly buckets per theme (Monday-anchored, zero-filled — a week with no reviews is `0`, not
missing). For each week `w`, a rolling Z-score against the prior `n` weeks:

```
z = (count_w − mean(prior n weeks)) / stddev(prior n weeks)
```

A spike requires `z ≥ 2.5` **and** `count_w ≥ 5`.

Four guards, each protecting against a specific failure mode:

| Guard | The failure it prevents |
|---|---|
| Baseline is strictly `series[w-n : w]` | **Leakage** — including week `w` in its own baseline suppresses the very anomaly you're hunting. |
| `min_absolute` floor (default 5) | The classic Z-score-on-sparse-data failure: a theme going **0 → 2 reviews** registers as a 6σ event. Statistically true, operationally meaningless. |
| Explicit `stddev == 0` branch | A perfectly flat baseline divides by zero. Handled, not crashed. |
| `insufficient_history` is a distinct state | A theme with fewer than `n+1` weeks **cannot be evaluated**. It is marked as such — never silently reported as "no spike," which would be a lie by omission. |

Reviewer confirmed all four, plus no LLM call anywhere in the module. 14 alerter tests;
178 in the suite overall.

---

### What the data actually said

**Corpus:** 2017-09-21 → 2026-07-06 (~9 years), 1,371 clustered items across 301 populated
weeks. All 22 themes had sufficient history; none were excluded.

**Three spikes detected. All of them sit exactly on the floor.**

| Week | Theme | Count | Baseline mean | z |
|---|---|---|---|---|
| 2023-04-10 | App stuck on loading screen | 5 | 0.25 | 10.97 |
| 2023-10-23 | Enthusiastic praise | 5 | 0.50 | 6.36 |
| 2026-01-26 | Unwanted AI feature prominence | 5 | 1.12 | 3.32 |

**Every spike has `count = 5` — the exact minimum floor — against a near-zero baseline.**

The z-scores are inflated by tiny denominators. A z of 10.97 sounds dramatic; what it
actually means here is *"five reviews in a week for a theme that normally sees zero or
one."* That is a small-N blip wearing a large number.

**Two of the three disappear entirely if the floor is raised from 5 to 8.** A result that
fragile is not a result.

---

### The artefact that was ruled out

The obvious hypothesis: App Store reviews cluster after releases, so *every* theme spikes in
the same week. If true, the detector would be discovering "Notion shipped an update," not
"the data-loss bug got worse" — a corpus artefact masquerading as per-theme signal.

**Checked. The recency ramp is real, but it produced zero correlated spikes.**

Corpus-wide volume ramps hard, from ~2/week early on to ~20–25/week from late 2025. But no
theme spiked as a result, for two reasons:

1. **Dilution.** That volume spreads across 22 themes — roughly 1 review per theme per week
   even at peak.
2. **A plateau is not a spike.** The rolling baseline *absorbs* sustained elevation. Volume
   that rises and stays high is, by construction, normal relative to its own recent history.
   Demonstrated directly: cluster_30's recent weeks sit at a 5–9/week plateau and produce
   **no recent spike** — its only detected spike is an isolated 2023 event.

The three spikes land in three different years. This is not the release-timing artefact.

**The detector correctly did not fire on the thing that would have fooled a naive
implementation.** That is the detector working.

---

### The decision: ship it disabled

The infrastructure is correct, leak-free, and tested. The signal is not there.

> **A single static, per-theme-sparse scrape does not contain robust weekly spike signal.
> Presenting these three blips to a PM as actionable alerts would overstate the evidence.**

So:

- **The Alerter ships.** The weekly trend series is real and is surfaced in the UI — a PM
  can see a theme's genuine volume history, which is useful on its own. A flat line means a
  chronic annoyance; a rising line means something is getting worse. That distinction is
  real and the list view cannot show it.
- **The alerts strip does not ship.** It renders only for spikes with `count ≥ 8` and
  `z ≥ 3.0`. No spike in the real data clears that bar, so the strip is currently absent —
  and that is the correct behaviour. There is no "all clear" empty state, because an empty
  state would still be making a claim.
- **No fabricated sigma values render anywhere.** An earlier UI iteration displayed mock
  spike data ("6 themes spiking — 4.2σ") inherited from the frontend scaffold. It was
  removed. A dashboard asserting a statistical claim the backend cannot support is the exact
  failure this project's eval work exists to prevent.

**What would make it real:** live daily ingestion (giving dense, current weekly buckets
rather than a nine-year sparse retrospective), or a higher-volume corpus per theme. Both are
data problems, not engineering ones. The detector is ready for either.

---

### Why this is in the eval doc

Every other section here describes catching the *model* being wrong. This one describes
catching **the data being insufficient** — and choosing not to ship a feature that would
have looked impressive and meant nothing.

The alerts strip would have been the flashiest thing on the dashboard. A red banner reading
*"App stuck on loading screen — 10.97σ spike"* is exactly the kind of number that makes a
demo land. It is also, on inspection, five reviews.

**Building the feature was the easy part. Measuring it, disbelieving it, and turning it off
was the work.**