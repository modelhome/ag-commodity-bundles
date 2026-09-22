# ag-commodity-bundles

Standalone Model Home **model bundles** for agricultural commodity markets:
the layer that turns a physical crop signal into a market consequence. Each
bundle is a self-contained folder with everything Model Home needs to run one
model: a `Modelfile.toml`, a `Dockerfile`, a `runner.py`, and sample input(s).

This repo holds only the Model Home packaging layer plus the data tables and
the economics that sit on top of them. Nothing upstream is vendored.

```
ag-commodity-bundles/
  CLAUDE.md                 <- you are here
  README.md
  LICENSE                   (MIT)
  .claude/skills/feat/      <- vendored feat skill (brief -> plan -> PR workflow)
  docs/features/            <- feature briefs (NNNN-name.md)
  docs/plans/               <- implementation plans, one per brief
  <bundle>/                 <- one self-contained model per folder
```

This is the market end of a climate -> agriculture -> finance flow:

| Node | Repo | Job |
|---|---|---|
| 1 | `agromet-bundles/crop-weather/` | the daily weather series, in PCSE's own variables and units |
| 2 | `wofost-bundles/corn-yield/` | phenology, projected yield, weather-driven yield anomaly per state |
| **3** | **`ag-commodity-bundles/corn-price/`** | **the national production shock and the price impact it implies** |

Node 2's output is node 3's input, unchanged: they are semantic peers,
composed in a Model Home Flow. Node 3 never re-derives anything node 2
produces, and never redefines the region set.

---

## The templates: `QuantLib-bundles/bond/`, `agromet-bundles/crop-weather/`, `wofost-bundles/corn-yield/`

