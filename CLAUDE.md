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
il, mn, ne, in, sd, oh, wi, ks, mo` -- and propagates unchanged. This repo joins
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

## Task list

1. Create `modelhome/ag-commodity-bundles` on GitHub and push `main`.
2. Build `corn-price/` (brief `docs/features/0001-corn-price.md`).
