# Plan: Region strata

Source brief: docs/features/0003-region-strata.md
Status: implemented
Planned against commit: 896f6cad20c9b2ea97e7091bebb6d1fe33625ffa
Base commit: 896f6cad20c9b2ea97e7091bebb6d1fe33625ffa (branch `feat/0003-region-strata`)

## Outcome

`corn-price/` runs on node 2's twelve `region_key` values instead of the ten it
was built against. `ne_irrigated`, `ne_rainfed`, `ks_irrigated` and `ks_rainfed`
each carry their own production weight, their own trend yield level and their own
observed detrended deviation distribution, so a percentile rank from node 2's
irrigated stratum is read against a distribution whose spread reflects irrigated
corn rather than Nebraska's blended average. Nothing in the price transmission
changes; the four-step flow gets past node 3.

## Scope

### In scope

- `production_weights.csv` re-keyed on twelve regions, split acres and
  production share for NE and KS, built by `build_weights.py`.
- `yield_history.csv` and `yield_history.meta.json` re-keyed on twelve regions,
  with per-stratum trend coefficients and rescaled deviation series, built by
  `build_yield_history.py`.
- ~~A new committed `region_strata.csv`~~ -- superseded by C-1: a `stratum`
  column in `production_weights.csv` and the ratios in `yield_history.meta.json`.
- Output metadata, Modelfile annotations and README stating the rescaling.
- `check_price.py` extended for the twelve-region set and the new transformation.
- A twelve-region `sample_input.json` from a real node 2 run.

### Out of scope

- Node 1 and node 2. Read-only here.
- The 1995-2024 window, the transmission fit, `price_history.csv`,
  `transmission.json`, and brief 0002's export-exposure block.
- Splitting states beyond NE and KS; per-stratum irrigation or soils modelling.
- Model Home registration and flow composition, which follow the merge.

## Assumptions and decisions

Two questions were put to John while framing the brief. Both were answered; the
answers are binding on `run`.

### D-1. Split to twelve, with a rescaled state distribution

**Answer: split to twelve.** Each stratum's 1995-2024 deviation series is its
state's series rescaled in spread by a stratum/state dispersion ratio measured on
the NASS irrigated survey series. Three alternatives were put and rejected:

- **Recombine to ten** is cheapest and needs no new data, but it requires
  collapsing two percentile ranks into one. A production-weighted mean of two
  ranks is not the rank of the combined yield, and it pulls toward 50 exactly
  when the strata diverge — i.e. it damps hardest in the drought years the model
  exists to catch. **Verified blocker:** the snapshot's
  `metadata.baselines.regions[k]` carries only quantiles (`median_kg_ha`,
  `p10_kg_ha`, `p90_kg_ha`, `min`, `max`, `n_years`) and **no per-year series**,
  so node 3 cannot reconstruct a combined baseline distribution from its input
  and re-rank properly. It would have to vendor node 2's `baseline_yields.csv`,
  which `CLAUDE.md` forbids.
- **Recombine to ten on a weighted anomaly** (weight `yield_projection_kg_ha` and
  `yield_baseline_kg_ha` into a state simulated anomaly) is statistically cleaner
  but puts the model back on node 2's uncalibrated magnitude — the one quantity
  `CLAUDE.md` says never to transmit — with no rank left to recover.
- **Split to twelve on the real 1995-2018 stratum series** uses honest data but
  breaks the period coupling: node 2's ranks refer to 1995-2024, so rank *n*
  would stop meaning the same thing on both sides, and fixing it needs a second
  upstream change that is out of scope.

### D-2. The irrigated-supply upper bound is documented, not damped

**Proposed answer, for John's review: document only; change no arithmetic.**

Node 2 models irrigation as unlimited supply — no aquifer decline, no allocation
limit, no pumping ceiling in extreme heat — so `ne_irrigated`'s drought
protection is an upper bound. The argument for leaving the arithmetic alone:
node 3's irrigated distribution is built from **observed** NASS irrigated yields,
which already embed whatever supply constraints were real over 1995-2018, so the
*scale* node 3 maps onto is not inflated by node 2's assumption. What survives is
a possible **rank** error — node 2 placing an irrigated stratum too high in a
severe drought — which node 3 cannot detect or correct from a rank alone. A
haircut would be a tuned parameter with no source behind it, which the repo's
modelling-honesty rules push against. It therefore goes in `not_captured`, the
README and the Modelfile `validity_domain`, and joins the existing upstream
follow-up in `CLAUDE.md`'s task list.

