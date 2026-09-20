# Plan: Corn price

Source brief: docs/features/0001-corn-price.md
Status: implemented, except AC-13 (blocked: the Model Home import needs a signed-in human)
Planned against commit: 5ef382a (chore: scaffold ag-commodity-bundles)
Base commit: 5ef382a (main at branch creation)

## Outcome

`corn-price/` is a self-contained Model Home model that takes a node 2
(`wofost-bundles/corn-yield/`) `corn_yield_snapshot` and returns the corn price
impact that season's weather implies: each state's simulated yield signal placed
on a real-world scale, combined by production weight into a national yield and
production shock, and the price response that shock implies under a fitted,
cited transmission -- every number beside its uncertainty. It makes no network
calls at run time.

Node 3 of the climate -> agriculture -> finance flow. Its input is node 2's
output unchanged. It is not a price forecast and not advice.

## Scope

### In scope

- The `corn-price/` bundle: `Modelfile.toml`, `Dockerfile`, `runner.py`, a
  committed sample input that is a real node 2 output, four committed tables,
  three one-time build scripts, and `check_price.py` run outside the image.
- Production weights and irrigation shares per `region_key` from the 2022 Census
  of Agriculture, the same file and vintage node 1 used.
- A real-scale rescaling of node 2's simulated anomaly by quantile mapping onto
  each state's observed detrended yield distribution.
- A national yield shock, a national production shock in bushels, and the
  ten-state coverage share, all reported explicitly.
- A fitted, stocks-conditioned transmission from yield shock to price change,
  with its prediction interval.
- Two JSON outputs following the repo convention.

### Out of scope

- Nodes 1 and 2, and the flow definition (composed in the platform; no
  Flowfile).
- Re-deriving anything node 2 produces. No crop model here.
- Trading signals, position sizing, or investment advice.
- A run-time price fetch (D1), the SPA, and redefining regions.

## Assumptions and decisions

The brief asked three blocking questions. All three were researched, put to John
with evidence on 2026-09-19, and answered; his answers are recorded in D1, D3 and
D4. The remaining decisions follow from them or from reading the platform and
upstream source.

### D0. What the research found before any decision was taken

Three measurements drove everything below. All were computed, not assumed.

**(a) The ten states are 82.5% of US corn, not 85-90%.** Summing
`CORN, GRAIN - PRODUCTION, MEASURED IN BU` for the ten `region_key` states in
`qs.census2022.txt.gz` against the national total gives **82.47%**. The brief's
estimate was optimistic; the real figure is what ships.

**(b) The irrigation bias is measurable from the same file.**
`CORN, GRAIN, IRRIGATED - ACRES HARVESTED` over `CORN, GRAIN - ACRES HARVESTED`,
2022 Census:

| region | ia | il | mn | ne | in | sd | oh | wi | ks | mo |
|---|---|---|---|---|---|---|---|---|---|---|
| irrigated share of harvested corn acres | 1.2% | 3.3% | 3.9% | **52.7%** | 6.3% | 3.5% | 0.5% | 4.7% | **25.4%** | 9.4% |

**(c) Node 2's anomaly is 3-10x more dispersed than a real yield anomaly.** This
is the finding that reshaped the node, and it is not in the brief. From node 2's
own `baseline_yields.csv`:

| region | min | p10 vs median | median kg/ha | max vs median |
|---|---|---|---|---|
| ia | 81 | -62% | 8,238 | +63% |
| il | 8 | -66% | 7,631 | +60% |
| ne | 24 | -90% | 3,936 | +170% |
| sd | 221 | -62% | 3,098 | +267% |
| ks | 0 | -94% | 1,491 | +289% |

Real state corn yields deviate from trend by roughly +/-25% in an extreme year.
Node 2's `yield_anomaly_pct` is the anomaly of an uncalibrated, rainfed,
single-point simulation with a very live water balance, and it over-disperses by
a factor. Node 2's own sample run already reports ia **+22.0%** and ne **+51.9%**
in a year nobody considered remarkable. Iowa alone carries an 18.3% production
weight, so +22% contributes **+4.0 percentage points** of national yield shock;
through a -1.84 price coefficient that is **-7.4% on corn from one state, in an
ordinary year**. Multiplying `yield_anomaly_pct` straight through any
transmission is wrong by a factor, not by a margin. D4 is the response.

### D1. Node 3 stays strictly offline (answered by John)

**Decision.** No run-time network call. The headline output is a **percentage**
price impact, which needs no price level at all. The dollar translation uses a
committed price table's latest value, overridable by an optional
`reference_price_usd_bu` input, with the table's vintage stamped in the output
metadata. Node 3 is therefore a pure function of its input plus committed
tables, the same determinism category as node 2, and it runs under
`--network none`.

