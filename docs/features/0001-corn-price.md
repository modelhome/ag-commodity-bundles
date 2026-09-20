# Corn price

## Outcome

`corn-price/` is a self-contained Model Home model that takes a node 2
(`wofost-bundles/corn-yield/`) `corn_yield_snapshot` and returns the corn price
impact that season's weather implies: a production-weighted national yield and
production shock built from the per-region anomalies, and the price response
that shock implies, with the reasoning and its limits legible in the output.

Pasting the subfolder's GitHub URL into
`http://localhost:5173/models/new/repo` creates a working model, and Model Home
composes it after `corn-yield` in a flow that runs daily.

This is node 3 of a three-node climate -> agriculture -> finance flow: forecast
weather -> corn crop model -> corn commodity price impact. Node 3 is where a
physical crop signal becomes a market quantity. Its input is node 2's output,
unchanged: they are semantic peers. Node 3 consumes the anomaly; it never
re-runs a crop model and never redefines the region set.

Node 3's output looks more like a tradeable number than anything else in the
flow, and it is not one. It is the price impact implied by a weather-driven
production shock under stated assumptions, reported with its uncertainty.

## Scope

### In scope

**Repo scaffold.** Top-level `.gitignore`, `.dockerignore`, `LICENSE` (MIT),
`README.md` and `CLAUDE.md` matching `agromet-bundles` and `wofost-bundles`,
plus the vendored `feat` skill, then `corn-price/` beneath it.

**The `corn-price/` bundle:** `Modelfile.toml`, `Dockerfile`, `runner.py`, a
sample input that is a **real node 2 output**, the committed static tables, the
build scripts for any sourced ones, and a validation check run outside the
image.

**Input contract.** Node 3's input schema *is* node 2's output schema. Flow
steps bind by required-key set, so declaring
`required = ["metadata", "columns", "rows"]` binds `corn_yield_snapshot` and
cannot bind `corn_yield_trajectory`. Verify that against the platform's
`check_schema_compatibility` before building, as node 2 did. The region set
comes from the input; node 3 joins its own tables on node 1's `region_key` and
fails loudly on a key it has no row for.

**Production weights per state**, keyed on node 1's `region_key`, so the ten
per-region anomalies can be combined into one national shock. USDA NASS is the
natural source -- the same 2022 Census of Agriculture node 1 used to place its
region points, so the two nodes share a vintage.

**The national production shock.** Combine the per-region yield anomalies with
those weights and a USDA production / trend-yield level, and state the ten-state
coverage share explicitly.

**The price response.** A documented, cited transmission from production shock
to price, with its assumptions and its uncertainty reported alongside the
number.

**Output.** A JSON document following the `corn-yield` / `crop-weather`
convention, designed so a future SPA or report can read it whole: the per-region
contributions, the national shock, the price impact, and the assumptions and
uncertainty that produced them.

### Out of scope

- Nodes 1 and 2, and the flow definition, which lives in the platform: no
  Flowfile.
- Re-deriving anything node 2 already produces. Node 3 consumes the anomaly; it
  does not re-run a crop model.
- Trading signals, position sizing, or anything that reads as investment advice.
- The SPA and any downstream visualisation.
- Redefining regions. Node 3 reads the region set from its input.

## Acceptance criteria

- **AC-1** -- `modelhome/ag-commodity-bundles` exists with top-level
  `.gitignore`, `.dockerignore`, `LICENSE` (MIT), `README.md`, `CLAUDE.md` and
  the vendored `feat` skill, plus a `corn-price/` subfolder, matching
  `agromet-bundles` and `wofost-bundles` conventions.
- **AC-2** -- `corn-price/` contains `Modelfile.toml`, `Dockerfile`,
  `runner.py`, a sample input JSON, the committed static tables, the build
  scripts for the sourced ones, and a validation check.
- **AC-3** -- The sample input is a **real node 2 `corn_yield_snapshot`**,
  committed, so the model runs standalone.
  `python corn-price/runner.py corn-price/<sample_input>` runs end to end and
  writes the declared outputs.
- **AC-4** -- `docker build` from the bundle folder succeeds and
  `docker run --network none` reproduces identical outputs. The model needs no
  network at run time.
- **AC-5** -- Production weights are keyed on node 1's `region_key`, sourced and
  dated, and the **ten-state coverage share is computed from the source and
  reported in the output**. The national figure is never silently scaled to
  100% of US corn.
- **AC-6** -- The per-region anomalies combine into a national yield and
  production shock by a documented weighting, and the arithmetic is verified
  against a hand-worked case in the committed check.
- **AC-7** -- Node 2's simulated anomaly is placed on a real-world scale before
  any price transmission is applied, by a documented and checkable
  transformation with its own provenance. The check demonstrates that a node 2
  anomaly of a given size produces a national shock of a plausible size.
- **AC-8** -- The rainfed bias in node 2's Kansas and Nebraska anomalies is
  handled by a documented choice, and the choice is visible in the output
  (per-region, so a reader can see what it did) and in the README.
- **AC-9** -- The price response comes from a cited transmission with a stated
  sample and stated assumptions, and **every price number ships with an
  uncertainty range in the same object**. The check verifies the sign, the
  magnitude against at least one known historical episode, and that no headline
  number appears without its interval.
- **AC-10** -- An input `region_key` with no matching table row fails the run
  loudly, naming the key and the table. Regions are never silently dropped.
- **AC-11** -- With a node 2 output as input and no other parameters, the model
  runs the full region set using the committed defaults, so the scheduled flow
  needs no manual parameters.
