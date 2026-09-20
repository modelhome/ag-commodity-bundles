# ag-commodity-bundles

Standalone [Model Home](https://modelhome.run) model bundles for agricultural
commodity markets: the layer that turns a physical crop signal into a market
consequence. Each subfolder is a self-contained model: a `Modelfile.toml`, a
`Dockerfile`, a `runner.py`, and sample inputs.

Nothing upstream is vendored here. Libraries these bundles depend on are
installed as pinned pip packages inside each bundle's image.

## Bundles

| Bundle | Model | Inputs -> Outputs |
|---|---|---|
| [`corn-price/`](./corn-price) | US corn price impact: the price move implied by the weather-driven yield shock the US Corn Yield model reports | a `corn-yield` per-region snapshot (nothing else) -> a production-weighted national yield and production shock, the implied corn price impact under a documented transmission, and the assumptions and uncertainty behind both |

`corn-price/` is node 3 of a climate -> agriculture -> finance flow:
[`agromet-bundles/crop-weather/`](https://github.com/modelhome/agromet-bundles)
supplies the daily weather,
[`wofost-bundles/corn-yield/`](https://github.com/modelhome/wofost-bundles)
turns it into a per-state yield signal, and this bundle turns that into a price
impact. The three are composed in a Model Home Flow.

## Quick start

Each bundle builds and runs from its own folder, which is also the build context
Model Home uses:

```bash
cd corn-price
docker build -t ag-commodity-corn-price:local .
docker run --rm ag-commodity-corn-price:local   # needs no network
```

Or, when creating a new model on Model Home, paste the bundle folder's GitHub
URL (for example
`https://github.com/modelhome/ag-commodity-bundles/tree/main/corn-price`) into
the "classic import" option.

Each bundle's README covers its inputs, outputs, data sources, the transmission
it assumes and its limits. See [`CLAUDE.md`](./CLAUDE.md) for the design notes,
the conventions every bundle follows, and how features are briefed, planned and
built.

## What these numbers are not

Nothing here is a price forecast, a trading signal, or investment advice. A
bundle in this repo reports the price impact *implied by* a stated physical
shock under a stated, cited transmission, with its assumptions and its
uncertainty printed beside the number. Read the bundle README before using a
figure for anything.

## Licence

MIT. See [`LICENSE`](./LICENSE). Each bundle's README carries the full
attribution for its own data tables.
