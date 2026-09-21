# Plan: Export exposure

Source brief: docs/features/0002-export-exposure.md
Status: implemented
Planned against commit: 175f6b5
Base commit: 175f6b5517e13e0b5c5df1a4b9435a81ed685fbe (branch feat/0002-export-exposure)

## Outcome

`corn-price/` carries the export side of the national balance sheet as committed
data, and states in all three places -- Modelfile `not_for`, output document and
README -- that export demand shocks and trade policy are outside its
transmission. A downstream node 4 can express a lost-sales scenario as a share
of US corn use against node 3's own vintage, and a reader of node 3's output can
see the export exposure the price impact sits on top of.

## Scope

### In scope

- `exports_mil_bu` and `export_share_of_use` columns in `price_history.csv`,
  from `Table 4--Corn: Supply and disappearance` in the ERS file already used.
- The current and latest-complete export exposure in `price_history.meta.json`,
  and a `definitions` entry stating exports are a component of total use.
- The exposure carried into the runner's output metadata, beside
  `carryin_stocks_to_use`, as context that does not enter the arithmetic.
- Trade-policy wording in `build_transmission.py`'s `not_captured`, the bundle
  README's matching list, and the Modelfile `not_for`.
- New checks in `check_price.py` for the columns, the meta, the output metadata
  and the internal consistency of the share.
- **Accepting any ERS revision** the refetch returns, refitting the
  transmission, and propagating every moved figure through the documentation.

### Out of scope

Unchanged from the brief: the node 4 model itself, any tariff or destination
data, any new transmission term, cross-commodity effects, the region set and
weights, and a live reference price.

## Assumptions and decisions

**D-1 -- ERS revisions are accepted, not pinned. Decided by John, 2026-09-20,
in answer to the brief's blocking question.** This **supersedes the brief's AC-3
and AC-4 as written**, which required that no existing value and no coefficient
change. `run` takes whatever the live ERS source returns, refits, and updates
every published figure that moves. **The brief was amended to this decision on
2026-09-20**, at John's instruction, after this plan was first written: AC-3 and
AC-4 were rewritten, AC-6 was loosened to permit revision-driven movement, and
AC-12 was added for the propagation sweep. The traceability table below matches
the amended brief.

**D-2 -- a revision is unlikely but must be handled anyway.** The committed
tables were built at 2026-09-20T05:21:22Z, which is the same day this plan is
written, so the refetch will very likely return identical data and no published
figure will move. The plan must not assume that. Both outcomes -- moved and
unmoved -- are reported explicitly in the pull request; silence is not a result.

**D-3 -- `latest_complete_marketing_year` stays 2025.** `build_price_history.py`
derives it as `now.year - 1` when the month is September or later. Run in
September 2026 that gives 2025, matching the committed meta. If `run` happens
after the turn of a year this must be re-checked, because it would silently move
a WASDE projection into the trend and transmission fits.

**D-4 -- carry both marketing years' exposure, not one.** The meta `defaults`
block is keyed to the current marketing year, which is a WASDE projection. For
exports the plan records both the current (projected) year and the latest
complete year, each with its own `marketing_year` and projection flag. A
downstream consumer choosing a denominator should not have to guess which one it
has, and the complete year is the one with an outcome behind it.

**D-5 -- the check suite needs no new expected values.** `check_price.py` holds
no hard-coded transmission constants: it reads `transmission.json` and
`price_history.csv` and asserts relationships, and the 2012 case reads
`price_rows[2012]` from the CSV with a 3-point tolerance. A refit therefore does
not require editing any expectation, which is the main reason accepting a
revision is a safe decision rather than a risky one.

**D-6 -- coverage share is unaffected.** 82.47% comes from the 2022 Census via
`build_weights.py`, not from ERS. `run` must not churn it, and must not rebuild
`production_weights.csv` or `yield_history.csv` at all.

**D-7 -- exports are endogenous within the marketing year.** They respond to the
price they are being reported beside, so the output metadata gives them the same
`role` treatment `carryin_stocks_to_use` already gets: context only, explicitly
not a conditioner of the transmission.

## Acceptance-criteria traceability