[`bond/`](https://github.com/modelhome/QuantLib-bundles),
[`crop-weather/`](https://github.com/modelhome/agromet-bundles) and
[`corn-yield/`](https://github.com/modelhome/wofost-bundles) are the
authoritative templates. Read them before starting a bundle and mirror them.
`bond` is the minimal shape; `crop-weather` is the shape for a bundle with a
committed table built by a one-time script; `corn-yield` is the immediate
upstream and the closest model for this repo's conventions. Consistency with
them matters more than any local preference:

- **`Modelfile.toml` keys.** `name`, `description`, `run`, `image`, `args`,
  `[resources]`, `[[inputs]]` with a documented `[inputs.schema]` (every
  property has a plain-language `description`; the schema's `default` is what
  the platform offers as the "Example to paste", so it must stay runnable), and
  `[[outputs]]` with `[outputs.schema]`. Add the annotation fields Model Home
  validates: `determinism`, `expected_runtime`, `validity_domain`, `not_for`,
  `provenance`, and per-property `unit`. Rationale the schema cannot express
  goes in TOML comments beside it. The platform validator caps
  `validity_domain` at **600 characters** and `provenance` at **400**, and
  rejects any key listed in a `required` array that has no declared
  `properties.<key>.type`; longer prose belongs in the README.
- **Runner I/O contract.** Input JSON file path(s) arrive as positional args.
  The result JSON goes to **stdout** and nothing else does; logs go to stderr.
  The Modelfile's `run` redirects stdout to `run/<output>.output.json`. Further
  outputs are passed as `{output:NAME}` args.
- **`required = []` and defaults in the runner.** A present-but-empty value
  (`""` or `null`) falls back the same way a missing key does, so the model runs
  standalone or composed.
- **Dockerfile.** `python:3.12-slim`, `WORKDIR /app`, exact `==` pins installed
  in one `pip install --no-cache-dir` layer, `COPY` paths relative to the bundle
  folder, `ENTRYPOINT ["python", "runner.py"]`, and a `CMD` naming the bundled
  sample input so a bare `docker run` works.
- **Stdlib over dependencies.** The arithmetic in this repo is weighted sums,
  a fitted coefficient and an interpolation. It does not need a scientific
  stack; nothing gets added without a clear reason.

## Model Home platform facts (verified against the platform code)

- **Build context is the bundle subfolder.** Adding a model from
  `github.com/modelhome/ag-commodity-bundles/tree/main/<bundle>` promotes that
  folder to the build-context root, exactly like `cd <bundle> && docker build .`.
  Never use repo-relative `COPY <bundle>/...` paths; the on-platform build fails.
- **Every output is a JSON file.** The platform collects only
  `/run/<name>.output.json` for each declared `[[outputs]]` name and parses it
  with `json.loads`. Any other file a runner writes (a CSV, a PNG) is discarded
  on-platform. A long-format table therefore ships as JSON
  (`{metadata, columns, rows}`); a CSV written beside it is for off-platform use
  only, and its README must say so.
- **A schedule re-sends a fixed input.** A scheduled run passes the same stored
  input every time, so anything that should change per run (such as "today")
  must be defaulted inside the runner, not baked into the schedule's input.
- **Flow steps connect by output shape, not by name**, and "shape" has an exact
  meaning: `check_schema_compatibility` in
  `orchestration/modelfile/validation.py` requires the downstream input's
  `required` keys to be a **subset** of the upstream output's `required` keys,
  then recurses into `properties` and array `items`, comparing declared `type`s
  by equality with one widening (a `number` input accepts an `integer` output).
  A property with no declared `type` is unconstrained and is not compared.
  Practical consequence: **an output is selected by its required-key set**, so
  declaring `required = ["metadata", "columns", "rows"]` binds to node 2's
  `corn_yield_snapshot` and cannot bind to `corn_yield_trajectory`, whose
  required keys are `generated_at`, `metadata`, `regions`.
- **A step's inputs need not all come from the step before it.** `flow_service`
  resolves each input independently: to any *earlier* step's output
  (`source_step_index` / `source_artifact`), to an `inline` literal JSON
  document, or to an `http(s)` `url`. So a second input -- a reference price, a
  stocks figure -- can be wired in a flow without the model fetching anything
  itself. Auto-matching by name only happens against the immediately previous
  step; anything else is wired explicitly.
- **The Modelfile schema format has no nullable type.** `schema_type` requires a
  plain string, so `["number", "null"]` is not expressible. A field that can be
  empty therefore must not appear in any `required` list -- and if a required
  field can go null, fix the model rather than the declaration.

## The upstream contract: node 2's `corn_yield_snapshot`

Node 3's input schema **is** node 2's output schema. Take the field list from
`wofost-bundles/corn-yield/Modelfile.toml`, not from memory. What matters here:

- The document is `{metadata, columns, rows}`, one row per region, with
  `required = ["region_key", "state", "date", "stage_name",
  "yield_projection_kg_ha", "yield_baseline_kg_ha", "yield_anomaly_pct",
  "yield_percentile_rank", "heat_stress_days_in_silking_window",
  "frost_days_in_sensitive_window"]`.
- **`yield_anomaly_pct` is a simulated, uncalibrated, rainfed anomaly**, not a
  real-world yield anomaly. Node 2's own thirty-year baseline distributions span
  81 to 13,414 kg/ha for Iowa and 0 to 5,795 kg/ha for Kansas. A percentage off
  a median like that is several times more dispersed than a real state yield
  deviation from trend, and it is the single biggest trap in this repo: multiply
  it straight through a price transmission and the answer is not slightly wrong,
  it is wrong by a factor.
- **`yield_percentile_rank` is the robust statistic** on a distribution that
  skewed, and it is bounded by construction.
- **Nothing in node 2 carries a yield trend.** Its baseline is fixed-weather by
  design, so genetics and management are absent. The trend belongs in this repo,
  applied to a real USDA level.
- **Node 2 simulates every region as rainfed.** In the 2022 Census of
  Agriculture, 52.7% of Nebraska's and 25.4% of Kansas's harvested corn acres
  were irrigated, against 1.2% in Iowa. Their weather response is overstated,
  and production-weighting propagates that into the national figure unless it is
  handled deliberately.

## Conventions for every bundle

- **Read the upstream Modelfile and the upstream plan before coding.** Take
  field names, units and semantics from the source, not from a README or from
  this file.
- **Unit slips are the classic bug.** Convert at one boundary and name variables
  with their unit (`yield_kg_ha`, `production_bu`, `price_usd_bu`). Every
  conversion gets a comment naming both units and the factor.
- **Percent versus fraction is this repo's own unit trap.** Node 2 emits
  percentages (`yield_anomaly_pct`, `yield_percentile_rank` on 0-100). Elasticity
  arithmetic is natural in fractions. Pick one internally, name the variables for
  it, and convert once.
- **Commit a validation check** per bundle, run outside the image, that proves
  the *modelling claims* rather than the plumbing: that the weights sum to the
  coverage share, that a known historical shock reproduces a known price move
  within the stated interval, that the sign is right, that an unknown region
  fails loudly.
- **Pin everything** in the Dockerfile.
- **Readable over clever.** These inputs are tens of rows.
- **Parameters, not constants.** Economic choices are declared, annotated inputs
  with sensible defaults, and they appear in the output metadata.
- **No emojis** in source files.

### Modelling honesty

This repo publishes numbers that look tradeable. Nothing else here matters more
than getting the labelling right:

- **It is an implied impact, never a forecast and never advice.** The output is
  the price move implied by a stated physical shock under a stated transmission.
  It is not a price prediction, not a trading signal, and not investment advice.
  Say that in the `not_for` annotation, in the README, and in the output
  document itself -- not only in one of the three.
- **Every headline number carries its uncertainty in the same object.** A point
  estimate without an interval beside it is not shippable here. Where the
  interval comes from a fit, report the fit statistics too; where it comes from
  an assumption, say which assumption.
- **State the coverage share; never silently scale to 100%.** The ten states
  node 1 defines are 82.5% of US corn grain production in the 2022 Census.
  A national figure built from them is a ten-state figure, labelled as one.
- **Rescale before you transmit.** A simulated anomaly and a real yield anomaly
  are different quantities. Any step from one to the other is a documented,
  checkable transformation with its own provenance, not an implicit assumption.
- **Cite the transmission, and name what it does not capture.** A price response
  taken from the literature or fitted from history embeds a particular sample,
  a particular market structure and a particular kind of shock. Name the source,
  the sample, and the effects it leaves out.
- **Seasonality is expected, not a defect.** Early in the season node 2's
  projection is mostly climatology and its anomaly is near zero by construction,
  so node 3's price impact is near zero too. Say so rather than tuning it away.

### Determinism

Like `corn-yield` and unlike `crop-weather`, bundles here make **no network
calls at run time**: they are pure functions of their input plus committed
tables. Production weights, historical yields and prices, and any fitted
coefficient are produced only by one-time committed build scripts, and the
committed artifact is the CSV, not the multi-hundred-megabyte export it was
read from. State this in the bundle README and the Modelfile annotations, and
keep it true -- the moment a runner fetches, the determinism claim changes
category and the output must start stamping `retrieved_at`, the source and the
endpoints the way node 1 does.

A committed table has a vintage, and a vintage goes stale. Every built table
ships a `*.meta.json` recording the source URL, the file's server-side
`last-modified`, the period covered and the build date, and the runner puts
that vintage in its output metadata so a reader can see how old the economics
are.

### Region identity

Bundles in this flow share one region set, and **node 1 owns it**. The
`region_key` originates in `agromet-bundles/crop-weather/regions.csv` -- `ia,
il, mn, ne_irrigated, ne_rainfed, in, sd, oh, wi, ks_irrigated, ks_rainfed, mo`
-- and propagates unchanged. Node 1 splits a state into an irrigated and a
rainfed stratum when irrigation covers 20% or more of its harvested corn acres,
which is Nebraska and Kansas; `ne` and `ks` no longer exist. **A region's
stratum is declared in a committed table, never inferred from the spelling of
its key**, the discipline node 2 asserts for its water regime. This repo joins
on the key and adds its own attributes (production weights, irrigation shares,
historical yield distributions); it never redefines the key, and an input
`region_key` with no matching table row **fails the run loudly** rather than
being dropped.

### Data sources

The USDA bulk exports are keyless and are the same ones node 1 used, which is
why they are preferred here:

- **`https://www.nass.usda.gov/datasets/qs.census2022.txt.gz`** (~310 MB,
  stable filename) -- the 2022 Census of Agriculture. Node 1 took its
  county-level production-weighted region points from it; this repo takes
  state-level `CORN, GRAIN - PRODUCTION, MEASURED IN BU`,
  `CORN, GRAIN - ACRES HARVESTED` and
  `CORN, GRAIN, IRRIGATED - ACRES HARVESTED` from the same file and the same
  vintage. `(D)` means NASS withheld the value for disclosure; treat it as
  missing, never as zero.
- **`https://www.nass.usda.gov/datasets/qs.crops_<YYYYMMDD>.txt.gz`** (~1.1 GB)
  -- the survey series, for annual state yields. The **filename carries a date
  that changes**, so a build script must discover the current name and record
  it in its `meta.json` rather than hard-coding one.
- The Quick Stats **API needs a key** (it returns 401 without one). The bulk
  files do not. Use the bulk files.

## How features are built: `feat`

Features are developed from versioned briefs with the vendored
[`feat`](./.claude/skills/feat/SKILL.md) skill, so the brief, the plan and the
implementation land together in one pull request:

1. `/feat create <name>` scaffolds `docs/features/NNNN-<name>.md`. Hand-written
   briefs in the same template are fine.
2. `/feat plan <name>` writes `docs/plans/NNNN-<name>.md` and stops. John reviews
   and revises the plan before anything is built.
3. `/feat run <name>` implements the approved plan on `feat/NNNN-<name>` and
   stops at the pull request. It never merges, releases or deploys.

Repo-wide conventions live in this file; briefs reference them rather than
restating them.

## The `corn-price/` bundle

**US Corn Price Impact.** Takes node 2's `corn_yield_snapshot` and returns the
corn price impact the season's weather implies: each state's signal placed on a
real-world scale, combined by production weight into a national yield and
production shock, and the price response that implies under a fitted
transmission, with an uncertainty range beside every price figure. Brief:
`docs/features/0001-corn-price.md`; plan with every decision and its reasoning:
`docs/plans/0001-corn-price.md`. User-facing documentation:
[`corn-price/README.md`](./corn-price/README.md).

```
corn-price/
  Modelfile.toml            one input (node 2's snapshot), two JSON outputs
  Dockerfile                python:3.12-slim, no pip layer at all
  runner.py                 the model
  production_weights.csv    per-region stratum, production share and irrigation share
  yield_history.csv         observed detrended yields, 1995-2024 (360 rows)
  price_history.csv         national balance sheet, exports and price, 1975-2026 (52 rows)
  transmission.json         the committed fit, its selection trail and bootstrap
  *.meta.json               provenance for the three built tables
  build_weights.py          one-time weights build (not in the image)
  build_yield_history.py    one-time observed-yield build (not in the image)
  build_price_history.py    one-time balance-sheet build (not in the image)
  build_transmission.py     one-time fit (not in the image)
  check_price.py            validation, needs Python 3.11+ (not in the image)
  sample_input.json         a real twelve-region node 2 output
  README.md
```

### Design notes

- **The rank is used, not the percentage. This is the one thing to understand
  before changing anything here.** Node 2's `yield_anomaly_pct` comes from an
  uncalibrated point simulation and is 3.1 to 9.2 times more dispersed
  than the observed distribution of the same state's yield around trend. Its
  *rank* survives that; its *magnitude* does not. Each region's
  `yield_percentile_rank` is quantile-mapped onto that state's observed
  1995-2024 detrended distribution. On the committed sample the unmapped
  national figure would have been +2.83% against the +0.19% actually used.
- **The mapping is median-centred**, because node 2's anomaly is median-relative
  and a rank of 50 must therefore imply a zero shock. A least-squares trend
  through a left-skewed yield series also sits below the median, so uncentred
  quantiles would carry a standing positive offset (about +2.4 points in Ohio).
- **Strata get a rescaled state distribution, because no per-stratum history
  exists.** NASS publishes an annual state-level `CORN, GRAIN, IRRIGATED - YIELD`
  and a `NON-IRRIGATED` counterpart for NE and KS, but **both end in 2018** --
  24 of the 30 years, missing the 2022 drought. So a stratum's distribution is
  its state's 1995-2024 series with its spread multiplied by the stratum/state
  dispersion ratio measured over the overlapping years (NE 0.495 / 2.108, KS
  0.617 / 1.936), placed on the stratum's own fitted trend level. Irrigation
  damps real yield variation by about half; reusing the state distribution
  unchanged would have overstated an irrigated stratum's volatility twofold.
  **The measured ratios are then normalised by the RUNNER, against the weights
  it is about to use**, because marginal spreads only add linearly when the
  strata move together and they do not -- they correlate 0.31 in NE and 0.80 in
  KS. Un-normalised, the split would have inflated Kansas's say in the national
  figure by 39%. Normalising in the committed table instead would have been
  exact for no run at all: the runner weights by acres times each region's own
  trend yield at the run's year, and because the strata have their own trend
  slopes that basis drifts away from census production shares (NE +3.3% in 2024,
  +5.1% in 2040). Doing it at aggregation time makes the identity exact at every
  year, and it is checked in the runner's own weights, not in the table's.
  **The assumption this rests on -- that a stratum's year-to-year shape is its
  state's -- is the weakest claim in the bundle** and is named in the output's
  `not_captured`. Brief 0003.
- **The stratum is cross-checked against node 2, not just declared.** The
  runner compares `production_weights.csv`'s stratum against node 2's
  `metadata.baselines.regions[key].regime` and refuses to start on a mismatch or
  on an absent regime. An irrigated distribution is about half the width of a
  rainfed one, so a stale snapshot would be a silently wrong answer -- the same
  finding Copilot raised one node upstream, one field over.
- **The periods are coupled.** Node 2's baseline window and `yield_history.csv`
  must be the same, or rank *n* stops meaning the same thing on both sides. The
  runner refuses to start on a mismatch rather than answering wrongly.
- **Weights are acres times trend yield, not 2022 production.** Combining
  relative anomalies needs production weights, but 2022 actual production embeds
  2022's own western drought. The trend also belongs here, since node 2's
  baseline is fixed-weather by design.
- **Coverage is stated and never scaled up.** 82.47% of US corn-for-grain
  production, computed from the census by the build script. Running on a subset
  reports that subset's coverage.
- **The transmission is fitted here, not cited.** Roberts and Schlenker's
  multiplier is for a permanent world shift; farmdoc's -1.84 is fitted against
  market expectation rather than a weather baseline. Neither matches this input,
  so both ship in the output as cited reference points that are not used.
- **Term selection was by a rule fixed before the fits were run** (|t| >= 2.0),
  applied to every candidate, and the whole trail is in `transmission.json`.
- **Stocks-to-use did not survive, and that is reported as a finding.** Every
  form tried came out at |t| < 0.1. The literature's convex relationship is about
  the price *level*; this dependent variable is a *change*, which differences it
  away. The model does not claim an amplification it cannot demonstrate.
- **The interval is a bootstrap on the transmission coefficient**, not on the
  price. It says how well the relationship is known from fifty years, not what
  corn will do.
- **No pip install layer.** Weighted sums, an empirical quantile and one
  coefficient. The standard library is enough.
- **Export exposure is carried but never read.** `price_history.csv` holds
  `exports_mil_bu` and `export_share_of_use` from the same ERS Table 4 as the
  stocks series, and every run copies the current and latest-complete figures
  into `metadata.export_exposure`. Nothing in the arithmetic touches them. They
  exist so a downstream trade-policy model can size a lost-sales scenario
  against this bundle's own vintage instead of re-sourcing the balance sheet.
  **Exports are a component of total use, not an addition to it**, and the
  transmission prices no trade scenario of any kind -- that is stated in
  `not_captured`, in the output's `role` string and in the Modelfile `not_for`.
  Brief 0002.

### Verified results (2026-09-21)

- `check_price.py`: **157/157 checks pass** (63, 73, 95, then 133/148/154/157
  across brief 0003's two Copilot reviews and the normalisation fixes).
- **The 2012 drought, end to end, on PUBLISHED stratum observations:** the four
  strata are ranked against USDA's own 1995-2018 irrigated/non-irrigated series
  rather than against this model's rescaled reconstruction, so the case tests
  the reconstruction instead of confirming it. Result **-21.52%** against the
  actual **-22.23%** -- 0.71 points, *worse* than the ten-region model's 0.31,
  and that gap is the shared-shape assumption paid for honestly. Ranking the
  strata against their own rescaled series would have shown 0.26 and proved
  nothing. The case is dated 2012 as well as ranked 2012, so
  the weights use 2012 trend yields -- getting that wrong was a review finding. That validates the mapping, the weighting and the
  coverage assumption against a real outcome rather than against themselves.
- A tenth-percentile season everywhere: -9.73% US shock, **+7.91%** [+3.90,
  +13.15] price impact. Right sign, plausible size.
- Sample run (twelve regions, real node 1 -> node 2 chain): covered shock
  +0.16%, US shock +0.13% over 82.5% of production, price impact **-0.10%**
  [-0.17, -0.05] = -$0.005/bu on $4.80. Under a second.
- **Splitting a state is weight-neutral nationally.** Backtested on the national
  yield deviation 1995-2018 (n=24), the twelve-region model reproduces the
  ten-region model's RMSE of 1.19 points exactly. The split buys stratum
  resolution, not national accuracy, and the honest claim is that it costs none.
- **Transmission:** `dlog_price = a + b0*d + c*d[t-1]`, 1976-2025, n=50.
  b0 = **-0.7826** (se 0.2834, t -2.76), bootstrap 95% [-1.269, -0.393],
  R^2 0.329.
- **Docker build and run** produce output **identical** to the local run apart
  from `generated_at`, with `--network none`.
- **Modelfile validates** (`OK`, no annotation warnings), and
  `check_schema_compatibility` confirms the input binds node 2's
  `corn_yield_snapshot` and is refused by `corn_yield_trajectory`.
- **Copilot review (PR #1):** ten findings, all legitimate, all addressed. The
  sharpest was that the 2012 validation changed only the ranks and left the
  sample's 2026 dates, so it weighted a 2012 episode with 2026 trend yields and
  was not testing the episode it claimed to. Checks went 63 -> **73**. No
  committed table or coefficient changed.
- **Not yet verified:** the Model Home import, which needs a signed-in human at
  the Auth0 login.

### Task list

1. Add the model on the local Model Home stack from the branch subfolder URL and
   run it with a twelve-region node 2 output; mark the PR ready once it passes.
2. After merge: register on Model Home from `main` and compose it after the US
   Corn Yield model in the daily flow. Brief 0003 changed the region set, so
   **node 2 must be re-registered too** and any stored flow input from before
   the split is stale.
3. Follow-ups, detailed in the bundle README: replacing the stratum rescaling
   with measurement if NASS resumes the series; a live or market reference price
   (three routes, cheapest first), five-year average production weights, a
   within-season futures transmission, and stocks-to-use revisited in a
   price-level specification.

## Task list

1. ~~Create `modelhome/ag-commodity-bundles` on GitHub and push `main`.~~ Done
   2026-09-20.
2. Finish `corn-price/` (brief 0001): see that bundle's task list above.
3. Upstream follow-up for `wofost-bundles/corn-yield`: an irrigation-aware run
   for Nebraska and Kansas. Quantile mapping here compresses the rainfed bias but
   cannot fix a misranked season, and that is node 2's to fix.