### Measured inputs the decision rests on

Verified on 2026-09-21 against the cached bulk exports, not from memory.

- **The break.** A twelve-region snapshot fails at `runner.py:248`:
  `unknown region_key 'ks_irrigated': no row in production_weights.csv`. It
  fails on the first stratum row, before any arithmetic, and **after**
  `check_periods` has passed — so the period contract is intact and the break is
  purely key values. Node 2's output `required` key set is unchanged, so the
  Modelfile schema binding to `corn_yield_snapshot` must not be touched.
- **A per-stratum 30-year history does not exist.** NASS *does* publish
  state-level annual `CORN, GRAIN, IRRIGATED - YIELD, MEASURED IN BU / ACRE` and
  a `NON-IRRIGATED` counterpart under `SOURCE_DESC=SURVEY`,
  `PRODN_PRACTICE_DESC=IRRIGATED` / `NON-IRRIGATED`, for NE, KS, CO, TX and
  others — but for NE and KS **both series end in 2018**: 24 of 30 years,
  missing 2019-2024 including the 2022 western drought.
- **The dispersion ratios**, detrended p10-p90 spread over the 24 overlapping
  years 1995-2018:

  | | irrigated | state | non-irrigated | irr/state | rainfed/state |
  |---|---|---|---|---|---|
  | NE | 10.14 | 20.47 | 43.14 | **0.50** | **2.11** |
  | KS | 16.43 | 26.61 | 51.53 | **0.62** | **1.94** |

- **Node 2 agrees on direction.** Its simulated baseline p10-p90 spreads:
  `ne_irrigated` 26.7%, `ne_rainfed` 178.0% (old `ne` 222.2%); `ks_irrigated`
  43.3%, `ks_rainfed` 280.8% (old `ks` 257.6%).
- **The `dispersion_ratio` assertion survives, and improves.** Simulated over
  observed, with per-stratum distributions: `ne_irrigated` 2.63, `ne_rainfed`
  4.13, `ks_irrigated` 2.64, `ks_rainfed` 5.45 — all above 1, which is what
  `check_price.py` asserts. Reusing the state distribution unchanged would have
  given 1.40 and 1.66 for the irrigated strata, understating the real
  over-dispersion.
- **Per-stratum weights are derivable and are not the hard part.** 2022 Census,
  state level: `CORN, GRAIN, IRRIGATED - ACRES HARVESTED` NE 4,554,560 of
  8,648,207 and KS 1,181,420 of 4,658,341; operation-class yields
  `IRRIGATED, ENTIRE CROP` NE 197.8 / KS 173.1 bu/acre and
  `IRRIGATED, NONE OF CROP` NE 127.3 / KS 84.4 bu/acre.

### Other material assumptions

- **A-1. The stratum trend is fitted on the real 1995-2018 stratum series.**
  The state trend stays fitted on 1995-2024. The two windows differ, and that is
  recorded in `yield_history.meta.json` per region. The trend is a *level* used
  for weighting (`acres_harvested * trend_yield_bu_acre`) and the runner already
  evaluates it outside its fitting window for the input year, so extrapolating
  six further years is the same operation, not a new one.
- **A-2. The rescaling preserves shape and changes only scale.**
  `deviation_pct_stratum(year) = deviation_pct_state(year) * ratio`, with the
  ratio applied about the series' own centre so the median-centred quantile map
  behaves as it does today. The eight unsplit states get `ratio = 1.0` and their
  rows must come out **byte-identical** to the current table — that is the
  regression guard.
- **A-3. Stratum identity comes from a committed table, never from the key.**
  `production_weights.csv` declares `stratum` per region (see C-1), mirroring
  node 2's `water_regime.csv` discipline, and a check asserts no code infers a
  stratum from a key's suffix. Node 2's `metadata.baselines.regions[k].regime` is used as a
  cross-check, not as the source.
- **A-4. Old `ne` and `ks` rows are removed, not retained.** Node 1 and node 2
  emit only the twelve; keeping dead rows would let a stale input pass quietly.
- **A-5. `sample_input.json` must come from a real node 2 run**, not a re-keyed
  copy of the current one. The synthetic twelve-region document used to confirm
  the break is a diagnostic only and is not committed.
- **A-6. Coverage does not change.** Splitting a state redistributes its share
  between two rows; the twelve shares must still sum to 0.8247 within rounding.