- **AC-12** -- The Modelfile's `not_for` annotation is explicit that the output
  is not a price forecast and not advice, and the same statement appears in the
  output document and the README. Every key in a `required` array has a declared
  `properties.<key>.type`; `validity_domain` is within 600 characters and
  `provenance` within 400.
- **AC-13** -- Pasting the `corn-price/` subfolder GitHub URL into
  `http://localhost:5173/models/new/repo` creates a working model whose run
  produces the expected artifacts, and it binds after `corn-yield` in a flow.
- **AC-14** -- The bundle README documents: the input contract; every table's
  source, vintage and method; the coverage share; the rescaling from node 2's
  simulated anomaly to a real-world scale; the irrigation handling; the
  transmission, its citation, its sample and what it does not capture; the
  uncertainty construction; and the determinism and offline semantics.

## Constraints and dependencies

- **Node 2's anomaly is a rainfed simulation.** Much of the corn in **Kansas and
  Nebraska is irrigated** -- 25.4% and 52.7% of harvested corn acres
  respectively in the 2022 Census, against 1.2% in Iowa -- so those regions'
  weather response is overstated (Kansas's simulated baseline median is about
  1,500 kg/ha against a real state average several times that). Naive
  production-weighting propagates that bias into the national figure. Decide how
  to handle it and document the choice.
- **Node 2's anomaly is also far more dispersed than a real yield anomaly.** Its
  thirty-year baseline distributions span 81 to 13,414 kg/ha for Iowa and 0 to
  5,795 kg/ha for Kansas, so a percentage off those medians is several times the
  spread of a real state yield deviation from trend. Multiplying it straight
  through a price transmission is wrong by a factor, not by a margin.
- **The ten states are 82.5% of US corn grain production** (2022 Census of
  Agriculture, `CORN, GRAIN - PRODUCTION, MEASURED IN BU`, the ten `region_key`
  states against the national total), not all of it. State the coverage share in
  the output; never silently scale to 100%.
- **Node 2's baseline carries no yield trend by design** -- it measures weather
  only, against a fixed-weather reference. Genetics and management raise real
  yields steadily, so the trend belongs here, applied to a real USDA level.
- **Never present the output as a price forecast or as advice.** It is the price
  impact implied by a weather-driven production shock under stated assumptions.
  Report the assumptions and an uncertainty range beside every number, and make
  the model's `not_for` annotation explicit about this.
- **No network at run time** unless the price-source decision says otherwise,
  and if it does, stamp `retrieved_at`, the source and the endpoints in the
  output metadata as node 1 does.
- **Every key in a Modelfile `required` array needs a declared
  `properties.<key>.type`**; `validity_domain` is capped at 600 characters and
  `provenance` at 400. The schema format has **no nullable type**, so a field
  that can be empty must not appear in any `required` list -- and if a required
  field could go null, fix the model rather than the declaration.
- **Region key originates in node 1.** Key every table on it, fail loudly on a
  key with no row, and never invent one.
- **Licence** MIT.

Repo-wide conventions -- mirroring `bond/`, `crop-weather/` and `corn-yield/`,
the Modelfile and runner contracts, the platform facts, the upstream contract,
unit discipline, modelling honesty, determinism, region identity and the USDA
bulk data sources -- live in [`CLAUDE.md`](../../CLAUDE.md) and are not restated
here.

## General guidance

- Before you write the plan, ask any questions you need to in order to best
  implement the brief. **Three are expected to be blocking**, and none of them
  should be guessed:

  1. **Where the price data comes from, and what that does to determinism.**
     Node 2 is strictly offline -- a pure function of its input and committed
     tables. A run-time price fetch would make node 3 "deterministic given its
     inputs and upstream data as of retrieval", the same category as node 1. A
     committed historical price table plus a current-price input keeps it
     offline. Note that `yfinance-bundles/historical-ohlcv` already exists and
     describes itself as the upstream market-data step in a finance flow --
     evaluate composing with it before writing a fetcher. Recommend one and say
     why.
  2. **The yield -> price transmission model.** This is the substantive
     modelling decision and the easiest place in the whole flow to produce a
     confident-looking wrong number. Options include a documented constant price
     elasticity of demand, a fitted historical relationship between yield
     surprise and price move, and a stock-to-use-conditioned response (the same
     production shock moves price far more in a tight-stocks year than a loose
     one). Research what the agricultural economics literature actually
     supports, cite it, and be explicit about which effects the chosen approach
     does and does not capture.
  3. **Which node 2 statistic to use.** `yield_anomaly_pct` is the magnitude you
     can multiply by production. `yield_percentile_rank` is more robust where
     the distribution is skewed -- and node 2's is. Decide which drives the
     calculation and which is carried as context.

- **Read node 2 first**, especially `wofost-bundles/docs/plans/0001-corn-yield.md`.
  It records where the original design broke and why, which is the best
  available guide to the failure modes in this flow.
- **Verify claims by running things**, not by reading about them. Node 2's two
  worst defects -- PCSE downloading its crop parameters with a seven-day cache,
  and PCSE printing to stdout on first import -- were both invisible until the
  model was run in a clean container with `--network none`.
- Read `wofost-bundles/corn-yield/` and `QuantLib-bundles/bond/` as the
  authoritative templates for `Modelfile.toml` format, the `runner.py` I/O
  contract, the Dockerfile pattern, `.gitignore` / `.dockerignore` and
  `CLAUDE.md` conventions. Match them; don't invent new ones. Consistency across
  the bundles matters more than any local preference.
- Document every modelling choice in the README and the Modelfile
  validity-domain annotations.