**Why not `yfinance-bundles/historical-ohlcv`.** It was evaluated as the brief
asked. Three reasons against, in order of weight: it fetches Yahoo Finance at
run time, which would move node 3 out of node 2's determinism category for a
number that is cosmetic to the core output; continuous front-month `ZC=F` has
roll artifacts that make it a poor reference price; and the bundle is
old-generation -- legacy `[runner]` / `[build]` blocks, no `determinism` /
`validity_domain` / `not_for` / `provenance` annotations, and a literal typo in
its output schema (`properties.ticrowsker.type`). It would need rebuilding to
current conventions before it could be composed with anything.

**Recorded as future work in the bundle README** (John's instruction): the two
ways node 3 could take a live price later, and what each costs. First, composing
with a rebuilt `yfinance-bundles/historical-ohlcv` as an earlier flow step --
`flow_service._normalize_steps` already resolves each input independently
against *any* earlier step, so a second input binds with no change to node 3;
the cost is modernising that bundle's Modelfile and accepting a non-deterministic
upstream. Second, a run-time fetch inside node 3 itself, which would require
stamping `retrieved_at`, the source and the endpoints in the output metadata the
way node 1 does, and rewording the `determinism` annotation. Third, and needing
no code at all today: the flow editor's `inline` and `url` input bindings can
already feed `reference_price_usd_bu` a current number.

### D2. Committed tables, and where they come from

Four committed tables, three build scripts, one `*.meta.json` each. Every
`meta.json` records the source URL, the server-side `last-modified`, the period
covered and the build timestamp, and the runner copies those vintages into its
output metadata.

| Table | Built by | Source | Contents |
|---|---|---|---|
| `production_weights.csv` | `build_weights.py` | `nass.usda.gov/datasets/qs.census2022.txt.gz` (~310 MB, keyless, stable filename -- **the same file and vintage node 1 used**) | per `region_key`: `state`, `acres_harvested`, `acres_irrigated`, `irrigated_share`, `production_bu_2022`, `production_share_of_us`, `method`, `source` |
| `yield_history.csv` | `build_yield_history.py` | `nass.usda.gov/datasets/qs.crops_<YYYYMMDD>.txt.gz` (~1.1 GB, keyless) | per `region_key` per year 1995-2024: `yield_bu_acre`, fitted `trend_yield_bu_acre`, `deviation_pct`; plus per-region trend coefficients |
| `price_history.csv` | `build_price_history.py` | USDA ERS Feed Grains Yearbook Tables -- All Years (primary candidate; **confirm the exact file URL at build time**, fall back to the NASS bulk series) | national annual: `year`, `us_yield_bu_acre`, `us_yield_deviation_pct`, `price_usd_bu` (marketing-year average received), `ending_stocks_bu`, `total_use_bu`, `stocks_to_use` |
| `transmission.json` | `build_transmission.py` | fitted from `price_history.csv` | the fitted coefficients, standard errors, R^2, residual standard deviation, sample period and the specification string |

Notes that bit node 1 and will bite here:

- `(D)` in a NASS export means the value was **withheld for disclosure**. Treat
  it as missing and say so in the `method` column; never as zero.
- The survey bulk file's name **carries a date that changes**
  (`qs.crops_20260919.txt.gz` today). The build script discovers the current
  name from the directory listing and records it in `meta.json` rather than
  hard-coding one.
- The Quick Stats **API returns 401 without a key**; the bulk files do not need
  one. Use the bulk files. Caches go in `.nass-cache/` and `.ers-cache/`, both
  already git-ignored; the committed artifact is the CSV.
- `build_transmission.py` runs **once** and commits its fit. The runner never
  fits anything, which is what keeps it deterministic and in the "seconds"
  runtime class.

### D3. The transmission: fitted on a matching regressor, stocks-conditioned (answered by John)

**The finding that drove the question.** The two obvious off-the-shelf options
are both wrong for this input, in opposite directions.

*Roberts and Schlenker (2013, AER 103(6), 2265-95)* is the canonical
identification of agricultural supply and demand elasticities using yield shocks
as the instrument. Their Table 1 (FAO data) gives supply elasticity
**0.087 to 0.116** and demand elasticity **-0.028 to -0.066**, implying a price
multiplier `1/(beta_s - beta_d)` of **5.75 to 7.73**. But that multiplier is
derived for a *permanent* shift in the world caloric aggregate -- the ethanol
mandate -- and the paper's own storage discussion explains why a transitory
shock is smoothed instead. Applying ~6x to a single-season US weather shock
overstates it by roughly three to four times.

*farmdoc daily (2018-11)* regresses the percent change in December corn futures
on the percent change in national corn yield from the May to the November WASDE,
1993-2017: coefficient **-1.84**, R^2 **0.48**, significant at 99%. That is the
right kind of object -- US corn, within-season, transitory, and it already
embeds storage and the demand response. But its regressor is the yield surprise
against the *May WASDE*, that is, against market expectation, whereas node 2's
anomaly is measured against a thirty-year *weather* median. Those are different
quantities, and -1.84 cannot honestly be applied to the second.

**Decision.** Fit our own reduced-form relationship on USDA annual data, so the
regressor is defined the same way as the shock node 3 actually computes:

```
d_log_price_t = (a + b / stocks_to_use_t) * yield_deviation_pct_t + controls + e_t
```

estimated over the longest period `price_history.csv` supports, with
`yield_deviation_pct` the national yield's deviation from a fitted trend -- the
same construction, on the same detrending, that the regions get in
`yield_history.csv`. The `1 / stocks_to_use` interaction is the standard convex
form: the relationship between stocks-to-use and price is well documented as
non-linear, with prices responding far more sharply below roughly 10% stocks-to-
use. `stocks_to_use` is a declared optional input defaulting to the latest value
in the committed table, so the model needs no configuration but the tight-versus-
loose-year effect is captured and overridable.

**Uncertainty.** The shipped interval is the fit's **prediction interval** at the
computed shock, not a confidence interval on the mean, because the question the
output answers is "what price move does this shock imply", not "what is the
average relationship". The fit's R^2, residual standard deviation, sample period
and specification string all travel in the output metadata. On roughly thirty to
sixty annual observations with an interaction, the interval will be wide. That is
the honest result and it is not to be narrowed by tuning.

**Recorded in the bundle README** (John's instruction): the two alternatives and
why they were not chosen -- the published farmdoc constant, with its
regressor-mismatch problem stated plainly, and the Roberts-Schlenker structural
band, with its permanent-versus-transitory problem stated plainly. Roberts and
Schlenker's multiplier also ships in the output metadata as a cited reference
point, so a reader can see the structural number beside the fitted one. It is
never the headline.

**What this does not capture**, and must be said in the README and in the
`validity_domain`: cross-commodity substitution (soybean and wheat prices move
too, and corn acreage responds next season); the ethanol and export demand
channels separately, which the reduced form only sees in aggregate; basis and
the futures curve, since the fit is on a cash marketing-year price; policy
shocks; and anything about *when* within the season the price moves.

### D4. Quantile mapping, driven by the percentile rank (answered by John)

**Decision.** `yield_percentile_rank` drives the calculation;
`yield_anomaly_pct` is carried as context.

For each region, take node 2's percentile rank and read the same quantile of that
region's **real** detrended yield deviation distribution from
`yield_history.csv`, by linear interpolation between the empirical order
statistics:

```
yield_anomaly_real_pct[s] = quantile(real_deviation_pct[s], yield_percentile_rank[s] / 100)
```

This is standard quantile-mapping bias correction -- a quantile of the simulated
distribution is replaced by the same quantile of the observed one -- applied to
yields rather than to the weather. It is the right tool for D0(c) because the
problem is distributional, not a level offset: node 2's distribution is both far
too wide and strongly skewed, and no single scale factor fixes both.

It also does most of the irrigation work of AC-8 **for free**, which is why it
beats a bolt-on irrigation weight: Nebraska's and Kansas's *real* yield
distributions already contain their irrigated acres, so mapping a rank onto them
compresses the overstated dryland response to the spread those states actually
show. The `irrigated_share` column still ships per region, and the output reports
each region's simulated anomaly, its mapped anomaly and the ratio between them,
so the size of the correction is visible rather than implicit.

**The period aligns.** Node 2's baseline distribution is 1995-2024 and
`yield_history.csv` is built on 1995-2024, so rank *n* of thirty means the same
thing on both sides. If either period moves, the other must move with it; the
runner checks the two periods match and refuses to run otherwise, the same way
node 2 refuses to run against a mismatched crop-parameter commit.

**What it does not fix**, to be stated in the README: the mapping assumes node
2's *rank* is informative even where its magnitude is not. In a heavily irrigated
state a rainfed simulation can rank a dry year far too low, and the mapping will
faithfully carry that wrong rank onto the real distribution. Residual irrigation
bias in NE and KS therefore survives, reduced but not removed. The percentile is
also discrete over thirty baseline years, so it has about 3.3-point granularity.

### D5. Weights: harvested acres times trend yield

Combining *relative* yield anomalies into a national relative anomaly requires
**production** weights, not area weights: the national anomaly is the
production-weighted mean of the regional ones. But 2022 actual production embeds
2022's own weather, and 2022 was dry in the west -- exactly the states the model
is already careful about. So the weight is

```
w[s] = acres_harvested[s] (2022 Census) * trend_yield[s](year)
```

normalised over the regions present in the input, with `trend_yield[s](year)`
evaluated from the per-region trend fitted in `yield_history.csv`. This is also
where the brief's trend requirement lands: node 2's baseline is fixed-weather by
design and carries no genetics or management gain, so the trend belongs here,
applied to a real USDA level. The raw 2022 production share ships alongside as a
cross-check, and the two should not differ much; the check asserts they do not
differ wildly.

### D6. The national shock, and not scaling to 100%

Three figures ship, and the distinction between them is the point:

1. `ten_state_yield_shock_pct` -- the production-weighted mean of the mapped
   regional anomalies. The primary number.
2. `ten_state_production_shock_bu` -- that shock applied to the ten states' trend
   production.
3. `us_production_shock_pct` -- the same shock as a share of **US** production,
   under the explicitly named assumption that the uncovered 17.5% of US corn is
   at trend. The assumption is a labelled field in the output, not a footnote.

The coverage share is computed by the build script from the census and carried
into the output. The national figure is never silently scaled to 100%, and there
is no code path that does so.

### D7. Two JSON outputs

| Output | `required` | Content |
|---|---|---|
| `corn_price_impact` | `generated_at`, `metadata`, `national`, `regions`, `assumptions` | the headline: the national shock, the price impact with its interval, the per-region contributions, and every assumption as data |
| `corn_price_regions` | `metadata`, `columns`, `rows` | the per-region table in the familiar long shape, for a table view or a future step |

`corn_price_impact` is the stdout redirect because it is the headline;
`corn_price_regions` is the `{output:...}` arg. Their required-key sets differ,
so a downstream binding is unambiguous under the platform's subset rule. A CSV
sidecar is written for off-platform use only and the README says it is discarded
on-platform.

Every price and shock figure ships as three flat fields -- `<name>`,
`<name>_low`, `<name>_high` -- all three in the same `required` list, so AC-9 is
enforced by the schema rather than by discipline. `assumptions` carries the
transmission specification, the sample period, R^2, the stocks-to-use used and
where it came from, the coverage share, the uncovered-states assumption, the
table vintages, and the not-a-forecast statement as a string field.

### D8. Inputs

One bound input plus two optional scalars. Keeping it to two is deliberate: this
model should need no configuration in a flow.

- `corn_yield_snapshot`, `required = ["metadata", "columns", "rows"]`, which
  binds node 2's snapshot and cannot bind its trajectory. No schema `default`:
  a runnable example is a full snapshot, too large to paste, and in a flow the
  upstream supplies it. Declare `rows.items` properties in full, because the
  platform only compares types it is given.
- `reference_price_usd_bu`, `required = []`, defaulting to the committed price
  table's latest value.
- `stocks_to_use`, `required = []`, defaulting to the committed table's latest
  value.

Per repo convention a present-but-empty value falls back exactly as a missing key
does.

### D9. Region join, and failing loudly

The region set comes from the input. Every distinct `region_key` in the input
rows requires a row in `production_weights.csv` and a full 1995-2024 series in
`yield_history.csv`. A missing key exits non-zero naming the key and the table,
with no traceback. Regions are never silently dropped and node 3 never invents a
key.

### D10. Sample input

`sample_input.json` is a real node 2 `corn_yield_snapshot`. Node 2's committed
run covers only `ia` and `ne`, and AC-11 wants the full set, so the plan's first
implementation step is to generate a **real ten-region** node 1 -> node 2 run and
commit its snapshot. That also closes node 2's own outstanding follow-up ("a full
ten-region node 1 output should be run once before the model goes on a
schedule"). If the ten-region run cannot be produced, fall back to node 2's
committed two-region output and record AC-11 as partial evidence, exactly as node
2 recorded its AC-9.

## Acceptance-criteria traceability

| ID | Acceptance criterion | Implementation | Verification | Status |
|---|---|---|---|---|
| AC-1 | Repo scaffold matching siblings | already on `main` at 5ef382a: `.gitignore`, `.dockerignore`, `LICENSE`, `README.md`, `CLAUDE.md`, `.claude/skills/feat/` | `ls`; vendored `feat` diffs identical to `wofost-bundles` | **pass** |
| AC-2 | Bundle files and tables present | `corn-price/`: Modelfile, Dockerfile, runner, 3 tables + 1 fit + 3 meta files, 4 build scripts, check, sample, README | `ls corn-price` | **pass** |
| AC-3 | Sample input runs end to end | `sample_input.json` = a real ten-region node 2 snapshot from a live node 1 -> node 2 chain | runner writes both outputs; 10 rows; under a second | **pass** |
| AC-4 | Docker build and run reproduce it | `Dockerfile`, python:3.12-slim, no pip layer | `docker run --network none`: whole document **identical** to local apart from `generated_at` | **pass** |
| AC-5 | Weights keyed on node 1's key; coverage share from the source | `build_weights.py`, `production_weights.csv` | `check_price.py`: ten keys, shares equal production/US total, coverage **82.47%** in a sane band, ne/ks most irrigated | **pass** |
| AC-6 | Regional anomalies combine by a documented weighting | `process()` weighting (D5, D6) | `check_price.py`: weights sum to 1, contributions sum to the covered shock, US = covered x coverage, hand-worked three-region case | **pass** |
| AC-7 | Simulated anomaly placed on a real scale | `quantile_map` (D4, revised by C1) | `check_price.py`: monotone, bounded, rank 50 -> exactly 0, dispersion ratio **> 2 in every region** (3.07x-11.66x), unmapped +3.70% vs +0.23% used | **pass** |
| AC-8 | Rainfed bias handled and visible | D4 plus `irrigated_share` and `dispersion_ratio` per region | `check_price.py`: the two most over-dispersed regions are the two most irrigated (ne 11.7x/52.7%, ks 9.9x/25.4%) | **pass** |
| AC-9 | Cited transmission; every number with an interval | `transmission.json`, `price_impact` (D3, revised by C2) | `check_price.py`: b0 < 0, bootstrap CI excludes 0, every headline has `_low`/`_high` bracketing it, bad season -> +7.91%, **2012 recovers -22.04% vs actual -22.23%** | **pass** |
| AC-10 | Unknown key fails loudly | table loaders, `process()`, `check_periods` | `check_price.py`: unknown `zz` exits 1 naming key and table, no traceback; window mismatch exits 1 naming both windows | **pass** |
| AC-11 | Full region set, no parameters | `required = []` on the one optional input | the ten-region sample runs on the document alone; a two-region subset also runs and reports 28.8% coverage | **pass** |
| AC-12 | Annotations honest and within limits | `Modelfile.toml`, output `assumptions` | `validate` -> **OK**; validity_domain 587/600, provenance 393/400; not_for and the output both say not a forecast / not advice / not a signal; every required key typed; no required field null | **pass** |
| AC-13 | Model Home import works | `Modelfile.toml` | `check_schema_compatibility`: **binds** `corn_yield_snapshot`, **refuses** `corn_yield_trajectory`. Import itself not run -- needs a signed-in human | **blocked** |
| AC-14 | README documents every choice | `corn-price/README.md` | covers the input contract, all four tables with sources and vintages, the coverage share, the rescaling, the irrigation handling, the transmission with both rejected alternatives, the uncertainty construction, 7 limitations and the three live-price routes | **pass** |

## Verification

This repo has no test framework, matching its siblings; the convention is one
committed check script per bundle, run outside the image. There is no baseline to
record because there is no prior code.

| Command | Purpose | Baseline result | Final result |
|---|---|---|---|
| `python corn-price/runner.py corn-price/sample_input.json run/corn_price_regions.output.json > run/corn_price_impact.output.json` | the model runs standalone (AC-3) | n/a (new code) | **pass** |
| `python corn-price/check_price.py run/corn_price_impact.output.json` | every modelling assertion (AC-5 to AC-10, AC-12) | n/a (new code) | **pass** |
| `cd corn-price && docker build -t ag-commodity-corn-price:local .` | the image builds from the bundle context (AC-4) | n/a (new code) | **pass** |
| `docker run --rm --network none -v "$PWD/run:/run" ag-commodity-corn-price:local ...` | no network needed; output identical to local (AC-4) | n/a (new code) | **pass** |
| `uv run python -m orchestration.modelfile validate .../corn-price/Modelfile.toml` | Modelfile valid, no annotation warnings | n/a (new code) | **pass** |
| `check_schema_compatibility(node3 input, node2 outputs)` | the flow actually binds (D8) | n/a (new code) | **pass** |

The three build scripts need network; the model itself does not. There was no
pre-existing code, so no baseline could regress.

## Implementation steps

1. **Generate the sample input** (D10). Run node 1 for all ten regions, feed it
   to node 2, and commit node 2's ten-region `corn_yield_snapshot` as
   `corn-price/sample_input.json`. Fall back to node 2's committed two-region
   output and record partial evidence if that run cannot be completed.
2. **`build_weights.py`** (D2). Read the cached `qs.census2022.txt.gz`, take the
   four state-level series, join to node 1's `regions.csv` keys, compute the
   irrigated share and the coverage share, and write `production_weights.csv`
   plus its `meta.json`. Treat `(D)` as missing.
3. **`build_yield_history.py`** (D2). Discover the current
   `qs.crops_<YYYYMMDD>.txt.gz` name, pull annual state corn yields 1995-2024 for
   the ten states, fit a per-state trend, and write `yield_history.csv` with
   `deviation_pct` per year plus the trend coefficients, and its `meta.json`.
4. **Sanity-check the yield history** before going further: thirty rows per
   region, deviations centred near zero, and the real dispersion **materially
   narrower** than node 2's simulated dispersion for every region. If that last
   check fails, D4's whole premise is wrong and the work stops for a decision.
5. **`build_price_history.py`** (D2). Confirm the ERS Feed Grains Yearbook file
   URL; fall back to the NASS bulk series if it has moved. Write
   `price_history.csv` and its `meta.json`.
6. **`build_transmission.py`** (D3). Fit the stocks-conditioned specification,
   write `transmission.json` with coefficients, standard errors, R^2, residual
   standard deviation, sample period and specification string. Report the fit in
   the plan and in `CLAUDE.md`.
7. **`runner.py` -- input.** Parse node 2's `{metadata, columns, rows}`, read the
   reporting date from `metadata`, join the tables with the loud failure of D9,
   resolve the two optional inputs against their defaults, and assert the
   1995-2024 period match of D4.
8. **`runner.py` -- mapping.** The quantile map of D4, with the simulated
   anomaly, the mapped anomaly and their ratio retained per region.
9. **`runner.py` -- aggregation.** The weights of D5 and the three national
   figures of D6.
10. **`runner.py` -- transmission.** Apply `transmission.json` with the
    stocks-to-use in force, producing central, low and high for the percentage
    impact and, via `reference_price_usd_bu`, for the dollar impact.
11. **`runner.py` -- output.** The two documents of D7 plus the CSV sidecar;
    stdout carries only `corn_price_impact`, logs to stderr.
12. **`Modelfile.toml`** (D8). One bound input with
    `required = ["metadata", "columns", "rows"]` plus two optional scalars with
    `required = []`; two outputs; every annotation field; `validity_domain` under
    600 characters and `provenance` under 400; a `not_for` that says plainly this
    is not a forecast and not advice.
13. **`Dockerfile`.** `python:3.12-slim`, one pinned `pip install
    --no-cache-dir` layer, `COPY` of the runner, the four tables, the meta files
    and the sample, `ENTRYPOINT`, `CMD ["sample_input.json"]`. The three build
    scripts and `check_price.py` are **not** copied in.
14. **`check_price.py`.** All of AC-5 to AC-10 and AC-12, including the D4
    compression-factor signal check and the 2012 episode of AC-9.
15. **`corn-price/README.md`** covering the full AC-14 list, including the D1
    future-work paragraph (rebuilt yfinance, a run-time fetch, and the inline /
    URL binding available today) and the D3 alternatives paragraph (the farmdoc
    constant and the Roberts-Schlenker band, each with the reason it was not
    chosen), both of which John asked for explicitly.
16. **Top-level `README.md`**: confirm the bundle table row reads correctly once
    the outputs are final.
17. **Run every verification command** and record results in the table above and
    in a "Verified results" section in `CLAUDE.md`, matching how
    `wofost-bundles` records them.
18. **Open the pull request.** Stop there.

## Files likely to change

```
corn-price/Modelfile.toml            new
corn-price/Dockerfile                new
corn-price/runner.py                 new
corn-price/build_weights.py          new (not in the image)
corn-price/build_yield_history.py    new (not in the image)
corn-price/build_price_history.py    new (not in the image)
corn-price/build_transmission.py     new (not in the image)
corn-price/check_price.py            new (not in the image)
corn-price/production_weights.csv    new (built)
corn-price/yield_history.csv         new (built)
corn-price/price_history.csv         new (built)
corn-price/transmission.json         new (built)
corn-price/*.meta.json               new (built)
corn-price/sample_input.json         new (a real node 2 output)
corn-price/README.md                 new
CLAUDE.md                            verified-results and task-list sections
README.md                            bundle table row, if the outputs change
docs/plans/0001-corn-price.md        status, base commit, verification results
```

Note: the plan lists four build scripts against the three named in D2; the
transmission fit is the fourth and is counted with them.

## Risks and follow-ups

- **The quantile map is the load-bearing assumption**, exactly as the soil table
  was in node 2. It fixes the magnitude but inherits the rank. If node 2's
  rainfed simulation misranks a season in an irrigated state, node 3 carries that
  error faithfully. The step-4 sanity check and the step-14 compression-factor
  check are what stop a silent regression to raw anomalies; neither can detect a
  wrong rank.
- **Residual irrigation bias in NE and KS survives.** Mapping compresses it; it
  does not remove it. An irrigation-conditioned node 2 run is the real fix and
  belongs in node 2, not here. Named as follow-up in both READMEs.
- **The transmission is fitted on roughly thirty to sixty annual observations
  with an interaction term.** The prediction interval will be wide and the
  stocks-to-use coefficient may not be individually significant. If it is not,
  ship the constant-coefficient version and say so; do not keep a term the data
  does not support.
- **The ERS Feed Grains file URL is unconfirmed.** ERS discontinued the custom
  query and the CSV in May 2025 and moved to yearbook tables in January 2026, so
  step 5 begins by confirming the current location. The NASS bulk series is the
  fallback and the plan does not depend on which wins.
- **The survey bulk export is 1.1 GB** and its filename moves. Cached and
  git-ignored, run once, but it makes `build_yield_history.py` slow and
  non-reproducible by filename alone; the `meta.json` records what was actually
  read.
- **The 2012 validation episode is one observation.** Reproducing it inside the
  interval is weak evidence, not strong. It is in AC-9 because a model that
  cannot get 2012 roughly right is certainly wrong, not because getting it right
  proves much.
- **AC-13 needs a signed-in human.** The local stack is behind Auth0, exactly as
  node 1's AC-9 and node 2's AC-10 were; this step is handed to John and the pull
  request is a draft until it passes.
- **Two nodes now depend on the 1995-2024 window.** Node 2's baseline and node
  3's yield history must move together. The runner's period check makes a
  mismatch a failed run rather than a wrong answer, but it is a coupling worth
  remembering.

## Deviations and conflicts found during run

Everything in the plan held except D3's interaction term, which the data refused;
the plan anticipated that and said what to do, so `run` did not stop.

### C1. The stocks-to-use interaction is not supported, and the plan said so

D3 specified `(b0 + b1 / c) * d` and added: "If it is not [significant], ship the
constant-coefficient version and say so; do not keep a term the data does not
support." It is not significant. Fitted exactly as specified,
`b1 = +0.0266` with `t = +0.33` -- and the sign is wrong as well, implying a
*smaller* response at tight stocks. After the C2 term was added, every
stocks-to-use form tried came out at `|t| < 0.1`:

| candidate | coefficient | t | kept |
|---|---|---|---|
| `d[t-1]` (C2) | +0.9691 | **+3.42** | yes |
| `d / carryin` | -0.0012 | -0.02 | no |
| `d * log(carryin)` | -0.0042 | -0.01 | no |
| `log(carryin)` | +0.0025 | +0.05 | no |

**This is reported as a finding, not buried as an omission.** It is not a
refutation of the well-documented convex stocks-to-use relationship: that
relationship is about the price *level*, and this dependent variable is a
year-on-year *change*, which differences it away. `transmission.json` carries the
whole selection trail, and the output's `assumptions.stocks_to_use_finding` says
it in the document itself.

**Consequence.** The plan's second optional input, `stocks_to_use`, is **gone**.
An input that no longer feeds anything would be a false affordance. The carry-in
ratio still ships in the output metadata as context, labelled as context. The
model has one optional input, `reference_price_usd_bu`.

### C2. A lagged shock term was added, which the plan did not have

With a differenced dependent variable, last season's shock mechanically enters
this season's change: a short crop last year raised last year's price, so this
year's change is positive for reasons that have nothing to do with this year's
weather. Omitting it left that variance in the residual. Adding it takes R^2 from
0.162 to **0.329** and sharpens the shock coefficient (from -0.941, se 0.309, to
-0.783, se 0.283).

It is a **control**, not part of the answer. What the model reports is the
marginal effect `b0 * d` -- the price response to this season's shock holding the
rest fixed -- which is what "the impact implied by this shock" means and is why
the output is explicitly not a predicted price change. Selection used the same
pre-stated `|t| >= 2.0` rule as every other candidate.

### C3. The interval is a bootstrap on the coefficient, not a prediction interval

The plan specified "the fit's **prediction interval** at the computed shock".
Two problems surfaced. A parametric prediction interval needs a t quantile and a
normal-errors assumption that fifty annual observations of a fat-tailed price
series do not justify. More importantly it answers the wrong question: a
prediction interval is a range for *what the price will do*, which this model
explicitly does not claim.

What ships is a **nonparametric pairs bootstrap 95% interval on `b0`** (10,000
draws, fixed seed 20260919): `[-1.269, -0.393]`. It says how well the
transmission itself is known, which is the quantity the model is actually
asserting. The fit's R^2, residual s.d., n and sample period all ship beside it
so a reader can see that most price variation is outside the model.

### C4. The quantile map is median-centred, which the plan did not specify

Implemented as the plan wrote it -- read the quantile straight off the observed
detrended distribution -- a rank of 50 mapped to **+2.85% in Illinois and +2.40%
in Ohio**, not to zero, so an entirely unremarkable season implied a price
impact. The cause is legitimate and structural: a least-squares trend through a
left-skewed yield series sits below the median.

It is also inconsistent with node 2, whose anomaly is median-relative by
construction (it divides by the median of its thirty baseline runs), so its rank
50 *means* zero anomaly. The mapping must preserve that. The mapped value is now
`quantile(observed, p) - median(observed)`.

This does not disturb the transmission: the reported effect is marginal, so only
the *scale* of the deviation matters and shifting its origin changes nothing
except making an average season report zero. `check_price.py` asserts rank 50
maps to exactly 0.

### C5. `dispersion_ratio` replaced the plan's per-observation compression factor

The plan's AC-7 check was "the compression factor exceeds 1 for **every**
region", with the factor as `|simulated| / |mapped|` per observation. Implemented,
that came out at **0.67 for Illinois and 0.20 for Ohio** -- below 1, failing the
check -- because when a simulated anomaly happens to land near zero the ratio is
small regardless of how over-dispersed the simulation is. The per-observation
ratio is noise, and the check as written would have failed on a correct model.

What ships instead is a **distributional** ratio: node 2's own p10-to-p90 baseline
spread, as a percentage of its median, over the same spread of the observed
distribution. It is a property of the two distributions rather than of this
season, it is above 1 for every region by a wide margin (**3.07x to 11.66x**),
and it is the right invariant to assert. Node 2 publishes its baseline quantiles
in the input metadata, so this is computed from the document in hand rather than
from a second committed copy of node 2's numbers that could drift.

### C6. `us_yield_shock_pct_unmapped` was added

Not in the plan. The rescaling is the single biggest modelling decision in the
node, and it is invisible in a finished document unless the alternative is shown
beside it. The national block now carries what the figure would have been using
node 2's percentages directly: on the committed sample, **+3.70% against the
+0.23% used**. It is never consumed; it exists so the decision is auditable from
the output alone.

### C7. Minor deviations, already applied

- **No pip install layer in the Dockerfile.** The whole model is weighted sums,
  an empirical quantile and one committed coefficient. Nothing needed numpy or
  pandas, and the repo's rule is that a dependency needs a reason. The image is
  `python:3.12-slim` and the runner.
- **`check_price.py` needs Python 3.11 or newer**, because it reads
  `Modelfile.toml` with `tomllib` to assert the annotation limits and that every
  required key is typed. It exits 2 with the exact `uv run --python 3.12` command
  on an older interpreter rather than failing obscurely. The runner itself is
  version-agnostic.
- **A fourth build script.** D2 named three; the transmission fit is the fourth
  and was always implied by D3.

### C8. Findings in the upstream data, recorded so the next bundle does not rediscover them

- **The NASS survey bulk export contains NUL bytes** in trailing fields on about
  three lines in four, which makes Python's `csv` module raise
  `_csv.Error: line contains NUL` on the second row. `build_yield_history.py`
  strips them. The 2022 Census export node 1 used has none, which is why node 1
  never hit this.
- **Kansas has no corn yield trend** over 1995-2024: slope **-0.29 bu/acre/year**,
  R^2 **0.03**, against +1.9 to +2.8 everywhere else. Checked against the raw
  series (124 bu/acre in 1995, 129 in 2024, peak 155 in 2009) and it is real, not
  a parsing error -- Kansas's corn area has shifted and it is the most
  drought-exposed state in the set. The trend still ships; `r2` is in the meta
  file so a reader can see it is not significant, and Kansas carries a 3.5%
  weight so it moves nothing much.
- **Node 1's default forecast window asks for one day more than Open-Meteo
  serves.** Generating the ten-region sample with an empty input failed with
  "Open-Meteo served neither ERA5 nor a forecast for 1 day(s): 2026-10-05";
  `{"forecast_days": 14}` works. This is a node 1 edge case at a UTC day
  boundary, not a node 3 problem, and is listed as an upstream follow-up rather
  than worked around here.

### C9. What the ten-region sample also closed

The plan's step 1 generated a real ten-region node 1 -> node 2 run to serve as
`sample_input.json`. That also discharges node 2's own outstanding follow-up
("AC-9 was verified on two regions, not ten... a full ten-region node 1 output
should be run once before the model goes on a schedule"). Node 2 ran all ten
regions cleanly, offline, in about a second per region.