## Acceptance-criteria traceability

| ID | Acceptance criterion | Implementation | Verification | Status |
|---|---|---|---|---|
| AC-1 | Valid output over a real twelve-region snapshot; unknown key still fails loudly | `production_weights.csv`, `yield_history.csv`, `sample_input.json` regenerated from a real node 2 run at `24d658b` | Runner produces both outputs; `check_loud_failures` passes; subset check re-pointed at `ne_irrigated` | **pass** |
| AC-2 | Twelve weight rows; split acres sum to the state total; shares still sum to 82.47% | `build_weights.py` `apportion_state` + `STRATA` | `check_tables`: NE and KS strata sum to published census acres; coverage 0.824706, unchanged | **pass** |
| AC-3 | 30 rows per region for 1995-2024 on all twelve keys | `build_yield_history.py` | 360 rows, 30 per region; `check_periods` and the exact-year check pass in a full run | **pass** |
| AC-4 | Dispersion ratio matches the NASS measurement, recorded with window and source | `rescale_to_stratum`, `yield_history.meta.json` | `check_stratum_rescaling`: direction, year count, series pinning, the committed-spread/recorded-ratio identity, and the weight-neutrality invariant in the runner's own weights | **pass** |
| AC-5 | Every region's output `dispersion_ratio` above 1, both irrigated strata included | unchanged runner logic on per-stratum distributions | `check_rescaling`: **2.7x to 9.2x**; irrigated stratum below its rainfed one in both states | **pass** |
| AC-6 | 2012 drought validation over twelve regions | `check_price.py` AC-9 block, unmodified | modelled **-21.52%** vs actual **-22.23%** (0.71 pts), with the four strata ranked on USDA's **published** 1995-2018 stratum series rather than this model's reconstruction; ten-region baseline was -21.92% (0.31 pts) | **pass** |
| AC-7 | Rescaling and irrigated upper bound in output, Modelfile and README | `runner.py` assumptions + `not_captured`, `Modelfile.toml`, README section 1b | `check_stratum_rescaling` asserts all three; Modelfile validates with no annotation warnings | **pass** |
| AC-8 | Full suite passes; transmission and price tables unchanged | - | **157/157**; `git diff --stat` empty for `transmission.json`, `price_history.csv`, `price_history.meta.json`, both price build scripts | **pass** |

## Verification

Baseline captured at planning time on commit `896f6ca`.

| Command | Purpose | Baseline result | Final result |
|---|---|---|---|
| `python3 corn-price/runner.py corn-price/sample_input.json run/corn_price_regions.output.json > run/corn_price_impact.output.json` | The model runs and produces both outputs | **pass** — 10 regions, US shock +0.2303%, impact -0.1801% | **pass** — 12 regions, US shock **+0.1331%**, impact **-0.1041%** |
| `cd corn-price && uv run --python 3.12 python check_price.py` | The modelling claims, end to end | **95/95 checks pass** | **157/157 checks pass** (62 added across two Copilot reviews) — improved |
| `cd corn-price && docker build -t ag-corn-price:local . && docker run --rm --network none ag-corn-price:local` | Image builds; determinism claim holds | **pass** — US shock 0.2303, impact -0.1801, identical to the local run | **pass** — identical to the local run apart from `generated_at`, `--network none` — unchanged |
| `python3 corn-price/build_weights.py --regions ../agromet-bundles/crop-weather/regions.csv` | The region map agrees with node 1 | **fails as predicted** — `only upstream: ['ks_irrigated', 'ks_rainfed', 'ne_irrigated', 'ne_rainfed']; only here: ['ks', 'ne']`. This is the break; it must pass after. | **pass** — `regions: verified against .../regions.csv` — regression fixed |

Baseline run on `896f6ca` at 2026-09-21. No pre-existing failures other than the
drift guard above, which is the defect this brief exists to fix.

`check_price.py` needs Python 3.11+ for `tomllib`; the system `python3` here is
3.9.6, so it runs under `uv --python 3.12`. The runner itself is stdlib-only and
runs on either.

## Implementation steps

1. **Update the region map in both build scripts.** `build_weights.py:69`
   `REGIONS` and `build_yield_history.py:load_regions` move to node 1's twelve
   keys. Confirm `regions_from_upstream` passes against
   `agromet-bundles/crop-weather/regions.csv` at `0618aed`.
