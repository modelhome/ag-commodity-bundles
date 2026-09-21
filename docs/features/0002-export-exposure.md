# Export exposure

## Outcome

`corn-price/` carries the export side of the national balance sheet as
committed data, and says plainly -- in the Modelfile annotations, in the output
document and in the README -- that trade policy is outside its transmission.

Two things are true when this is done that are not true today:

1. A reader of node 3's output can see the export exposure the season's price
   impact sits on top of: how many bushels of the marketing year's use are
   exports, and what share of use that is, beside the carry-in stocks-to-use
   already carried for context.
2. A downstream trade-policy model -- node 4, in its own repo, not built here --
   can express a lost-sales scenario as a share of US corn use without
   re-sourcing the balance sheet or picking a different vintage from node 3's.

The transmission gains no new term. It is, however, refitted against the
rebuilt table, because the exports column cannot be added without re-running
`build_price_history.py` against the live ERS source, and ERS revises history.
Any revision that returns is accepted and propagated rather than pinned away --
see the decision recorded under Acceptance criteria. This brief adds data and
labelling; it does not add economics.

## Scope

### In scope

**An `exports_mil_bu` column in `price_history.csv`**, read from the same ERS
file, the same vintage and the same `Table 4--Corn: Supply and disappearance`
that already supplies beginning stocks, ending stocks and total use. Exports are
a disappearance line in that table, so this is one more `read_series` call in
`build_price_history.py` and no new source, no new download and no new
dependency.

**A derived `export_share_of_use` column**, exports over total use for the same
marketing year, consistent with how `stocks_to_use` and `trend_yield_bu_acre`
are already derived into the committed table rather than recomputed downstream.

**The current exposure in `price_history.meta.json`**, alongside the existing
`defaults` block: the latest marketing year's exports, its export share of use,
the year those came from, and whether that year is a WASDE projection. A
`definitions` entry states that exports are a disappearance component of total
use and therefore already inside the denominator, so a reader cannot
double-count them.

**The exposure in the runner's output metadata.** The same pattern
`carryin_stocks_to_use` already follows: carried as context beside the result,
not entering the arithmetic. The output schema's `metadata` is a plain object
with no declared sub-properties, so adding keys inside it cannot disturb flow
binding, which compares required-key sets.

**Labelling that names trade policy.** The existing `not_captured` entry --
"policy shocks, including changes to the Renewable Fuel Standard" -- is where a
reader looking for exactly this will look first, and it does not currently say
the word. Sharpen it, in `build_transmission.py` so the committed
`transmission.json` carries it, and in the README's matching list, to state that
the transmission is fitted on supply-side weather shocks against an annual cash
marketing-year average price and captures neither export demand shocks nor trade
policy. Mirror the statement in the Modelfile `not_for` annotation within its
character cap.

**Validation in `check_price.py`.** Exports present and positive for every
complete marketing year; the derived share equal to exports over total use to
the committed precision; the share inside a plausible band for US corn across the
window; exports strictly less than total use in every row; the exposure present
in the output metadata; and the assertion that every existing committed headline
figure is unchanged by this feature.

### Out of scope

- **The node 4 trade-policy model itself.** It belongs in its own repo, with its
  own brief, and consumes node 3's output rather than living inside it. The
  design recorded at the end of this brief is context for that work, not work
  this brief does.
- Any tariff schedule, country-level trade table, destination split, or trade
  elasticity. Nothing in this brief knows what a tariff is.
- **Adding** an export or trade term to the transmission. Trade policy shocks
  of the relevant size are a handful of episodes in the fifty-year window; a term
  fitted on them would fail the repo's fixed |t| >= 2.0 selection rule exactly as
  every stocks-to-use form did. Re-running the existing specification against the
  rebuilt table is in scope and unavoidable; changing that specification is not.
- Cross-commodity effects: the soybean price and the acreage substitution that
  follows it are the larger channel for a China tariff, and they are node 4's
  problem, not this bundle's.
- Any change to the region set, the production weights, the quantile mapping or
  the coverage share.
- A live or market reference price, which remains a separate follow-up with its
  own three routes in the bundle README.

## Acceptance criteria

- **AC-1** -- `price_history.csv` carries `exports_mil_bu` and
  `export_share_of_use` for every marketing year in the existing window, sourced
  from `Table 4--Corn: Supply and disappearance` in the same ERS file and
  vintage as the columns already there. No second source is introduced.