| ID | Acceptance criterion | Implementation | Verification | Status |
|---|---|---|---|---|
| AC-1 | `price_history.csv` carries `exports_mil_bu` and `export_share_of_use` for every marketing year, from Table 4 of the same ERS file and vintage | `build_price_history.py`, `price_history.csv` | `check_export_exposure`: both columns present, 52/52 rows populated, share 6.6%-32.8% | **pass** |
| AC-2 | The build uses the table's own attribute string, discovered from the export | `build_price_history.py` | attribute enumerated from the cached export: `'Exports'`, 52 rows in Table 4; `read_series` raises `SystemExit` on an unmatched attribute | **pass** |
| AC-3 | The table is rebuilt against the live ERS source; any revision is accepted and the diff reported | `price_history.csv`, `price_history.meta.json` | column-wise comparison against `HEAD`: **0 pre-existing cells changed**, 0 columns removed. **ERS had not revised anything** | **pass** |
| AC-4 | `transmission.json` rebuilt; whether any coefficient moved is stated explicitly | `build_transmission.py`, `transmission.json` | `shock` -0.7825520359332103 -> **-0.7825520359332104** (1 ulp). Isolated probe fitting the **pre-change** table on this host gives the same -...104, so the move is host/interpreter floating-point, **not** the data and **not** this change. `n` 50 and `r2` 0.328940679814806 identical. Nothing published to 4 dp moves | **pass** |
| AC-5 | The meta records exposure for both the current and latest complete year, plus a `definitions` entry | `price_history.meta.json` | 2026 projected 3275.0 bu / 0.20241; 2025 complete 3425.0 bu / 0.205336; both carry `is_projection`; `definitions.exports` states component-of-total-use | **pass** |
| AC-6 | The runner carries the exposure as context; no headline figure recomputed | `runner.py` | `metadata.export_exposure` present; sample run **-0.1801 [-0.2919, -0.0905]**, US shock 0.2303, coverage 0.824706 -- identical to baseline | **pass** |
| AC-7 | `not_captured`, the README list and the Modelfile `not_for` each name export demand shocks and trade policy | `build_transmission.py`, `corn-price/README.md`, `Modelfile.toml` | three checks assert each place; `not_for` names trade policy; README gained two bullets and an Export exposure section | **pass** |
| AC-8 | New checks pass and the existing 73 still pass, none weakened | `check_price.py` | **95/95 pass** (73 baseline + 22 new); no existing check edited except a corrected document path (see deviations) | **pass** |
| AC-9 | Modelfile still valid; `validity_domain` <= 600, `provenance` <= 400, every required key typed | `Modelfile.toml` | parses as TOML; 587 / 393 / `not_for` 579; existing check "every required key has a declared type" passes; input `required` still `[metadata, columns, rows]` | **pass** |
| AC-10 | `docker build` succeeds and `docker run --network none` reproduces the local run; build scripts stay out of the image | `Dockerfile` | image `/app` holds runtime files only (no build scripts, no `.ers-cache`); container output differs from local at **`.generated_at` only** | **pass** |
| AC-11 | README documents the columns, source, vintage, component-of-use and context-not-input | `corn-price/README.md` | new **Export exposure** section; committed-tables row names exports | **pass** |
| AC-12 | Every figure the refit moves is updated in the same commit; if nothing moved, say so | `corn-price/README.md`, `CLAUDE.md` | **nothing moved.** No coefficient, sample-run or R-squared figure changed at published precision. Only the check count was updated, 73 -> 95, in both documents. 82.47% coverage untouched | **pass** |

## Verification

| Command | Purpose | Baseline result | Final result |
|---|---|---|---|
| `uv run --python 3.12 --no-project python corn-price/runner.py corn-price/sample_input.json > run/corn_price_impact.output.json` | the model runs end to end on the committed sample | exit 0; -0.18% [-0.29, -0.09], US shock +0.23% | exit 0; **-0.1801 [-0.2919, -0.0905]**, US shock +0.2303 -- unchanged |
| `uv run --python 3.12 --no-project python corn-price/check_price.py run/corn_price_impact.output.json` | the 73 modelling checks plus the new ones | 73/73 pass | **95/95 pass** -- 22 added, 0 failed |
| `docker build -t ag-commodity-corn-price:local corn-price` | the bundle still builds from its own folder as context | not run at baseline | **succeeds**; image holds runtime files only |
| `docker run --rm --network none ag-commodity-corn-price:local` | determinism and the offline claim | not run at baseline | **identical to the local run apart from `.generated_at`** |
| column-wise diff of `price_history.csv` against `HEAD` | makes any ERS revision visible rather than implicit | n/a | **0 pre-existing cells changed**; ERS had not revised |

The Model Home Modelfile validator and `check_schema_compatibility` live in the
platform repository, not here. AC-9's validator run and the binding confirmation
are manual steps against the local stack; the plan does not invent a harness for
them.

## Implementation steps

1. **Baseline.** Run the runner and `check_price.py` on the committed tree and
   record 73/73 before changing anything. Capture the committed values of
   `shock`, its bootstrap interval, `n` and `r2` from `transmission.json`, and
   the sample run's headline figures, so any later movement is measurable.
2. **Discover the exports attribute.** Run `build_price_history.py` once to
   populate `.ers-cache/`, then read the distinct `attribute` values for
   `Table 4--Corn: Supply and disappearance` from the cached file. Use the exact
   string; do not assume it is `"Exports"`.
3. **Add the columns.** One more `read_series` call for exports, plus
   `exports_mil_bu` and the derived `export_share_of_use` in the row dict,
   following the existing formatting precision convention. Exports are a
   disappearance component of `total_use_mil_bu`, so the share is a fraction of
   a denominator that already contains it; say so in a comment.
4. **Extend the meta.** Add the current and latest-complete exposure per D-4 and
   a `definitions` entry per the brief. Keep the existing `defaults` keys
   untouched.
5. **Rebuild and diff.** Re-run `build_price_history.py`, then
   `git diff corn-price/price_history.csv`. Confirm the only changed cells are
   the two new columns, or, if ERS has revised, record exactly what moved.
   Re-run `build_transmission.py` and diff `transmission.json` the same way. The
   bootstrap seed is fixed at 20260919, so identical inputs give identical
   coefficients; any coefficient movement means the data moved, and is reported
   rather than absorbed.
