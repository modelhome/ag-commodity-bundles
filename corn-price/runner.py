#!/usr/bin/env python3
"""
US Corn Price Impact -- node 3 of the climate -> agriculture -> finance flow.

Takes a `corn_yield_snapshot` from wofost-bundles/corn-yield (node 2) and
returns the corn price impact that season's weather implies.

    python runner.py <corn_yield_snapshot.json> [<corn_price_regions.output.json>]

The result document goes to stdout and nothing else does; logs go to stderr.
The platform redirects stdout into run/corn_price_impact.output.json and passes
the regions-table path as the second argument.

What the model does, in order:

1.  Reads node 2's per-region rows. The region set comes from the input; this
    model never defines its own and fails loudly on a key it has no table row
    for.

2.  Places each region's signal on a real-world scale by QUANTILE MAPPING. Node
    2's `yield_anomaly_pct` is the anomaly of an uncalibrated, rainfed,
    single-point WOFOST simulation, and its thirty-year distributions are two
    and a half to eleven times more dispersed than the observed distribution of
    that state's yield around trend. The percentage therefore cannot be
    multiplied by production. What survives is the RANK, so each region's
    `yield_percentile_rank` is read as the same quantile of that state's
    observed detrended yield distribution over the same 1995-2024 window.

3.  Combines the mapped anomalies into a national shock with production weights
    -- harvested acres times trend yield, because combining relative anomalies
    needs production weights and 2022's actual production embeds 2022's own
    drought in exactly the western states this model is careful about.

4.  Applies the committed transmission to the national shock and reports the
    implied price impact with the bootstrap interval on the transmission
    coefficient.

The output is the price impact IMPLIED BY a stated physical shock under a
stated transmission. It is not a price forecast, not a trading signal and not
investment advice.

Deterministic and offline: a pure function of the input document and the
committed tables. No network calls, no randomness, no wall-clock dependence
beyond the `generated_at` stamp.
"""
import csv
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent

WEIGHTS_PATH = HERE / "production_weights.csv"
YIELD_HISTORY_PATH = HERE / "yield_history.csv"
YIELD_HISTORY_META_PATH = HERE / "yield_history.meta.json"
PRICE_HISTORY_META_PATH = HERE / "price_history.meta.json"
WEIGHTS_META_PATH = HERE / "production_weights.meta.json"
TRANSMISSION_PATH = HERE / "transmission.json"

NOT_A_FORECAST = (
    "This is the corn price impact implied by a weather-driven production shock under "
    "the stated transmission, holding everything else equal. It is not a price forecast, "
    "not a trading signal and not investment advice. The transmission explains only part "
    "of what moves the corn price; see fit.r2 and interval_definition."
)


class RunError(Exception):
    """A condition the run cannot continue past, reported without a traceback."""


def log(message):
    print(message, file=sys.stderr)


# ---------------------------------------------------------------- input tables

def load_weights():
    """{region_key: {...}} from production_weights.csv."""
    with open(WEIGHTS_PATH, newline="") as fh:
        rows = list(csv.DictReader(fh))
    out = {}
    for row in rows:
        out[row["region_key"]] = {
            "state": row["state"],
            "production_bu": float(row["production_bu"]),
            "acres_harvested": float(row["acres_harvested"]),
            # Blank means NASS withheld the figure for disclosure. It is carried
            # through as None, never read as "this state does not irrigate".
            "irrigated_share": (
                float(row["irrigated_share"]) if row["irrigated_share"] else None
            ),
            "production_share_of_us": float(row["production_share_of_us"]),
        }
    return out


def load_yield_history():
    """({region_key: [deviation_pct, ...] sorted}, {region_key: {years}}, meta)."""
    with open(YIELD_HISTORY_PATH, newline="") as fh:
        rows = list(csv.DictReader(fh))
    deviations = {}
    years = {}
    for row in rows:
        key = row["region_key"]
        deviations.setdefault(key, []).append(float(row["deviation_pct"]))
        years.setdefault(key, set()).add(int(row["year"]))
    for key in deviations:
        deviations[key].sort()
    return deviations, years, json.loads(YIELD_HISTORY_META_PATH.read_text())


# --------------------------------------------------------------- the modelling