- **AC-2** -- `build_price_history.py` produces them, using the table's own
  attribute string for exports, discovered from the export rather than assumed.
  A wrong attribute must fail the existing loud `SystemExit` in `read_series`,
  not write an empty column.
- **AC-3** -- The table is rebuilt by its build script against the **live** ERS
  source, and any revision to an already-committed value is **accepted**. The
  complete diff against the previously committed table is reported in the pull
  request. Nothing is hand-edited and nothing is pinned to the old vintage.
- **AC-4** -- `transmission.json` is rebuilt from the rebuilt table by
  `build_transmission.py`, whose bootstrap seed is fixed, so identical input data
  gives identical coefficients. Whether or not any coefficient moved is stated
  **explicitly** in the pull request, with before-and-after values for `shock`,
  its bootstrap interval, `n` and `r2`. Silence is not an acceptable result.
- **AC-5** -- `price_history.meta.json` records the current exports, export
  share of use, the year and its projection flag, plus a `definitions` entry
  stating that exports are a component of total use.
- **AC-6** -- The runner carries the export exposure into the output metadata
  beside `carryin_stocks_to_use`, as context that does not enter the arithmetic.
  No headline field is added, removed or recomputed: any movement in the reported
  figures for the committed sample input must be attributable to AC-3's data
  revision alone, and is reported as such.
- **AC-7** -- `transmission.json`'s `not_captured`, the README's matching list
  and the Modelfile's `not_for` each state that export demand shocks and trade
  policy are outside the transmission. All three, not one of the three.
- **AC-8** -- `check_price.py` gains checks covering AC-1, AC-5, AC-6 and the
  internal consistency of the share, and the whole suite passes. The existing 73
  checks all still pass and none is weakened to accommodate the new column.
- **AC-9** -- `Modelfile.toml` still validates with no annotation warnings,
  `validity_domain` within 600 characters and `provenance` within 400, and every
  key in a `required` array still has a declared `properties.<key>.type`.
- **AC-10** -- `docker build` from the bundle folder succeeds and
  `docker run --network none` reproduces the local run's output apart from
  `generated_at`. The build scripts stay out of the image.
- **AC-11** -- The bundle README documents the new columns, their source and
  their vintage in the committed-tables table, explains that exports sit inside
  total use, and states that the export figure is exposure context rather than
  an input to the price arithmetic.
- **AC-12** -- Every published figure that the refit moves is updated in the
  **same commit**: the bundle README's coefficient table, its R-squared and its
  sample-run and tenth-percentile figures, and `CLAUDE.md`'s "Verified results"
  block. If nothing moved, the pull request says so plainly. The 82.47% coverage
  share comes from the 2022 Census, not from ERS, and must not be touched.

## Constraints and dependencies

- **The ERS attribute string for exports must be read from the file.** The
  build script matches `row["attribute"]` exactly, and the plan must not guess
  the label. The 18 MB export is uncached in a fresh checkout, so the build
  refetches; that is expected and is a build-time action, never a run-time one.
- **Exports are inside total use.** `total_use_mil_bu` already includes them, so
  a lost-export scenario reduces use rather than adding to it. State this in the
  meta definitions and the README; it is the obvious way for a downstream
  consumer to double-count.
- **The marketing year is Sep-Aug and the current year is a WASDE projection**,
  already flagged `is_projection` and already excluded from both the trend fit
  and the transmission fit. Exports for that year inherit the same status and
  the metadata must say so.
- **Determinism is unchanged.** No network at run time; the new column is a
  committed table like the others, with its vintage in the meta file and copied
  into the output metadata.
- **Flow binding is by required-key set.** Adding keys inside the output's
  `metadata` object, which declares no sub-properties, cannot change what node
  3's outputs bind to. Confirm with `check_schema_compatibility` rather than
  asserting it.
- **This repo publishes numbers that look tradeable.** An export figure beside a
  price impact invites exactly the inference this bundle refuses to make. The
  output must not imply that node 3 has priced a trade scenario; it has not.
- Repo-wide conventions -- the Modelfile and runner contracts, the platform
  facts, unit discipline, modelling honesty, determinism, region identity and
  the USDA bulk data sources -- live in [`CLAUDE.md`](../../CLAUDE.md) and are
  not restated here.

## General guidance

- The brief's one blocking question -- what to do if the ERS refetch has
  revised the committed series -- was **answered by John on 2026-09-20: accept
  the revision and refit**, rather than pinning the feature to the committed
  vintage. AC-3, AC-4, AC-6 and AC-12 above are written to that decision, and it
  is recorded as D-1 in the plan. The mitigating fact, established while
  planning, is that `check_price.py` holds no hard-coded transmission constants:
  it reads `transmission.json` and `price_history.csv` and asserts relationships,
  so a refit validates on its own terms rather than against stale expectations.

