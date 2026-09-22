# Region strata

## Outcome

`corn-price/` runs again on node 2's current region set. Node 1 split Nebraska
and Kansas into irrigated and rainfed strata, node 2 merged that change
(`wofost-bundles` PR #2, `24d658b`, 2026-09-21) and now emits twelve regions
keyed `ia, il, mn, ne_irrigated, ne_rainfed, in, sd, oh, wi, ks_irrigated,
ks_rainfed, mo`. `ne` and `ks` no longer exist. Node 3 keys both of its
region tables on the old ten and raises on an unknown `region_key` by design, so
the four-step flow stops at node 3 today.

When this is done, node 3 carries production weights and an observed detrended
yield distribution per **stratum**, so `ne_irrigated`'s percentile rank is read
against a distribution whose spread reflects irrigated corn rather than
Nebraska's blended average. The national shock and the price impact it implies
are once again computable from a real node 2 output, and the rescaling that makes
the stratum distributions possible is stated in the output document rather than
assumed.

## Scope

### In scope

- Re-key `production_weights.csv` on node 1's twelve keys, with per-stratum
  harvested acres and production share derived from the 2022 Census of
  Agriculture by `build_weights.py`.
- Re-key `yield_history.csv` and its trend coefficients on the twelve keys.
  Each stratum's 1995-2024 detrended deviation distribution is its state's
  distribution rescaled in spread by a stratum/state dispersion ratio measured
  on the NASS `CORN, GRAIN, IRRIGATED - YIELD` and `NON-IRRIGATED - YIELD`
  survey series over the years that series covers (see constraints).
- A committed, sourced table declaring each region's stratum and, for split
  states, the measured dispersion ratio and the years it was measured over --
  following node 2's `water_regime.csv` pattern, so nothing infers a regime
  from the spelling of a region key.
- The rescaling, its source, its measurement window and what it does not capture
  in the output metadata, the Modelfile annotations and the README, in the same
  three places the existing rescaling claim appears.
- `check_price.py` extended: the weights sum to the stated coverage over twelve
  regions, the per-stratum dispersion ratio against node 2's simulated spread is
  still above 1 everywhere, the 2012 drought case still reproduces the national
  outcome, an unknown key still fails loudly, and the rescaling is applied in the
  declared direction (irrigated narrower than its state, rainfed wider).
- A twelve-region `sample_input.json` taken from a real node 2 run.

### Out of scope

- Node 1 and node 2. Their region set, stratum weights, apportionment method and
  baseline window are given and are not edited from this brief.
- The 1995-2024 window itself. It is a contract with node 2 and does not move.
- The transmission fit, `price_history.csv`, `transmission.json` and the
  export-exposure block from brief 0002. Nothing in the price arithmetic changes.
- Splitting any state beyond Nebraska and Kansas, and any per-stratum irrigation
  or soils modelling, which is node 2's.
- The Model Home registration and the flow composition, which follow the merge.

## Acceptance criteria

- **AC-1** — `runner.py` produces a valid output over a real twelve-region node 2
  snapshot, and still fails loudly on a `region_key` with no table row.
- **AC-2** — `production_weights.csv` carries twelve rows. Each split state's two
  strata sum to that state's published 2022 Census harvested acres, and the
  twelve production shares sum to the same coverage figure the ten did (82.47%
  of US corn-for-grain production) within rounding.
- **AC-3** — `yield_history.csv` carries 30 rows per region for 1995-2024
  inclusive for all twelve keys, so `check_periods` and the per-region exact-year
  check pass unchanged.
- **AC-4** — The dispersion ratio between the stratum and state distributions in
  `yield_history.csv` matches the ratio measured from the NASS irrigated series,
  and that measurement, its window and its source are recorded in
  `yield_history.meta.json`.
- **AC-5** — Every region's `dispersion_ratio` in the output -- node 2's
  simulated spread over this model's observed spread -- is above 1, including
  both irrigated strata, and `check_price.py` asserts it.
- **AC-6** — The 2012 drought validation still runs end to end over the twelve
  regions and reproduces the actual national yield deviation within the band the
  existing check states, with the split states fed their own stratum ranks.
- **AC-7** — The output document, the Modelfile `provenance`/`validity_domain`
  annotations and the README each state that the stratum distributions are
  rescaled state distributions, name the measurement window, and say that the
  irrigated strata's yields are an upper bound because node 2 models irrigation
  supply as unconstrained.
- **AC-8** — `check_price.py` passes in full, and nothing in `transmission.json`,
  `price_history.csv` or the fitted coefficient changes.

## Constraints and dependencies

Measured against the cached USDA NASS bulk exports on 2026-09-21, the same files
the existing build scripts use:

- **The break is purely in key values.** Node 2's output shape is unchanged --
  same `required` key set -- so the Modelfile schema binding to
  `corn_yield_snapshot` still holds and must not be touched. Verified: a
  twelve-region snapshot fails at `runner.py:248` with `unknown region_key
  'ks_irrigated'`, after `check_periods` has already passed.

- **A per-stratum 30-year observed history does not exist.** NASS *does* publish
  a state-level annual `CORN, GRAIN, IRRIGATED - YIELD, MEASURED IN BU / ACRE`
  and a `NON-IRRIGATED` counterpart in the SURVEY program for NE and KS, but
  **both series end in 2018**: 24 of the 30 years in 1995-2024, missing 2019
  through 2024, which includes the 2022 western drought. A straight per-stratum
  re-key is therefore impossible under the fixed window, and the runner's
  exact-year check rejects a partial table by design.

- **The 24 overlapping years do give a measured damping factor.** Detrended
  p10-p90 spread, 1995-2018:

  | | irrigated | state total | non-irrigated |
  |---|---|---|---|
  | NE | 10.1 pts (0.50x) | 20.5 | 43.1 (2.11x) |
  | KS | 16.4 pts (0.62x) | 26.6 | 51.5 (1.94x) |

  Reusing a state distribution unchanged for its irrigated stratum would
  overstate that stratum's real volatility by about a factor of two, and
  understate the rainfed stratum's by about the same, and that error would feed
  straight into the price signal.

- **The rescaling is a documented substitute, not a measurement.** It assumes
  the stratum/state dispersion ratio measured over 1995-2018 holds through 2024,
  and it preserves the state distribution's shape while changing only its scale.
  Both assumptions belong in the output and the README beside the number.

- **Per-stratum weights are fully derivable, and are not the hard part.** The
  2022 Census publishes state-level `CORN, GRAIN, IRRIGATED - ACRES HARVESTED`
  directly (NE 4,554,560 of 8,648,207; KS 1,181,420 of 4,658,341), and
  operation-class yields `IRRIGATED, ENTIRE CROP` and `IRRIGATED, NONE OF CROP`
  give the stratum yield level. `production_weights.csv` already carries
  `acres_irrigated` and `irrigated_share` per state from that same file and
  vintage.

- **A stratum is not "the better half".** Node 1 documents that operations
  irrigating their entire corn crop out-yield those irrigating none by 105% in
  Kansas and 55% in Nebraska, but yield 8% *less* in Iowa and 20% less in Ohio.
  Nothing here may assume irrigated means better, and the two irrigated strata
  are irrigated because a committed table says so, not because of how their keys
  are spelled -- the discipline node 2 already asserts in `check_yield.py`.

- **Node 2 models irrigation supply as unconstrained.** No aquifer decline, no
  allocation limit, no pumping ceiling in extreme heat, so `ne_irrigated`'s and
  `ks_irrigated`'s drought protection is an upper bound. How node 3 should treat
  that is left to the plan to propose.

- **Node 1 owns the region set.** Node 3 joins on the key, adds its own
  attributes, and never redefines the key or invents one.

- **No network at run time.** The committed tables are produced by the one-time
  committed build scripts, which are not in the image, and every built table
  ships its `*.meta.json` vintage.

- **Sequencing.** `wofost-bundles` PR #2 has landed on `origin/main`; this brief
  depends on it and on nothing else. Model Home re-registration of both nodes
  follows the merge of this work.

## General guidance

- Before you write the plan, ask any questions you need to in order to best implement the brief