def quantile(sorted_values, fraction):
    """Linear-interpolated empirical quantile of an already-sorted list."""
    if not sorted_values:
        raise RunError("cannot take a quantile of an empty distribution")
    if len(sorted_values) == 1:
        return sorted_values[0]
    position = fraction * (len(sorted_values) - 1)
    low = int(math.floor(position))
    high = min(low + 1, len(sorted_values) - 1)
    weight = position - low
    return sorted_values[low] * (1 - weight) + sorted_values[high] * weight


def quantile_map(percentile_rank, observed_deviations, region_key=None):
    """Node 2's percentile rank read as the same quantile of the observed distribution.

    Measured against the observed MEDIAN, not against the trend line, for two
    reasons. Node 2's own anomaly is median-relative -- it divides by the median
    of its thirty baseline runs -- so a rank of 50 means "an unremarkable
    season" and has to map to a shock of zero here too, or every ordinary season
    would imply a price impact. And a least-squares trend through a left-skewed
    yield series sits below the median, so quantiles read straight off the trend
    line carry a standing positive offset (about +2.4 points in Ohio) that has
    nothing to do with this season's weather.

    Only the SCALE of the deviation matters downstream: the transmission's
    reported effect is the marginal one, b0 * d, so shifting d's origin changes
    nothing about the impact while making an average season report zero.
    """
    if percentile_rank is None:
        where = f"{region_key}: " if region_key else ""
        raise RunError(
            f"{where}yield_percentile_rank is missing. It drives the whole calculation, "
            f"because node 2's percentage is not on a real-world scale."
        )
    fraction = min(max(percentile_rank / 100.0, 0.0), 1.0)
    median = quantile(observed_deviations, 0.5)
    return quantile(observed_deviations, fraction) - median


def dispersion_ratio(node2_baseline, observed_deviations):
    """How much wider node 2's simulated distribution is than the observed one.

    Both as a p10-to-p90 spread in percentage points. Node 2 publishes its own
    baseline quantiles in the input metadata, so this is computed from the
    document in hand rather than from a second committed copy of node 2's
    numbers that could drift away from it.

    This is a property of the two distributions, not of this season, which is
    why it is the figure check_price.py asserts is above 1 for every region. A
    per-observation ratio would dip below 1 whenever a simulated anomaly
    happened to land near zero, and would say nothing about over-dispersion.
    """
    if not node2_baseline:
        return None
    median = node2_baseline.get("median_kg_ha")
    p10 = node2_baseline.get("p10_kg_ha")
    p90 = node2_baseline.get("p90_kg_ha")
    if not median or p10 is None or p90 is None:
        return None
    simulated_spread = (p90 - p10) / median * 100.0
    observed_spread = (
        quantile(observed_deviations, 0.9) - quantile(observed_deviations, 0.1)
    )
    if observed_spread <= 0:
        return None
    return simulated_spread / observed_spread


def trend_yield(trends, region_key, year):
    """The fitted trend yield in bu/acre for a region in a calendar year."""
    trend = trends.get(region_key)
    if trend is None:
        raise RunError(f"{region_key}: no trend coefficients in yield_history.meta.json")
    return trend["intercept_bu_acre"] + trend["slope_bu_acre_per_year"] * year


def check_periods(node2_metadata, yield_meta):
    """Node 2's baseline window and this model's observed window must be the same.

    A percentile rank from node 2 is read as the same quantile of this model's
    distribution. If the two windows differ, rank n stops meaning the same thing
    on both sides and the mapping is quietly wrong, so this is a failed run
    rather than a wrong answer.
    """
    baselines = node2_metadata.get("baselines") or {}
    upstream_period = baselines.get("period")
    local_period = yield_meta.get("period")
    if not upstream_period:
        raise RunError(
            f"the upstream document declares no metadata.baselines.period, so there is no "
            f"way to tell whether its percentile ranks were computed over the same window "
            f"as yield_history.csv ({local_period}). A rank read against the wrong window "
            f"is a silently wrong answer, so this is a failed run rather than an "
            f"assumption. Run against an upstream model that publishes its baseline period."
        )
    if upstream_period != local_period:
        raise RunError(
            f"period mismatch: the upstream model's baseline covers {upstream_period} "
            f"but yield_history.csv covers {local_period}. A percentile rank from one "
            f"cannot be read against the other. Rebuild yield_history.csv over "
            f"{upstream_period}, or run against an upstream model built on {local_period}."
        )
    return local_period, upstream_period