2. **Declare the stratum.** Superseded by C-1: rather than a third table, add a
   `stratum` column (`all` / `irrigated` / `rainfed`) to `production_weights.csv`
   from `build_weights.py`'s `STRATA` map, and record each measured
   `dispersion_ratio` with its window and source in `yield_history.meta.json`.
3. **Extend `build_weights.py` to split NE and KS.** Read state-level
   `CORN, GRAIN, IRRIGATED - ACRES HARVESTED` (already read for
   `irrigated_share`) for the irrigated stratum's acres; rainfed acres are total
   minus irrigated. Apportion the state's published production between strata by
   acres times the operation-class stratum yields, and say in the row's `method`
   that this mixes an operation-class yield with an area split, exactly as node 1
   says it. Assert the two strata's acres sum to the published state total.
4. **Extend `build_yield_history.py` to emit stratum rows.** Read the
   `IRRIGATED` / `NON-IRRIGATED` survey series for NE and KS, fit each stratum's
   own trend over the years the series covers, measure the stratum/state p10-p90
   dispersion ratio over that same window, then write 1995-2024 rows whose
   `deviation_pct` is the state's series scaled by that ratio and whose
   `trend_yield_bu_acre` comes from the stratum's own fitted line. Record the
   ratio, its window, the series' `SHORT_DESC` and the fact that it ends in 2018
   in `yield_history.meta.json`. Fail the build if a stratum series is shorter
   than a stated minimum rather than quietly fitting on a handful of years.
5. **Cross-check the two tables.** `build_yield_history.py` already reads its
   region set from `production_weights.csv`; keep that, and have it read the
   stratum from the same place so the two cannot disagree.
6. **Regenerate `sample_input.json`** from a real twelve-region node 2 run
   (`wofost-bundles` at `24d658b`), not from a re-keyed copy.
7. **Runner changes, deliberately minimal.** The join, the quantile map and the
   aggregation are key-agnostic and should not need editing. Add: `stratum` to
   each output region row, the rescaling statement to the `assumptions` block,
   and the irrigated upper bound to `not_captured`. Fail loudly at load time on a
   `production_weights.csv` with no `stratum` column.
8. **Modelfile and README.** Update `validity_domain` (within its 600-character
   cap) and `provenance` (400), the region list, the rescaling section and the
   limitations. Add the `stratum` property with its `description` and, if the
   output row schema gains a required key, keep `required` consistent with the
   declared `properties` types.
9. **Extend `check_price.py`**: twelve-region table checks, per-state acre sums,
   `check_stratum_rescaling` recomputing the ratio, per-stratum
   `dispersion_ratio > 1`, the unsplit-eight byte-identity regression, a
   stratum-shaped unknown key in `check_loud_failures`, and no-inference-from-key
   -spelling. Re-point the 2012 case at the twelve-region history.
10. **Update `CLAUDE.md`**'s `corn-price/` design notes, file listing, verified
    results and task list, and note in the upstream follow-up that the rank error
    in a severely water-short year is still node 2's to fix.
11. **Rerun every verification command** and record final results in the table.

## Files likely to change

```
corn-price/production_weights.csv        re-keyed, twelve rows
corn-price/production_weights.meta.json  new region list, split method text
corn-price/yield_history.csv             re-keyed, 360 rows
corn-price/yield_history.meta.json       twelve trends, ratios, windows
corn-price/build_weights.py              twelve keys, stratum apportionment
corn-price/build_yield_history.py        stratum trends and rescaling
corn-price/runner.py                     strata table load, output metadata
corn-price/check_price.py                new and extended checks
corn-price/Modelfile.toml                annotations, stratum property
corn-price/README.md                     regions, rescaling, limitations
corn-price/sample_input.json             real twelve-region node 2 output
CLAUDE.md                                design notes, results, task list
docs/plans/0003-region-strata.md         status and final results
```

Unchanged, and a check should prove it: `transmission.json`,
`price_history.csv`, `price_history.meta.json`, `build_transmission.py`,
`build_price_history.py`.

## Conflicts and deviations found while building

Recorded as they were hit, per the skill's rule that implementation does not
silently redesign around a plan that turns out to be incomplete.

### C-1. `region_strata.csv` was not created; the stratum is a column instead