6. **Runner metadata.** Add an `export_exposure` block beside
   `carryin_stocks_to_use` in the metadata dict, sourced from the meta file with
   a `role` string per D-7. Change no arithmetic and no headline field.
7. **Propagate, if anything moved.** Only if step 5 showed movement, update:
   `corn-price/README.md` lines around 203 (coefficient table), 206 and 361
   (R-squared), 314 and 328 (sample and tenth-percentile runs); `CLAUDE.md`
   lines 326-339 (the "Verified results" block); and the Modelfile
   `validity_domain`'s "about a third of year-on-year price variation" if the
   R-squared no longer supports that phrase. Leave the 82.47% coverage figure
   alone per D-6.
8. **Wording.** Sharpen `not_captured` in `build_transmission.py` to name export
   demand shocks and trade policy, re-run that script so the committed
   `transmission.json` carries the new text, and mirror the statement in the
   README's matching list and the Modelfile `not_for` within its cap.
9. **Checks.** Add the new checks to `check_price.py` under the existing
   `check_tables` and `check_contract` sections, following the file's `check(...)`
   convention.
10. **Verify.** Run the full verification table, including the Docker build and
    the `--network none` run, and record final results in the table above.
11. **Pull request.** Report the ERS diff, the coefficient before-and-after, the
    check count, and which of the two outcomes in D-2 actually occurred.

## Files likely to change

- `corn-price/build_price_history.py` -- exports series, derived share, meta keys
- `corn-price/price_history.csv` -- two new columns, regenerated
- `corn-price/price_history.meta.json` -- exposure and definitions, regenerated
- `corn-price/build_transmission.py` -- `not_captured` wording
- `corn-price/transmission.json` -- regenerated; new wording, new `built_at`
- `corn-price/runner.py` -- `export_exposure` in output metadata
- `corn-price/check_price.py` -- new checks
- `corn-price/Modelfile.toml` -- `not_for`, possibly `validity_domain`
- `corn-price/README.md` -- committed-tables table, `not_captured` list, and the
  published figures only if they moved
- `CLAUDE.md` -- the bundle file list, and "Verified results" only if it moved

## Risks and follow-ups

- **An ERS revision moves published coefficients.** Accepted by D-1. The
  mitigation is that it is measured, quantified and reported, never silent, and
  that D-5 means the check suite validates the new numbers on its own terms
  rather than against stale constants.
- **`built_at` timestamps change even with identical data**, in both
  `price_history.meta.json` and `transmission.json`. Expected and harmless, but
  it means `git diff` will never be empty; the review has to read past the
  timestamps to the values.
- **The 18 MB ERS fetch is a build-time action.** It must not leak into the
  image or the runner. `.dockerignore` already excludes the build scripts and
  the cache; confirm rather than assume.
- **An export figure beside a price impact invites the inference this bundle
  refuses to make.** The `role` string and the three-place labelling are the
  mitigation; a reviewer should check the output reads as exposure context and
  not as a priced trade scenario.
- **Follow-up:** node 4, the trade-policy model, in its own repo. The design is
  recorded in the "Downstream context" section of the brief.
- **Follow-up:** amending brief 0002's AC-3 and AC-4 to match D-1, if John wants
  the brief to read consistently with the decision.

## Outcome of D-1 and D-2

**The revision did not materialise.** The live ERS fetch returned data
byte-identical to the committed table: 0 pre-existing cells changed across 52
rows and 13 columns. D-1's accept-and-refit path was therefore exercised but
had nothing to absorb, and AC-12's propagation sweep found nothing to
propagate beyond the check count.

One movement is worth recording precisely, because AC-4 requires it. The
refitted `shock` coefficient is `-0.7825520359332104` against the committed
`-0.7825520359332103`: a difference of one unit in the last place, about 1e-16
relative. To establish the cause rather than assume it, the pre-change table was
fitted in isolation on this host and produced the **same** `-...104`. The
difference is therefore the host's floating-point arithmetic, not the data and
not this feature. Nothing published quotes the coefficient beyond four decimals,
so no documented figure changes.

Worth knowing for later: `transmission.json` is reproducible run-to-run on one
host, but **not bit-identical across hosts**, despite the fixed bootstrap seed.
The determinism claim in the README concerns the runner, which is unaffected,
but a future rebuild on another machine will show the same last-digit churn.

## Deviations from the plan

- **The runner's `role` string and one new check first pointed at
  `metadata.transmission.not_captured`, which does not exist.** The document
  carries the list at `assumptions.not_captured`. The new check caught it on
  first run; both were corrected, and a further check now asserts the role
  string names a path that actually resolves in the document.
- **Step 7 (propagation) was a no-op** beyond the check count, per D-2's
  unmoved branch.
- **`not_for` was trimmed.** A first draft reached 704 characters. `not_for`
  has no documented cap, but its neighbours are capped at 600 and 400, so it was
  shortened to 579 rather than risk the platform validator.