# ------------------------------------------------------------------- the model

def process(snapshot, weights, deviations, observed_years, yield_meta, price_meta,
            weights_meta, transmission, reference_price_override):
    metadata = snapshot.get("metadata") or {}
    rows = snapshot.get("rows")
    if not isinstance(rows, list) or not rows:
        raise RunError("the input document has no rows; expected one per region")

    period, _ = check_periods(metadata, yield_meta)
    expected_years = set(range(yield_meta["period_start"], yield_meta["period_end"] + 1))

    date = metadata.get("date") or (rows[0].get("date") if rows else None)
    if not date:
        raise RunError("no reporting date in the input metadata or rows")
    year = int(str(date)[:4])

    trends = yield_meta["trends"]

    regions = []
    for row in rows:
        key = row.get("region_key")
        if not key:
            raise RunError("a row has no region_key")
        if key not in weights:
            raise RunError(
                f"unknown region_key {key!r}: no row in production_weights.csv. "
                f"Known keys: {', '.join(sorted(weights))}. Regions are defined by the "
                f"weather model upstream and are never invented here."
            )
        if key not in deviations:
            raise RunError(
                f"unknown region_key {key!r}: no rows in yield_history.csv. "
                f"Known keys: {', '.join(sorted(deviations))}."
            )
        observed = deviations[key]
        # The upstream rank refers to a thirty-year distribution, so the observed
        # distribution it is read against must be that same window exactly. A
        # truncated table would shift every empirical quantile while the rank
        # still meant thirty years -- a wrong mapping, not a rough one.
        missing = expected_years - observed_years.get(key, set())
        extra = observed_years.get(key, set()) - expected_years
        if missing or extra:
            raise RunError(
                f"{key}: yield_history.csv does not cover {period} exactly "
                f"({len(observed_years.get(key, set()))} years"
                + (f", missing {sorted(missing)}" if missing else "")
                + (f", unexpected {sorted(extra)}" if extra else "")
                + "). The upstream percentile rank refers to that whole window, so a "
                  "partial table would shift every quantile it is read against."
            )

        simulated_pct = row.get("yield_anomaly_pct")
        rank = row.get("yield_percentile_rank")
        real_pct = quantile_map(rank, observed, key)
        node2_baseline = ((metadata.get("baselines") or {}).get("regions") or {}).get(key)
        ratio = dispersion_ratio(node2_baseline, observed)

        info = weights[key]
        region_trend_yield = trend_yield(trends, key, year)
        weight_raw = info["acres_harvested"] * region_trend_yield

        regions.append({
            "region_key": key,
            "state": row.get("state") or info["state"],
            "date": row.get("date") or date,
            "yield_percentile_rank": rank,
            "yield_anomaly_simulated_pct": simulated_pct,
            "yield_anomaly_real_pct": round(real_pct, 4),
            # How much wider node 2's simulated distribution is than the observed
            # one for this region. Above 1 everywhere is the whole justification
            # for the mapping, and check_price.py asserts it.
            "dispersion_ratio": None if ratio is None else round(ratio, 2),
            "irrigated_share": info["irrigated_share"],
            "acres_harvested": info["acres_harvested"],
            "trend_yield_bu_acre": round(region_trend_yield, 2),
            "production_share_of_us": info["production_share_of_us"],
            "_weight_raw": weight_raw,
        })
        # The Modelfile schema format has no nullable type, so an optional field
        # that has no value is OMITTED rather than emitted as null. Two can be
        # absent legitimately: irrigated_share when NASS withheld the figure for
        # disclosure, and dispersion_ratio when the upstream document carries no
        # baseline quantiles for the region.
        for optional in ("irrigated_share", "dispersion_ratio",
                         "yield_anomaly_simulated_pct"):
            if regions[-1].get(optional) is None:
                del regions[-1][optional]

    total_weight = sum(r["_weight_raw"] for r in regions)
    if total_weight <= 0:
        raise RunError("production weights sum to zero; cannot form a national figure")

    for region in regions:
        region["production_weight"] = round(region["_weight_raw"] / total_weight, 6)
        region["contribution_pct"] = round(
            region["production_weight"] * region["yield_anomaly_real_pct"], 4
        )
        del region["_weight_raw"]

    # Coverage is recomputed from the regions actually present, so running on a
    # subset reports the subset's coverage rather than the full ten states'.
    coverage = sum(r["production_share_of_us"] for r in regions)

    covered_shock_pct = sum(r["contribution_pct"] for r in regions)
    us_shock_pct = covered_shock_pct * coverage

    # The same arithmetic on node 2's raw simulated anomalies, for comparison
    # only. It is reported so the single biggest modelling decision in this node
    # is auditable from its own output rather than taken on trust. It is never
    # used: node 2's percentages are not on a real-world scale.
    unmapped_covered_pct = sum(
        r["production_weight"] * r["yield_anomaly_simulated_pct"]
        for r in regions if r["yield_anomaly_simulated_pct"] is not None
    )

    covered_trend_production_bu = sum(
        r["acres_harvested"] * r["trend_yield_bu_acre"] for r in regions
    )
    covered_production_shock_bu = covered_trend_production_bu * covered_shock_pct / 100.0
    # The uncovered states are assumed to be at trend, so they add no shock. Their
    # production is inferred from the covered share rather than scaled away.
    us_trend_production_bu = covered_trend_production_bu / coverage

    # The transmission was fitted on the NATIONAL yield deviation, so it is the
    # national figure it is applied to.
    b0 = transmission["shock_coefficient"]
    b0_low, b0_high = transmission["shock_coefficient_ci95"]
    shock_fraction = us_shock_pct / 100.0

    def impact_pct(coefficient):
        return (math.exp(coefficient * shock_fraction) - 1.0) * 100.0

    impacts = sorted([impact_pct(b0_low), impact_pct(b0_high)])
    central = impact_pct(b0)

    defaults = price_meta["defaults"]
    reference_price = (
        reference_price_override
        if reference_price_override is not None
        else defaults["reference_price_usd_bu"]
    )

    national = {
        "date": date,
        "regions_covered": len(regions),
        "coverage_share_of_us_production": round(coverage, 6),
        "covered_yield_shock_pct": round(covered_shock_pct, 4),
        "us_yield_shock_pct": round(us_shock_pct, 4),
        "us_yield_shock_pct_unmapped": round(unmapped_covered_pct * coverage, 4),
        "covered_trend_production_bu": round(covered_trend_production_bu, 0),
        "covered_production_shock_bu": round(covered_production_shock_bu, 0),
        "us_trend_production_bu": round(us_trend_production_bu, 0),
        "us_production_shock_bu": round(covered_production_shock_bu, 0),
        "price_impact_pct": round(central, 4),
        "price_impact_pct_low": round(impacts[0], 4),
        "price_impact_pct_high": round(impacts[1], 4),
        "reference_price_usd_bu": reference_price,
        "price_impact_usd_bu": round(reference_price * central / 100.0, 4),
        "price_impact_usd_bu_low": round(reference_price * impacts[0] / 100.0, 4),
        "price_impact_usd_bu_high": round(reference_price * impacts[1] / 100.0, 4),
        # There is deliberately no implied_price_usd_bu. A price LEVEL is the one
        # figure here a reader would take for a forecast, and it adds nothing that
        # reference_price_usd_bu plus price_impact_usd_bu does not already give.
    }

    assumptions = {
        "not_a_forecast": NOT_A_FORECAST,
        "rescaling": (
            "Node 2's yield_anomaly_pct is a simulated, uncalibrated, rainfed anomaly whose "
            "thirty-year distribution is far wider than an observed one, so it is not used "
            "as a magnitude. Each region's yield_percentile_rank is read as the same "
            f"quantile of that state's observed detrended yield distribution over {period}, "
            "measured against that distribution's median so an unremarkable season maps to "
            "zero (quantile mapping). yield_anomaly_simulated_pct and dispersion_ratio ship "
            "per region, and us_yield_shock_pct_unmapped shows what the national figure "
            "would have been without the rescaling, so the correction is auditable from "
            "this document."
        ),
        "weighting": (
            "Relative anomalies are combined with production weights: harvested acres "
            "(2022 Census) times the state's fitted trend yield for this calendar year. "
            "Actual 2022 production is not used as the weight because it embeds 2022's own "
            "drought in the western states."
        ),
        "coverage": (
            f"The regions present are {round(coverage * 100, 2)}% of US corn-for-grain "
            "production. us_yield_shock_pct assumes the remaining acres are at trend and "
            "contribute no shock. The figure is never scaled up to 100%."
        ),
        "trend": (
            "Node 2's baseline is fixed-weather and carries no yield trend by design, so "
            "the trend is applied here: per-state ordinary least squares on the observed "
            f"{period} series, evaluated at {year}."
        ),
        "irrigation": (
            "Node 2 simulates every region as rainfed. Quantile mapping absorbs most of the "
            "resulting bias, because a state's observed distribution already contains its "
            "irrigated acres; irrigated_share ships per region so the exposure is visible. "
            "It does not remove the bias: a rainfed simulation can rank an irrigated state's "
            "dry year too low, and the mapping carries that rank faithfully."
        ),
        "transmission": transmission["specification"],
        "transmission_reported_effect": transmission["reported_effect"],
        "transmission_selection": transmission["selection_rule"],
        "stocks_to_use_finding": transmission["stocks_to_use_finding"],
        "interval_definition": transmission["interval_definition"],
        "not_captured": transmission["not_captured"],
    }

    meta = {
        "date": date,
        "model": "US Corn Price Impact (ag-commodity-bundles/corn-price)",
        "node": "3 of the climate -> agriculture -> finance flow",
        "determinism": (
            "Deterministic and offline: a pure function of the input document and the "
            "committed tables. No network calls, no randomness, no wall-clock dependence."
        ),
        "observed_yield_window": period,
        "transmission": {
            "shock_coefficient": b0,
            "shock_coefficient_ci95": [b0_low, b0_high],
            "terms": transmission["terms"],
            "coefficients": transmission["coefficients"],
            "fit": transmission["fit"],
            "sources": transmission["sources"],
            "reference_estimates_not_used": transmission["reference_estimates_not_used"],
            "built_at": transmission["built_at"],
        },
        "tables": {
            "production_weights": {
                "vintage": weights_meta["vintage"],
                "source": weights_meta["source"],
                "source_last_modified": weights_meta["source_last_modified"],
                "built_at": weights_meta["built_at"],
                "coverage_share_of_us_all_ten": weights_meta["coverage_share_of_us"],
            },
            "yield_history": {
                "vintage": yield_meta["vintage"],
                "source": yield_meta["source"],
                "source_last_modified": yield_meta["source_last_modified"],
                "period": yield_meta["period"],
                "built_at": yield_meta["built_at"],
            },
            "price_history": {
                "vintage": price_meta["vintage"],
                "source": price_meta["source"],
                "source_last_modified": price_meta["source_last_modified"],
                "period": price_meta["period"],
                "built_at": price_meta["built_at"],
                "latest_complete_marketing_year": price_meta["latest_complete_marketing_year"],
            },
        },
        "reference_price": {
            "usd_bu": reference_price,
            "source": (
                "supplied as an input" if reference_price_override is not None
                else f"{price_meta['vintage']}, marketing year "
                     f"{defaults['reference_price_year']}"
                     + (" (WASDE projection, not an outcome)"
                        if defaults["reference_price_is_projection"] else "")
            ),
        },
        "carryin_stocks_to_use": {
            "value": defaults["carryin_stocks_to_use"],
            "marketing_year": defaults["carryin_stocks_to_use_year"],
            "role": (
                "context only. Every stocks-to-use form tried came out statistically "
                "indistinguishable from zero in this specification, so the shipped "
                "transmission does not condition on it."
            ),
        },
        "upstream": {
            "model": "wofost-bundles/corn-yield (node 2)",
            "date": metadata.get("date"),
            "generated_at": metadata.get("generated_at"),
            "baseline_period": (metadata.get("baselines") or {}).get("period"),
            "wofost_engine": metadata.get("wofost_engine"),
            "yield_anomaly_definition": metadata.get("yield_anomaly_definition"),
        },
    }

    return national, regions, assumptions, meta