The plan called for a third committed table declaring stratum and dispersion
ratio per region, on the model of node 2's `water_regime.csv`. Building it made
the case against it: the stratum is an attribute of a region that
`production_weights.csv` already describes, and the dispersion ratio is a
*measured* output of `build_yield_history.py`, so hand-curating it in a third
file would have meant writing a measured number by hand and then needing a check
that the hand-copy matched the measurement.

So the stratum is a **column in `production_weights.csv`** (written by
`build_weights.py` from its `STRATA` map) and the ratio lives in
`yield_history.meta.json` beside the trend coefficients it belongs with. Two
tables instead of three, no Dockerfile change, and no cross-table consistency
check to maintain. The "declared, never inferred from the key" discipline the
third table existed to enforce is unaffected and is asserted directly against
`runner.py`'s source by `check_no_stratum_inference`.

### C-2. The NASS stratum series has a planted-acre twin

`CORN, GRAIN, IRRIGATED - YIELD` is published both `MEASURED IN BU / ACRE` and
`MEASURED IN BU / NET PLANTED ACRE`. Filtering on the production practice alone
would have mixed a planted-acre yield into a harvested-acre series -- a silent
denominator error of exactly the kind `CLAUDE.md` warns about, and one that
would have biased the measured dispersion ratio rather than failing. The build
pins the full `SHORT_DESC`, as the state series already did, and a check asserts
the recorded series string ends in `MEASURED IN BU / ACRE`.

### C-3. An existing check's premise was inverted by the upstream change

`check_rescaling` asserted that "the two most over-dispersed regions are the two
most irrigated", which was true when node 2 simulated Nebraska and Kansas as
dryland. Node 2 now irrigates them, so their simulated spreads collapsed
(`ne_irrigated` 26.7% against old `ne` 222.2%) and the claim is simply no longer
true -- South Dakota is now the most over-dispersed region. Replaced with the
claim that is true and that the rescaling depends on: within each split state
the irrigated stratum is the *less* over-dispersed of the two. This is a finding
about the old check, not a regression.

### C-4. The strata weights cross-validate against node 1

Not a conflict, but worth recording. Node 3 apportions a split state at **state**
level; node 1 did it at **county** level, independently. The resulting
within-coverage weights agree to within 0.0004 (`ne_irrigated` 0.0804 vs node 1's
0.0808; `ks_irrigated` 0.0177 vs 0.0173). The eight unsplit states match node 1
exactly.

### C-5. The raw marginal ratios do not recombine to the state (found in review)

Raised while explaining the shared-shape limitation, after the first Copilot
review. Each stratum's ratio is measured against its state marginally, so
production-weighting them should give 1.0 if the two strata really shared one
shape. They give **1.086** (NE) and **1.394** (KS). Marginal spreads add
linearly only under perfect correlation; measured over the published years the
two strata correlate **0.31** in Nebraska and **0.80** in Kansas, so the real
state series is narrower than the sum of its parts and a shared-shape
reconstruction overshoots. Used raw, the split handed Nebraska 8.6% and Kansas
39.4% more influence over the national shock than treating each as one region
did -- an artifact, not a decision.

Fixed by `normalise_ratios`: divide each state's ratios by their own
production-weighted mean. The measured irrigated-to-rainfed proportion is
preserved exactly; the state recombines to its own observed swing. Shipped
factors NE 0.456 / 1.941, KS 0.443 / 1.388, with the raw measurements kept
beside them as `dispersion_ratio_measured`. Cost: a stratum's spread is no
longer its own measured marginal spread, which is stated in the meta file, the
README and the output.

Measured effect: a 24-year backtest of the national figure reproduces the
ten-region model's RMSE (1.19 points) exactly, which is what a weight-neutral
split should do. See C-7 for the correction to where the normalisation happens,
and C-8 for what the 2012 case says once it is tested honestly.

### C-6. Copilot review 1 (PR #3): five findings, all valid

1. **`irrigated_share` changed meaning on a split row** and the schema still
   described the old one. Now explicitly the region's own share, with a new
   `state_irrigated_share` carrying the state figure on every row.
2. **The upstream water regime was never checked.** Node 2 publishes it per
   region; nothing compared it against this model's stratum, so a stale snapshot
   would have had its rank read against a distribution of roughly twice or half
   the right width. `check_baseline_regimes` now fails the run, reporting every
   mismatch at once, and treats an absent regime as a mismatch. This is the same
   class of finding Copilot raised on node 2's own PR, one field over.
3. **`coverage_share_of_us_all_ten`** was a stale API name; renamed to
   `coverage_share_of_us_all_regions`.
