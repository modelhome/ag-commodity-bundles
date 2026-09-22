# corn-price

**US Corn Price Impact** — node 3 of the climate → agriculture → finance flow.

Takes a `corn_yield_snapshot` from
[`wofost-bundles/corn-yield`](https://github.com/modelhome/wofost-bundles/tree/main/corn-yield)
and returns the corn price impact that season's weather implies: a
production-weighted national yield and production shock, and the price response
that shock implies under a fitted, cited transmission, with the assumptions and
the uncertainty beside every number.

> **This is not a price forecast, not a trading signal and not investment
> advice.** It is the price move implied by one weather-driven production shock
> under one stated transmission, holding everything else equal. Demand, policy,
> the macro environment, the rest of the balance sheet and every other crop are
> outside it. The fitted relationship explains about a third of year-on-year
> corn price variation; most of what moves corn is not in this model.

## Running it

```bash
cd corn-price
docker build -t ag-commodity-corn-price:local .
docker run --rm --network none ag-commodity-corn-price:local   # uses sample_input.json
```

`--network none` is the test, not a precaution: the model makes no network calls
at run time.

Against your own node 2 output:

```bash
mkdir -p run && cp sample_input.json run/corn_yield_snapshot.json
docker run --rm --network none -v "$PWD/run:/run" ag-commodity-corn-price:local \
    /run/corn_yield_snapshot.json /run/corn_price_regions.output.json \
    > run/corn_price_impact.output.json
```

Or on Model Home, paste
`https://github.com/modelhome/ag-commodity-bundles/tree/main/corn-price` into
the "classic import" option, then wire it after the US Corn Yield model in a
flow. The two bind automatically: node 3's input declares
`required = ["metadata", "columns", "rows"]`, which matches node 2's
`corn_yield_snapshot` and is refused by its `corn_yield_trajectory`.

## Input

The whole input is node 2's `corn_yield_snapshot`. Node 3 reads, per region:

| Field | Used for |
|---|---|
| `region_key` | joining this model's tables; an unknown key fails the run |
| `state`, `date` | carried through |
| `yield_percentile_rank` | **drives the calculation** |
| `yield_anomaly_pct` | reported for comparison only, never as a magnitude |

and from `metadata`: the reporting date, `baselines.period`, and each region's
baseline spread (`median_kg_ha`, `p10_kg_ha`, `p90_kg_ha`).

`reference_price_usd_bu` sets the price the dollar figures are worked out
against. It is a **key inside the input document**, not a separate declared
input, so it is settable only when you compose that document yourself — running
the bundle standalone, or from a hand-written file. **In a flow the upstream
model supplies the document verbatim, so a flow always gets the committed
default.** Making it overridable in a flow needs a second declared `[[inputs]]`,
and the platform requires every input of a non-first step to be wired, which
would force every flow author to supply a price for a figure that is cosmetic to
the percentage impact. See *Future work*. A present-but-empty value falls back
the same way a missing key does; a non-finite or non-positive value fails the
run.

The region set comes from the input. This model joins on node 1's `region_key`
and never defines its own; a key with no table row exits 1 naming the key and
the table.

## The three things this model does

### 1. It uses node 2's rank, not node 2's percentage

This is the most important thing to understand here, and getting it wrong
produces a confident number that is wrong by a factor.

Node 2's `yield_anomaly_pct` is the anomaly of an uncalibrated, rainfed,
single-point WOFOST simulation. Its thirty-year baseline distributions span 81
to 13,414 kg/ha for Iowa and 0 to 5,795 kg/ha for Kansas. Measured as a
p10-to-p90 spread against each state's *observed* detrended yield distribution,
node 2 is over-dispersed everywhere:

| region | ia | il | mn | ne_irr | ne_rain | in | sd | oh | wi | ks_irr | ks_rain | mo |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| dispersion ratio | 6.6 | 6.0 | 5.3 | 2.8 | 4.4 | 5.7 | **9.2** | 4.2 | 6.6 | 2.7 | 5.6 | 3.1 |

A real state yield moves by roughly ±10 points against trend in a normal year
and ±25 in an extreme one. Node 2's percentages move by three to twelve times
that, so they cannot be multiplied by production and fed to a price elasticity.

What survives the scale problem is the **rank**. Each region's
`yield_percentile_rank` is read as the same quantile of that state's observed
detrended yield distribution over the same 1995–2024 window — standard
**quantile mapping**, applied to yields rather than to weather.

The mapping is measured against the observed distribution's **median**, not
against the trend line, for two reasons. Node 2's own anomaly is
median-relative, so a rank of 50 means "an unremarkable season" and must map to
zero here too, or every ordinary season would imply a price impact. And a
least-squares trend through a left-skewed yield series sits below the median, so
quantiles read straight off the trend line carry a standing positive offset
(about +2.4 points in Ohio) that has nothing to do with this season's weather.
Only the *scale* of the deviation matters downstream, because the reported
effect is marginal, so shifting the origin changes nothing except making an
average season report zero.

The size of this correction is visible in the output rather than taken on trust:
every region ships `yield_anomaly_simulated_pct` and `dispersion_ratio`
alongside `yield_anomaly_real_pct`, and the national block ships
`us_yield_shock_pct_unmapped` — what the figure would have been without the
rescaling. On the committed sample that is **+2.83% unmapped against +0.19%
used**, a factor of fifteen.

The **period must match**. A percentile rank from node 2 is read as the same
quantile of this model's distribution, so rank *n* of thirty has to mean the
same thing on both sides. The runner compares node 2's `baselines.period`
against `yield_history.meta.json` and refuses to run on a mismatch, rather than
producing a quietly wrong answer.

**What this does not fix.** The mapping assumes node 2's rank is informative
even where its magnitude is not. If a rank is set wrongly upstream, the mapping
carries it faithfully onto the real distribution.

### 1b. Irrigated and rainfed parts of a state are separate regions

Node 1 splits a state into an irrigated and a rainfed stratum when irrigation
covers 20% or more of its harvested corn acres. That is Nebraska (52.7%) and
Kansas (25.4%); the other eight states keep one region each. Node 2 simulates
the two irrigated strata with soil-moisture-triggered irrigation rather than as
dryland. So this model sees **twelve** regions — `ia, il, mn, ne_irrigated,
ne_rainfed, in, sd, oh, wi, ks_irrigated, ks_rainfed, mo` — and `ne` and `ks` no
longer exist.

Each stratum needs its own observed distribution, because **irrigation damps
year-to-year yield variation** and reading an irrigated stratum's rank against
its state's blended spread would overstate its swings by about a factor of two.
Measured on NASS's own irrigated and non-irrigated state yield series, detrended
p10–p90 spread:

| | irrigated | state | non-irrigated | corr(irr, non-irr) |
|---|---|---|---|---|
| NE | 10.1 pts (**0.50×**) | 20.5 | 43.1 (**2.11×**) | **0.31** |
| KS | 16.4 pts (**0.62×**) | 26.6 | 51.5 (**1.94×**) | 0.80 |

Those raw factors are each measured against the state *marginally*, and they do
not recombine to the state: production-weighted they average **1.086** in
Nebraska and **1.394** in Kansas, not 1. Marginal spreads only add linearly when
the two series move together, and the correlations above show they do not — the
real state series already embeds that diversification and is narrower than the
sum of its parts. Used raw, the split would have handed Nebraska 8.6% and Kansas
**39.4%** more influence over the national shock than treating each as one
region did, which is an artifact and not a modelling choice.

So the factors are **normalised**: divided by their own weighted mean, which
preserves the measured irrigated-to-rainfed proportion exactly while making a
split state recombine to its own observed swing.

**The normalisation happens in the runner, not in the committed table**, and the
reason is worth stating. The weights that decide how much a region counts are
acres times *that region's own fitted trend yield at the run's year* — not the
census production shares the table is built on. Because the two strata have
their own trend slopes, those two bases drift apart: normalising on production
shares left Nebraska 3.3% over-weighted in 2024 and 5.1% in 2040, a silent error
that grows with time. So `yield_history.csv` carries the **measured** factors
(NE 0.495 / 2.108, KS 0.617 / 1.936) and the runner divides by the divisor it
computes from the weights it is about to use. The identity then holds exactly at
every run year, and each row ships
`stratum_dispersion_ratio` (applied), `stratum_dispersion_ratio_measured` and
`stratum_normalisation_divisor` so the arithmetic is auditable from the output.

The cost, stated because it is real: a stratum's applied spread is no longer its
own measured marginal spread. Per-stratum figures are intermediate and the
national figure is the output, so preserving the aggregate is the right trade —
but a reader of a single stratum row should know its width is set by that
choice.

**The awkward part, stated plainly.** NASS publishes those per-stratum series
for Nebraska and Kansas, but **both end in 2018** — 24 of the 30 years in the
window, missing 2019–2024, which includes the 2022 western drought. There is
therefore no per-stratum history covering the window, and the window cannot move
because it is node 2's.

What ships instead is a **rescaling**: a stratum's distribution is its state's
1995–2024 detrended series with its spread multiplied by the ratio measured over
the overlapping years, placed on the stratum's own fitted trend level. Every
measured ratio, the years behind it and the NASS series it came from ship in
`metadata.tables.yield_history.stratum_rescaling`, and each region's own factor
is on its row as `stratum_dispersion_ratio`.

This assumes two things, and the second is the stronger:

1. that the ratio measured over the published years holds over the unpublished
   ones; and
2. that a stratum's **year-to-year shape** is its state's — it is only the width
   that differs.

In a season when irrigation is the whole story the two strata do not move
together at all, and this model carries that error rather than detecting it. It
is named in the output's `not_captured` for that reason. If NASS resumes the
series, or a county-level reconstruction becomes practical, the rescaling should
be replaced by measurement.

**A stratum is not "the better half".** Operations irrigating their entire corn
crop out-yield those irrigating none by 105% in Kansas and 55% in Nebraska, but
yield 8% *less* in Iowa and 20% less in Ohio, where irrigation sits on marginal
ground. Nothing here assumes irrigated means better, and no code infers a
stratum from the spelling of a region key — `production_weights.csv` declares
it, exactly as node 2's `water_regime.csv` declares its water regime. The runner
also **checks that declaration against node 2's own** `metadata.baselines.
regions[key].regime` on every run and refuses to start on a mismatch: a snapshot
whose `ne_irrigated` was in fact simulated rainfed would have its rank read
against a distribution of about twice the right width, which is a silently wrong
answer rather than a rough one. A region that declares no regime at all is
treated as a mismatch, not assumed rainfed.

Two irrigation figures ship per region and they mean different things.
`irrigated_share` describes **the region** — 1 or 0 for one part of a split
state, since the part is defined by its irrigation — while
`state_irrigated_share` carries **the whole state's** figure on every row, is
identical on both parts of a split state, and is the number node 1's 20%
threshold is applied to. Use the second to compare states.

**Irrigation supply is unconstrained upstream.** Node 2 irrigates whenever soil
moisture falls to its trigger, with no aquifer decline, allocation limit or
pumping ceiling, so an irrigated stratum's drought protection is an **upper
bound**. This model's distributions come from realised NASS yields and already
embed whatever supply limits were real, so the *scale* is not inflated by that
assumption; what it cannot correct is a **rank** set too high upstream in a
severely water-short year. That remains node 2's to fix.

### 2. It combines the regions by production weight

Combining *relative* anomalies into a national relative anomaly needs
**production** weights. The weight is harvested acres (2022 Census) times the
state's fitted trend yield for the run's calendar year:

```
w[s] = acres_harvested[s] * trend_yield[s](year)
```

Actual 2022 production is not used as the weight because 2022 was dry in the
west, so it would embed that season's own drought in exactly the states this
model is already careful about. The raw 2022 production share ships per region
as a cross-check.

This is also where the **yield trend** belongs. Node 2's baseline is
fixed-weather by design and carries no genetics or management gain; this model
applies a per-state ordinary least squares trend on the observed 1995–2024
series, evaluated at the run's year.

**Coverage is stated, never scaled away.** The twelve regions node 1 defines are
**82.47%** of US corn-for-grain production (2022 Census). Three national figures
ship and the difference between them is the point:

- `covered_yield_shock_pct` — the production-weighted mean over the regions
  present. The primary number.
- `us_yield_shock_pct` — the same shock as a share of the **whole US crop**,
  under the explicitly named assumption that the uncovered acres are at trend
  and contribute no shock. This is what the transmission is applied to, because
  the transmission was fitted on the national yield deviation.
- `us_production_shock_bu` — the shortfall or surplus in bushels.

Running on a subset reports that subset's coverage, not the full set's.

### 3. It applies a transmission fitted on a matching regressor

The two obvious off-the-shelf numbers are both wrong for this input, in opposite
directions, and both ship in the output as cited reference points that are **not
used**:

- **Roberts and Schlenker (2013)**, *American Economic Review* 103(6), 2265–95,
  is the canonical identification of agricultural supply and demand elasticities
  using yield shocks as the instrument. Their Table 1 (FAO data) gives a supply
  elasticity of 0.087–0.116 and a demand elasticity of −0.028 to −0.066,
  implying a price multiplier `1/(β_s − β_d)` of **5.75–7.73**. That multiplier
  is derived for a *permanent* shift in the world caloric aggregate — the US
  ethanol mandate — and the paper itself explains that storage smooths a
  transitory shock. Applying ~6× to one season's weather overstates it by
  roughly three to four times.
- **farmdoc daily (8):212**, 15 November 2018, regresses the percent change in
  December corn futures on the percent change in national corn yield from the
  May to the November WASDE, 1993–2017, and gets **−1.84** with an R² of 0.48.
  That is the right *kind* of object — US corn, within-season, transitory,
  already embedding storage. But its regressor is the yield surprise against the
  **May WASDE**, that is, against market expectation, whereas this model's shock
  is measured against a thirty-year weather baseline. Applying −1.84 to the
  second quantity would be applying a coefficient to something it was not fitted
  on.

So the relationship is fitted here, on a regressor defined the same way this
model defines its own shock. `build_transmission.py` fits:

```
dlog_price[t] = a + b0 * d[t] + c * d[t-1]
```

where `d` is the national yield's deviation from a fitted linear trend, as a
fraction. Fitted on **1976–2025, n = 50**:

| term | coefficient | s.e. | t |
|---|---|---|---|
| intercept | +0.0106 | 0.0234 | +0.45 |
| **shock** | **−0.7826** | 0.2834 | **−2.76** |
| lagged shock | +0.9691 | 0.2833 | +3.42 |

R² 0.329, adjusted R² 0.300, residual s.d. 0.166.

**Term selection was by a rule fixed before the fits were run**, applied to
every candidate alike: keep a term only if |t| ≥ 2.0. The candidates and what
happened to each are recorded in `transmission.json`. The lagged shock is in the
specification because the dependent variable is a *difference*, so last season's
shock mechanically enters this season's change; it is a control and does not
enter the reported impact.

**What is reported is the marginal effect**, `b0 × d`: the price response to
this season's shock holding everything else equal. It is deliberately not a
prediction of the price change.

**Stocks-to-use is not in the model, and that is a finding, not an omission.**
Every form tried — shock divided by carry-in, shock times log carry-in, and log
carry-in alone — came out statistically indistinguishable from zero (|t| < 0.1
in every case). This is *not* a refutation of the well-documented convex
relationship between stocks-to-use and the price **level**; the dependent
variable here is a year-on-year **change**, which differences that level
relationship away. The model therefore does not claim a tight-stocks
amplification it cannot demonstrate. The carry-in ratio still ships in the
output metadata as context.

Note also that the conditioner would have to be the **carry-in** ratio (the
prior marketing year's ending stocks over its total use), not the current year's
stocks-to-use: a short crop draws stocks down, so the usual figure is endogenous
to the very shock being conditioned.

### Uncertainty

`price_impact_pct` and `price_impact_usd_bu` each ship with a `_low` and a
`_high`, and the schema requires all three together so a number cannot ship
without its range even by accident. There is deliberately **no implied price
level** in the output: a bare `$/bu` figure is the one number a reader would
take for a forecast, and `reference_price_usd_bu` plus `price_impact_usd_bu`
already give it to anyone who wants it.

The range is a **nonparametric bootstrap 95% interval on `b0`** — 10,000 pairs
resamples, fixed seed — so it expresses how well the transmission itself is
known from fifty annual observations. On the committed fit that is
`b0 ∈ [−1.269, −0.393]`, a factor of three between the ends. That is the honest
answer at this sample size and it is not narrowed by tuning.

It is **not** an interval on what the corn price will do. The fit explains 33%
of year-on-year price variation, so most of what moves the price is outside this
model entirely. The shock itself carries no interval: its uncertainty comes from
node 2 and from the quantile mapping, neither of which this model can quantify
honestly.

### What the transmission does not capture

Named one by one in the output's `assumptions.not_captured`:

- cross-commodity substitution: soybean and wheat prices move too, and corn
  acreage responds in the following season;
- the ethanol and export demand channels separately — the reduced form sees only
  their aggregate;
- **export demand shocks.** The fit is on supply-side weather shocks; a shift in
  export demand, whatever causes it, is outside what this coefficient was
  estimated on, and applying it to one assumes a symmetry the fit does not
  demonstrate;
- **trade policy.** Tariffs, retaliation and export restrictions are absent
  entirely. Shocks of that kind are a handful of episodes in this fifty-year
  window — too few to fit a term on under the selection rule above — and they
  move the price on announcement rather than over a marketing year. See
  [Export exposure](#export-exposure) for what this bundle does carry instead;
- basis and the futures curve; the fit is on a cash marketing-year average price;
- policy shocks, including changes to the Renewable Fuel Standard;
- *when* within the season the price moves — this is an annual relationship.

## Export exposure

`price_history.csv` carries two columns this model never reads:
`exports_mil_bu` and `export_share_of_use`, from the same ERS file, the same
vintage and the same `Table 4--Corn: Supply and disappearance` that supplies the
stocks series. The current and latest-complete figures are in
`price_history.meta.json` and are copied into every run's
`metadata.export_exposure`.

Two things to be clear about, because a bushel figure printed beside a price
impact invites exactly the inference this bundle refuses to make:

- **Exports are a component of total use, not an addition to it.** A lost-export
  scenario reduces the denominator as well as the numerator. Adding exports to
  `total_use_mil_bu` double-counts them.
- **This is exposure context, not a priced scenario.** The transmission is
  fitted on supply-side weather shocks and does not price trade policy of any
  kind — see the list above. The exposure is carried so that a separate
  downstream trade-policy model can express a lost-sales scenario as a share of
  US corn use against this bundle's own vintage, rather than re-sourcing the
  balance sheet and silently picking a different one.

Exports are also **endogenous within the marketing year** — they respond to the
price they are reported beside — so, like `stocks_to_use`, the figure is context
and never a conditioner of the transmission.

Over the committed window the share runs from **6.6%** (2012, the drought year,
when a short crop rationed exports first) to **32.8%** (1980).

## Committed tables

Every table is built once by a script that is not in the image, and ships a
`*.meta.json` recording the source URL, the server's `last-modified`, the period
covered and the build time. The runner copies those vintages into its output so
a reader can see how old the economics are.

| Table | Rows | Source | Built by |
|---|---|---|---|
| `production_weights.csv` | 12 | USDA NASS **2022 Census of Agriculture**, state-level `CORN, GRAIN - PRODUCTION, MEASURED IN BU`, `CORN, GRAIN - ACRES HARVESTED`, `CORN, GRAIN, IRRIGATED - ACRES HARVESTED`, plus `CORN, GRAIN, IRRIGATED, ENTIRE CROP - YIELD` and `..., NONE OF CROP - YIELD` to apportion a split state between its strata, from `nass.usda.gov/datasets/qs.census2022.txt.gz` (~310 MB, keyless). **The same file and vintage node 1 used** to place its region points. | `build_weights.py` |
| `yield_history.csv` | 360 | USDA NASS survey series `CORN, GRAIN - YIELD, MEASURED IN BU / ACRE`, state / annual / final estimate, 1995–2024, from `nass.usda.gov/datasets/qs.crops_<YYYYMMDD>.txt.gz` (~1.1 GB, keyless), plus the `IRRIGATED` and `NON-IRRIGATED` counterparts (1995–2018 only) to size each stratum | `build_yield_history.py` |
| `price_history.csv` | 52 | USDA ERS **Feed Grains Yearbook Tables — All Years**, US annual marketing-year corn: area, yield, production, price received (Table 1) and beginning/ending stocks, total use and exports (Table 4) | `build_price_history.py` |
| `transmission.json` | — | fitted from `price_history.csv` | `build_transmission.py` |

Notes worth keeping:

- A NASS `(D)` value means the figure was **withheld for disclosure**. It is
  recorded as missing, never as zero — a withheld irrigated-acres figure would
  otherwise read as "this state does not irrigate".
- The survey bulk export's filename **carries a date that changes**, so
  `build_yield_history.py` discovers the current name from the directory listing
  and records it in its meta file rather than hard-coding one.
- About three lines in four of that export contain **NUL bytes** in trailing
  fields, which makes Python's `csv` module raise `line contains NUL`. The build
  script strips them. The census export has none.
- The stratum yield series is pinned through its full `SHORT_DESC`, because
  NASS also publishes each of them `MEASURED IN BU / NET PLANTED ACRE` — a
  different denominator that would silently mix a planted-acre yield into a
  harvested-acre series.
- A stratum row's `yield_bu_acre` is **derived** (trend × (1 + deviation)), not a
  published NASS figure. `trend_yield_bu_acre` and `deviation_pct` are what the
  runner reads.
- The Quick Stats **API needs a key** (401 without one). The bulk exports do not.
- The current marketing year in the ERS data is a **WASDE projection**, not an
  outcome. It is flagged `is_projection`, excluded from both the trend fit and
  the transmission fit, and used only as the source of the runner's defaults.

## Verified results

Sample run, 2026-09-21, all twelve regions from a real node 1 → node 2 chain:

| region | rank | node 2 % | mapped % | dispersion | weight | contribution |
|---|---|---|---|---|---|---|
| ia | 70.0 | +22.0 | +1.10 | 6.6x | 0.216 | +0.24 |
| il | 50.0 | +1.9 | 0.00 | 6.0x | 0.181 | 0.00 |
| in | 83.3 | +26.0 | +4.35 | 5.7x | 0.086 | +0.38 |
| ks_irrigated | 63.3 | +7.8 | +1.23 | 2.7x | 0.020 | +0.02 |
| ks_rainfed | 60.0 | +57.8 | +3.01 | 5.6x | 0.031 | +0.09 |
| mn | 30.0 | -41.8 | -5.45 | 5.3x | 0.123 | -0.67 |
| mo | 60.0 | +12.5 | +5.78 | 3.1x | 0.041 | +0.24 |
| ne_irrigated | 50.0 | +0.2 | 0.00 | 2.8x | 0.086 | 0.00 |
| ne_rainfed | 70.0 | +40.4 | +3.61 | 4.4x | 0.055 | +0.20 |
| oh | 50.0 | -0.5 | 0.00 | 4.2x | 0.051 | 0.00 |
| sd | 26.7 | -43.7 | -3.88 | 9.2x | 0.064 | -0.25 |
| wi | 40.0 | -14.8 | -2.01 | 6.6x | 0.046 | -0.09 |

Covered shock **+0.16%**, US shock **+0.13%** over 82.5% of production
(**+2.83%** unmapped). Price impact **-0.10%** [-0.17, -0.05], or
**-$0.005/bu** on a $4.80 reference. A genuinely unremarkable season implies
almost nothing, which is the behaviour to expect.

Note `ne_irrigated` at rank 50.0 against `ne_rainfed` at 70.0 on the same
weather: the two strata carry different signal, which is the point of splitting
them.

`check_price.py`: **157/157 checks pass** (95 before brief 0003, then 133 and 154
across two Copilot reviews). Notably:

- **The 2012 drought, end to end, on published stratum observations.** The four
  strata are ranked against USDA's *published* 1995-2018 irrigated and
  non-irrigated series, not against this model's own rescaled reconstruction, so
  the case tests the reconstruction rather than confirming it. In 2012 that puts
  `ne_irrigated` at the 17th percentile and `ne_rainfed` at the 0th -- irrigated
  Nebraska barely felt the drought. Result: a US yield shock of **-21.52%**
  against the actual national deviation of **-22.23%**, **0.71 points**. That is
  a larger error than the ten-region model's 0.31, and the gap is the
  shared-shape assumption being paid for honestly rather than hidden: ranking
  the strata against their own rescaled series instead would have given 0.26 and
  proved nothing. The case is dated 2012 so the
  weights use 2012 trend yields. That tests the mapping, the stratum rescaling,
  the weighting and the coverage assumption against a real outcome rather than
  against themselves. The actual 2012 price move (+10.8%) falls inside the
  implied range (+9.3% to +33.3%), which is a weak test on one observation and
  is labelled as one.
- A tenth-percentile season everywhere gives a **-9.76%** US shock and a
  **+7.94%** [+3.91, +13.20] price impact: right sign, plausible size.
- Node 2 is over-dispersed in every region (3.1x to 9.2x), and within each split
  state the irrigated stratum is the **less** over-dispersed of the two -
  irrigation damps the simulation and the observation alike, which is the
  direction the rescaling assumes.
- Each split state's two strata sum to its published 2022 Census harvested
  acres, and the twelve production shares still sum to 82.47%: splitting moves
  production between rows without creating or destroying any.
- The committed stratum spreads match the recorded dispersion ratios exactly,
  so `yield_history.csv` and `yield_history.meta.json` cannot describe different
  rescalings.
- **Splitting a state does not change its weight in the national figure**:
  checked in the weights the runner actually used, each split state's mean
  applied ratio is **1.0000**, while the raw measured ratios would have averaged
  1.1252 (NE) and 1.4240 (KS). The measured irrigated-to-rainfed proportion
  survives the normalisation exactly.
- A snapshot whose water regime disagrees with this model's stratum exits 1,
  in both directions and when the regime is absent entirely.
- No code infers a stratum from the spelling of a region key; a check asserts
  that directly against `runner.py`'s source.
- An unknown `region_key` exits 1 naming the key and the table, with no
  traceback; a baseline-window mismatch exits 1 naming both windows; an *absent*
  upstream baseline window also exits 1 rather than assuming compatibility; and
  a non-finite or non-positive reference price is rejected.
- No region field is emitted as `null` under a declared `number` type. The
  schema format has no nullable type, so an optional field with no value -
  `irrigated_share` and `state_irrigated_share` where USDA withheld it,
  `dispersion_ratio` where the
  upstream document carries no baseline spread, `stratum_dispersion_ratio` on an
  unsplit region - is **omitted**, never nulled.

The Modelfile validates against the platform validator with no annotation
warnings, and `check_schema_compatibility` confirms the input still binds node
2's `corn_yield_snapshot` and is still refused by `corn_yield_trajectory`.

`docker run --network none` reproduces the local run **byte-identically** apart
from `generated_at`.

The check needs Python 3.11 or newer to read the Modelfile; the image is
`python:3.12-slim`:

```bash
uv run --python 3.12 --no-project python corn-price/check_price.py run/corn_price_impact.output.json
```

## Limitations

1. **A stratum's year-to-year shape is its state's.** Each stratum's observed
   distribution is its state's series rescaled in width, because NASS's
   per-stratum series end in 2018. In a season when irrigation is the whole
   story the two strata do not move together at all, and this model carries that
   error rather than detecting it. It is the weakest assumption in the bundle.
2. **Irrigation supply is unconstrained upstream**, so an irrigated stratum's
   drought protection is an upper bound and its rank may be set too high in a
   severely water-short year. This model's scale is built from realised yields
   and is not inflated by that, but nothing here can correct a wrong rank. Node
   2's to fix.
3. **The rank is only as good as node 2.** Everything downstream inherits it.
4. **The percentile is discrete over thirty baseline years**, so it has about
   3.3-point granularity, and the mapped anomaly inherits that.
5. **R² 0.329 on 50 annual observations.** The transmission is a reduced form,
   not a structural model, and the bootstrap interval is wide because the
   relationship genuinely is not known more precisely than that.
6. **The uncovered 17.5% of US corn is assumed to be at trend.** Named in the
   output, but it is an assumption, and in a widespread drought it is wrong in
   the direction that understates the shock.
7. **Early in the season node 2's projection is mostly climatology**, so its
   anomaly is near zero by construction and so is this model's output. That is
   expected, not a defect.
8. **The 2022 Census is a single year.** Harvested acres from one census year
   are the weight; a five-year average would be more representative and is
   listed as follow-up.

## Future work

**Live and market prices** (the dollar figures only; the percentage impact needs
no price at all). Three routes, in increasing cost:

1. **A second declared input.** `reference_price_usd_bu` is currently a key
   inside the upstream document, so a flow cannot override it — the upstream
   model supplies that document whole. Promoting it to its own `[[inputs]]`
   would let the flow editor bind it to an `inline` literal or an `http(s)`
   `url`, since `flow_service` resolves each input independently against any
   earlier step. The cost is that the platform requires every input of a
   non-first step to be wired, so every flow author would then have to supply a
   price. Worth doing only once the dollar figure matters more than the
   zero-configuration default.
2. **Compose with a rebuilt `yfinance-bundles/historical-ohlcv`** as an earlier
   flow step. Structurally this already works — a step's inputs may come from
   *any* earlier step — but that bundle is old-generation and would need
   rebuilding to current conventions first: it carries legacy `[runner]` and
   `[build]` blocks, no `determinism` / `validity_domain` / `not_for` /
   `provenance` annotations, and a typo in its output schema
   (`properties.ticrowsker.type`). It would also make the flow
   non-deterministic, and continuous front-month `ZC=F` has roll artifacts that
   make it a poor reference price.
3. **A run-time fetch inside this model.** Cheapest to write, most expensive to
   own: it would move node 3 out of node 2's determinism category into node 1's,
   and the output would have to start stamping `retrieved_at`, the source and
   the endpoints, with the `determinism` annotation reworded to match.

**Other follow-ups.** Replacing the stratum rescaling with measurement, if NASS
resumes its irrigated and non-irrigated state yield series (discontinued after
2018) or if a county-level reconstruction becomes practical; a five-year average
for the production weights rather than a single census year; a within-season transmission fitted on futures rather than
an annual cash price, which would need a futures source and would let the model
answer "what does this do to December corn" rather than "to the marketing-year
average"; and revisiting stocks-to-use in a price-*level* specification, where
the literature's convex relationship actually lives.

## Determinism

Deterministic and offline: a pure function of the input document and the
committed tables. No network calls, no randomness, no wall-clock dependence
beyond the `generated_at` stamp. The bootstrap that produced the interval ran
once at build time with a fixed seed and its result is committed.

## Licence

MIT, as the repo. Data: USDA NASS and USDA ERS, both US Government works in the
public domain. Cited literature is cited, not redistributed.