# -------------------------------------------------------------------- plumbing

REGION_COLUMNS = [
    "region_key", "state", "date",
    "yield_percentile_rank", "yield_anomaly_simulated_pct", "yield_anomaly_real_pct",
    "dispersion_ratio", "production_weight", "contribution_pct",
    "production_share_of_us", "irrigated_share",
    "acres_harvested", "trend_yield_bu_acre",
]


def read_json(path):
    try:
        with open(path) as fh:
            return json.load(fh)
    except FileNotFoundError:
        raise RunError(f"input file not found: {path}")
    except json.JSONDecodeError as exc:
        raise RunError(f"input file is not valid JSON: {path}: {exc}")


def optional_number(document, key, positive=False):
    """A present-but-empty value falls back exactly as a missing key does.

    `float()` happily parses "nan", "inf" and negatives, none of which is a price.
    A NaN would also travel out as a bare `NaN` token, which is not valid JSON.
    """
    value = document.get(key)
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise RunError(f"{key} must be a number, got {value!r}")
    if not math.isfinite(number):
        raise RunError(f"{key} must be a finite number, got {value!r}")
    if positive and number <= 0:
        raise RunError(f"{key} must be greater than zero, got {number}")
    return number


def main(argv):
    if len(argv) < 2:
        raise RunError(
            "usage: runner.py <corn_yield_snapshot.json> [<corn_price_regions.output.json>]"
        )
    snapshot = read_json(argv[1])
    regions_path = argv[2] if len(argv) > 2 else None

    weights = load_weights()
    deviations, observed_years, yield_meta = load_yield_history()
    price_meta = json.loads(PRICE_HISTORY_META_PATH.read_text())
    weights_meta = json.loads(WEIGHTS_META_PATH.read_text())
    transmission = json.loads(TRANSMISSION_PATH.read_text())

    reference_price = optional_number(snapshot, "reference_price_usd_bu", positive=True)

    national, regions, assumptions, meta = process(
        snapshot, weights, deviations, observed_years, yield_meta, price_meta,
        weights_meta, transmission, reference_price,
    )

    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    for region in regions:
        log(f"  {region['region_key']}: rank {region['yield_percentile_rank']}, "
            f"simulated {region['yield_anomaly_simulated_pct']:+.1f}% -> "
            f"real {region['yield_anomaly_real_pct']:+.2f}% "
            f"(dispersion x{region['dispersion_ratio']}), "
            f"weight {region['production_weight']:.3f}")
    log(f"covered shock {national['covered_yield_shock_pct']:+.2f}%, "
        f"US shock {national['us_yield_shock_pct']:+.2f}% "
        f"over {national['coverage_share_of_us_production'] * 100:.1f}% of production "
        f"(unmapped would have been {national['us_yield_shock_pct_unmapped']:+.2f}%)")
    log(f"price impact {national['price_impact_pct']:+.2f}% "
        f"[{national['price_impact_pct_low']:+.2f}, {national['price_impact_pct_high']:+.2f}] "
        f"= {national['price_impact_usd_bu']:+.3f} $/bu on "
        f"{national['reference_price_usd_bu']:.2f}")

    impact = {
        "generated_at": generated_at,
        "metadata": meta,
        "national": national,
        "regions": regions,
        "assumptions": assumptions,
    }

    if regions_path:
        table = {
            "metadata": dict(meta, national=national, assumptions=assumptions),
            "columns": REGION_COLUMNS,
            "rows": [{c: region.get(c) for c in REGION_COLUMNS} for region in regions],
        }
        Path(regions_path).parent.mkdir(parents=True, exist_ok=True)
        with open(regions_path, "w") as fh:
            json.dump(table, fh, indent=2)
            fh.write("\n")
        log(f"wrote {regions_path}")

        # A CSV beside it, for off-platform use only. The platform collects only
        # the declared .output.json files and discards anything else.
        csv_path = Path(regions_path).with_suffix(".csv")
        with open(csv_path, "w", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=REGION_COLUMNS)
            writer.writeheader()
            writer.writerows({c: region.get(c) for c in REGION_COLUMNS} for region in regions)
        log(f"wrote {csv_path} (off-platform convenience only)")

    json.dump(impact, sys.stdout, indent=2)
    sys.stdout.write("\n")


if __name__ == "__main__":
    try:
        main(sys.argv)
    except RunError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