- Read `build_price_history.py` and `price_history.meta.json` before writing the
  plan. The definitions block there is careful about what is endogenous to a
  season and what is predetermined, and the export column has the same question
  attached: exports respond to the price within the marketing year, so an export
  figure is not a clean exogenous conditioner and must not be presented as one.
- Verify by running, not by reading. The check suite, a local run against the
  committed sample and a `--network none` container run are the evidence.
- Keep the diff small and cohesive. This is a data and labelling change to a
  reviewed, merged bundle; anything that moves a published number is out of
  scope by construction.

## Downstream context: the node 4 trade-policy model

Recorded here so the reasoning is versioned with the data it motivates. None of
this is built by this brief.

**Why node 4 is a separate model, not a term in node 3.** Three reasons, in
descending order of force:

1. **The fit cannot carry it.** Fifty annual observations and a selection rule
   fixed at |t| >= 2.0. The relevant trade shocks are roughly three episodes --
   the 1980 Soviet embargo, the 2018-19 Chinese retaliation, 2025. A tariff term
   would fail the rule, and the repo's posture is to report that kind of failure
   as a finding rather than ship the term anyway.
2. **It breaks node 3's identity.** Node 3's contract is one upstream document
   plus committed tables, and its determinism claim rests on both. A tariff is
   not a function of weather, it changes intra-season on announcement, and a
   committed tariff table would have a vintage measured in weeks. Wiring it as a
   second input would also cost the zero-configuration default, since the
   platform requires every input of a non-first flow step to be wired -- the same
   objection the README already raises against promoting `reference_price_usd_bu`.
3. **The time base is wrong.** The transmission is fitted on a marketing-year
   average cash price. Weather accumulates over a season and an annual average
   summarises it; a tariff is a discrete jump that futures price within hours,
   and an annual average smears it into nothing. The right instrument is the
   within-season futures transmission already listed as future work, which is a
   further argument for a separate fit in a separate model.

**Shape.** Node 4 consumes node 3's `corn_price_impact`, binding on its required
key set `["generated_at", "metadata", "national", "regions", "assumptions"]` --
not `["metadata", "columns", "rows"]`, which collides with `corn_price_regions`
and with node 2's snapshot. Its second input is a **scenario** supplied per run
as an inline literal: bushels of export demand removed, or a share of a named
destination's purchases. It emits the combined implied impact with the weather
and trade components broken out separately.

**Illustrative arithmetic**, using this bundle's committed coefficient and the
2026 projection row, to show the channel is worth modelling: 300 mil bu removed
against 16,180 mil bu of total use is a 1.85% demand shock; times -0.7826 is
about -1.45%, or roughly -$0.07/bu on $4.80, with the committed bootstrap
interval giving -0.73% to -2.35%. That is several times the weather signal in
the committed sample run (-0.18%). The figure is an illustration of magnitude,
not a result, and it inherits every caveat below.

**The four problems node 4 has to solve honestly.**

- **Corn is not soybeans.** China's corn purchases are episodic -- near zero in
  most years, a large spike in 2020-22, then down again -- while Mexico and Japan
  are the steady buyers. The dominant China-tariff channel for corn is indirect:
  a soybean price fall shifts acreage to corn the following spring and pushes the
  corn price down with a one-year lag. A corn-only direct-sales model would miss
  the larger effect. These shares should be verified against the ERS and Census
  data rather than taken from memory.
- **The coefficient was fitted on supply shocks.** Applying it to a demand shock
  assumes symmetry. Post-harvest supply is near-vertical so it is not unreasonable,
  but it is an assumption that belongs in the output, not buried in a build
  script.
- **A tariff is not a one-for-one sales loss.** Trade reallocates; in 2018-19
  soybean flows largely rerouted rather than disappeared, so the bushel loss was
  far below the headline. The scenario input should therefore be *bushels not
  sold*, leaving the tariff-to-bushels step as an explicit assumption the user
  supplies rather than something the model pretends to compute.
- **Announcement and expectation.** Once a tariff is known it is in the price.
  Node 4 answers "what does this trade scenario imply against a no-tariff
  baseline", which is not "what happens next" -- the same implied-impact framing
  node 3 already takes, applied to a policy shock instead of a weather one.