4. **The `dispersion_ratio` description** still said the ratio is largest in the
   irrigated states; node 2 now irrigates them, so it is lowest there.
5. **`production_weights.meta.json` referenced `region_strata.csv`**, which C-1
   removed. Regenerated.

Checks 133 -> 148 -> **154** with C-5's normalisation.

### C-7. Normalising in the table was exact for no run at all (Copilot review 2, HIGH)

C-5 divided the stratum ratios by their production-share-weighted mean at build
time. But the runner does not weight by production share: it weights by acres
times **each region's own fitted trend yield at the run's year**
(`runner.py` `weight_raw`). The two strata have their own trend slopes, so the
two bases diverge -- and the divergence grows with the year. Measured, the
build-time normalisation left Nebraska **+3.3% over-weighted in 2024, +4.1% in
2030 and +5.1% in 2040**, and Kansas +2.0% to +2.7%. Better than the +8.6% and
+39.4% C-5 removed, but silent and drifting, which is worse in character.

Fixed at the root: the committed table now carries the **measured** ratios, and
`normalise_ratios` moved into `runner.py`, which computes the divisor from the
weights it is about to use and rescales that state's mapped anomalies by it. The
identity is then exact at every run year. This is sound because the committed
stratum series is the state's scaled elementwise, so the quantile map is linear
in the distribution's scale and dividing the mapped anomaly is identical to
having scaled the distribution.

Each row now ships `stratum_dispersion_ratio` (applied),
`stratum_dispersion_ratio_measured` and `stratum_normalisation_divisor`, and the
check recomputes the invariant from the output's own `production_weight` --
Copilot's second finding, that a check on the production-share basis could pass
while the runner was not weight-neutral. Measured: applied mean **1.0000** in
both states, against 1.1252 (NE) and 1.4240 (KS) unnormalised.

### C-8. The 2012 case was validating the reconstruction against itself

`check_price.py` derived every 2012 rank from `yield_history.csv`, but for the
four strata that file holds the state series *rescaled* -- a reconstruction, not
an observation. Ranking it against itself tested nothing about the strata.

`build_yield_history.py` now records the published per-stratum deviations it
measured the ratio from (`observed_deviation_pct`), and the 2012 case ranks the
four strata against those. The real 2012 ranks are `ne_irrigated` p17,
`ne_rainfed` p0, `ks_irrigated` p8, `ks_rainfed` p0 -- irrigated Nebraska barely
felt the drought.

**The honest result is worse, and that is the point.** Modelled **-21.52%**
against the actual **-22.23%**, an error of **0.71 points**, against the
ten-region model's 0.31. Ranking the strata against their own rescaled series
would have reported 0.26. The extra error is the shared-shape assumption being
paid for in the open: the reconstruction cannot represent a season in which the
irrigated stratum is near trend while the rainfed one collapses, which is
exactly what 2012 was. This is the strongest evidence in the bundle for the
limitation already named in `not_captured`, and it now sits in the check suite
rather than in a comment.

## Risks and follow-ups

- **The rescaling is the weakest link, and is now quantified rather than just
  labelled.** Over the published years the two strata correlate **0.31** in
  Nebraska and 0.80 in Kansas, so the shared-shape assumption is close to
  unfounded in Nebraska -- which is 12.7% of covered production against Kansas's
  4.3%, i.e. weakest exactly where it costs most. In 2012 the model puts
  irrigated Nebraska at -8.1% against an actual -3.0%, and rainfed at -34.7%
  against an actual -55.4%. C-5's normalisation fixes the aggregate consequence;
  it does not fix the shape. Named in `not_captured`, the README and here.
- **NASS discontinued the series after 2018.** If it resumes, or if a
  county-level reconstruction becomes practical, the rescaling should be
  replaced by measurement. Worth a follow-up note in the README.
- **Node 2's rank in a water-short year** remains the residual error the
  rescaling cannot reach, and is already on `CLAUDE.md`'s task list as an
  upstream follow-up.
- **Six years of extrapolated stratum trend** feed the weights. The effect is
  second-order — weights are shares, so a common trend error largely cancels —
  but it should be measured and reported, not asserted.
- **Sequencing.** Node 2's change is merged on `origin/main`; the local
  `wofost-bundles` checkout is three commits behind and must be updated before
  step 6. Model Home re-registration of both nodes follows this merge.
