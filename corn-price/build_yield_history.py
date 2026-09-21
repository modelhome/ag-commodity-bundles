#!/usr/bin/env python3
"""
One-time build script for corn-price/yield_history.csv. Not part of the model
image.

Gives each of node 1's regions its *observed* corn yield history over the same
1995-2024 window node 2 builds its simulated baseline on, detrended, so node 2's
percentile rank can be mapped onto a real-world anomaly.

Why this table exists. Node 2's yield anomaly is the anomaly of an
uncalibrated, rainfed, single-point WOFOST simulation. Its thirty-year baseline
distributions span 81 to 13,414 kg/ha for Iowa and 0 to 5,795 kg/ha for Kansas,
several times the spread a real state yield shows against trend. The percentage
is therefore not on a real-world scale and cannot be fed to a price
transmission. What survives the scale problem is the *rank*: this table supplies
the observed distribution that rank is mapped onto.

Source, public and needing no API key:

- USDA NASS survey series "CORN, GRAIN - YIELD, MEASURED IN BU / ACRE" at
  AGG_LEVEL_DESC = STATE, SOURCE_DESC = SURVEY, FREQ_DESC = ANNUAL and
  REFERENCE_PERIOD_DESC = YEAR (the final estimate, not one of the in-season
  AUG/SEP/OCT/NOV forecasts), from the Quick Stats bulk export at
  https://www.nass.usda.gov/datasets/qs.crops_<YYYYMMDD>.txt.gz (about 1.1 GB
  gzipped). The Quick Stats *API* needs a key; the bulk export does not.

The bulk export's filename carries a date that changes, so the current name is
discovered from the directory listing and recorded in the meta file rather than
hard-coded.

Detrending. Real corn yields rise with genetics and management; node 2's
baseline is fixed-weather by design and carries no trend, so the trend belongs
here. A per-state ordinary least-squares line on the year is fitted over the
window and each year's deviation is expressed as a percentage of that line. The
fitted line is also what the runner evaluates to get a trend yield for the
input's year, which is outside the fitting window, so the coefficients ship in
the meta file.

Usage:

    python build_yield_history.py     # writes yield_history.csv beside this file

Downloads are cached in .nass-cache/ (gitignored); delete it to force a refetch.
"""
import csv
import gzip
import json
import re
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
CACHE = HERE / ".nass-cache"
OUT_PATH = HERE / "yield_history.csv"
META_PATH = HERE / "yield_history.meta.json"

LISTING_URL = "https://www.nass.usda.gov/datasets/"
NASS_VINTAGE = "USDA NASS Quick Stats survey series"
USER_AGENT = "modelhome-ag-commodity-bundles/corn-price (build_yield_history.py)"

# The window is node 2's. Both nodes must use the same one: rank n of thirty has
# to mean the same thing on the simulated and the observed side, and the runner
# refuses to run if the two periods disagree.
PERIOD_START = 1995
PERIOD_END = 2024

# The region set is NOT redefined here. It is read from production_weights.csv,
# which build_weights.py writes from node 1's own set, so the two committed
# tables cannot drift apart: there is one region map in this bundle, not two.
# Build the weights first.
WEIGHTS_PATH = HERE / "production_weights.csv"

SHORT_DESC = "CORN, GRAIN - YIELD, MEASURED IN BU / ACRE"

# The per-stratum counterparts of SHORT_DESC, used only for the split states.
# Same source and selector, different PRODN_PRACTICE_DESC.
#
# Pinned through the full SHORT_DESC, as SHORT_DESC above is, because NASS also
# publishes each of these "MEASURED IN BU / NET PLANTED ACRE" -- a different
# denominator that would silently mix a planted-acre yield into a
# harvested-acre series.
#
# These series do NOT cover the window. For NE and KS both of them run
# 1995-2018 and then stop: NASS discontinued the estimate, so 2019-2024 are
# absent, including the 2022 western drought. They therefore cannot BE the
# stratum's history -- they are used to measure how much wider or narrower a
# stratum's year-to-year variation is than its state's, and that ratio is then
# applied to the state's full-window series. See rescale_to_stratum.
STRATUM_SHORT_DESC = {
    "irrigated": "CORN, GRAIN, IRRIGATED - YIELD, MEASURED IN BU / ACRE",
    "rainfed": "CORN, GRAIN, NON-IRRIGATED - YIELD, MEASURED IN BU / ACRE",
}

# A ratio measured on a handful of years would be noise. NE and KS both have 24.
MIN_RATIO_YEARS = 15

SUPPRESSED = {"(D)", "(Z)", "(NA)", "(X)", "(S)", ""}


def log(message):
    print(message, file=sys.stderr)


def load_regions():
    """({region_key: STATE}, {region_key: stratum}, {region_key: share}) from weights."""
    if not WEIGHTS_PATH.exists():
        raise SystemExit(
            f"{WEIGHTS_PATH.name} is missing. It defines the region set this table is "
            f"built for, so run build_weights.py first."
        )
    with open(WEIGHTS_PATH, newline="") as fh:
        rows = list(csv.DictReader(fh))
    if not rows:
        raise SystemExit(f"{WEIGHTS_PATH.name} has no rows")
    if "stratum" not in rows[0]:
        raise SystemExit(
            f"{WEIGHTS_PATH.name} has no stratum column. Rebuild it with the current "
            f"build_weights.py; the stratum is declared there, never inferred from the "
            f"spelling of a region key."
        )
    regions = {r["region_key"]: r["state"] for r in rows}
    strata = {r["region_key"]: r["stratum"] for r in rows}
    # Production shares, used to normalise a split state's stratum ratios so
    # they recombine to the state's own swing. See normalise_ratios.
    shares = {r["region_key"]: float(r["production_share_of_us"]) for r in rows}
    unknown = sorted(set(strata.values()) - {"all"} - set(STRATUM_SHORT_DESC))
    if unknown:
        raise SystemExit(
            f"{WEIGHTS_PATH.name} declares stratum value(s) {unknown} that this script "
            f"has no NASS series for; known: {sorted(STRATUM_SHORT_DESC)}"
        )
    split = sum(1 for v in strata.values() if v != "all")
    log(f"regions: {len(regions)} read from {WEIGHTS_PATH.name} ({split} strata)")
    return regions, strata, shares


def current_crops_filename():
    """The current qs.crops_<YYYYMMDD>.txt.gz name from the NASS directory listing."""
    request = urllib.request.Request(LISTING_URL, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=120) as response:
        html = response.read().decode("utf-8", "replace")
    dates = sorted(set(re.findall(r"qs\.crops_(\d{8})\.txt\.gz", html)))
    if not dates:
        raise SystemExit(f"no qs.crops_<date>.txt.gz found at {LISTING_URL}")
    return f"qs.crops_{dates[-1]}.txt.gz"


def download(url, path):
    """Fetch url to path unless it is already cached. Returns (path, last_modified)."""
    headers_path = path.with_suffix(path.suffix + ".headers.json")
    if path.exists():
        log(f"cached  {path.name}")
        if headers_path.exists():
            return path, json.loads(headers_path.read_text()).get("last_modified")
        return path, None
    path.parent.mkdir(parents=True, exist_ok=True)
    log(f"fetching {url} (this is a large file)")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    partial = path.with_suffix(path.suffix + ".partial")
    with urllib.request.urlopen(request, timeout=3600) as response, open(partial, "wb") as fh:
        last_modified = response.headers.get("Last-Modified")
        while chunk := response.read(1 << 20):
            fh.write(chunk)
    partial.rename(path)
    headers_path.write_text(json.dumps({"last_modified": last_modified}))
    log(f"saved   {path.name} ({path.stat().st_size / 1e6:.0f} MB)")
    return path, last_modified


def without_nulls(lines):
    """Strip NUL bytes, which the survey export pads trailing fields with.

    About three lines in four of qs.crops carry at least one, and Python's csv
    module raises `_csv.Error: line contains NUL` rather than skipping them. The
    census export used by build_weights.py has none, so this is specific to the
    survey file.
    """
    for line in lines:
        yield line.replace("\0", "") if "\0" in line else line


def read_state_yields(archive, regions):
    """{series: {STATE_ALPHA: {year: bu/acre}}} for the final annual estimates.

    One pass over a 1.1 GB export reads the state series and, for the split
    states, the two per-stratum series as well.
    """
    wanted_states = set(regions.values())
    wanted_series = {SHORT_DESC} | set(STRATUM_SHORT_DESC.values())
    by_series = {series: {} for series in wanted_series}
    with gzip.open(archive, mode="rt", encoding="utf-8", errors="replace") as fh:
        for row in csv.DictReader(without_nulls(fh), delimiter="\t"):
            series = row["SHORT_DESC"]
            if series not in wanted_series:
                continue
            if row["AGG_LEVEL_DESC"] != "STATE" or row["DOMAIN_DESC"] != "TOTAL":
                continue
            # SURVEY/ANNUAL/YEAR is the final estimate. The AUG/SEP/OCT/NOV
            # forecasts share the SHORT_DESC and would otherwise be mixed in.
            if row["SOURCE_DESC"] != "SURVEY" or row["FREQ_DESC"] != "ANNUAL":
                continue
            if row["REFERENCE_PERIOD_DESC"] != "YEAR":
                continue
            state = row["STATE_ALPHA"]
            if state not in wanted_states:
                continue
            year = int(row["YEAR"])
            if not PERIOD_START <= year <= PERIOD_END:
                continue
            raw = (row["VALUE"] or "").strip()
            if raw in SUPPRESSED:
                continue
            by_series[series].setdefault(state, {})[year] = float(raw.replace(",", ""))
    return by_series


def fit_trend(years, values):
    """Ordinary least squares of value on year. Returns (intercept, slope, r2, rmse)."""
    n = len(years)
    mean_x = sum(years) / n
    mean_y = sum(values) / n
    sxx = sum((x - mean_x) ** 2 for x in years)
    sxy = sum((x - mean_x) * (y - mean_y) for x, y in zip(years, values))
    slope = sxy / sxx
    intercept = mean_y - slope * mean_x
    fitted = [intercept + slope * x for x in years]
    ss_res = sum((y - f) ** 2 for y, f in zip(values, fitted))
    ss_tot = sum((y - mean_y) ** 2 for y in values)
    r2 = 1 - ss_res / ss_tot if ss_tot else 0.0
    rmse = (ss_res / n) ** 0.5
    return intercept, slope, r2, rmse


def quantile(sorted_values, fraction):
    """Linear-interpolated empirical quantile, matching the runner's own."""
    position = fraction * (len(sorted_values) - 1)
    low = int(position // 1)
    high = min(low + 1, len(sorted_values) - 1)
    weight = position - low
    return sorted_values[low] * (1 - weight) + sorted_values[high] * weight


def deviations_from(series):
    """{year: deviation_pct} from a fitted trend through the series it is given."""
    years = sorted(series)
    values = [series[y] for y in years]
    intercept, slope, _, _ = fit_trend(years, values)
    out = {}
    for year in years:
        trend = intercept + slope * year
        out[year] = (series[year] - trend) / trend * 100
    return out


def spread(deviations):
    """p10-to-p90 spread of a deviation series, in percentage points."""
    values = sorted(deviations.values())
    return quantile(values, 0.9) - quantile(values, 0.1)


def normalise_ratios(ratios, shares, state):
    """Make a state's stratum ratios preserve the state's own aggregate swing.

    The raw ratios are each measured against the state marginally: the
    irrigated series' spread over the state's, and the rainfed series' over the
    state's. Used as they are measured, they do NOT recombine to the state.
    Production-weighting them gives 1.086 for Nebraska and 1.394 for Kansas, not
    1.0, so splitting a state and adding its halves back up would hand it 8.6%
    and 39.4% more influence over the national shock than treating it as one
    region did.

    That is an artifact, not a modelling choice. Marginal spreads add linearly
    only when the two series move together, and they do not: over the published
    years the irrigated and non-irrigated deviations correlate 0.31 in Nebraska
    and 0.80 in Kansas. The real state series already embeds that imperfect
    correlation and is therefore narrower than the weighted sum of its parts.
    Assuming one shared shape throws the diversification away and overshoots.

    The state series is the quantity this model has thirty trustworthy years of,
    so it is the one to preserve: the ratios are divided by their own
    production-weighted mean. The strata stay differentiated in exactly the
    measured proportion -- the irrigated-to-rainfed ratio is untouched -- and
    the state's contribution to the national figure is what its own observed
    distribution says it should be.

    The cost, stated because it is real: each stratum's spread no longer equals
    its own measured marginal spread. Nebraska's irrigated stratum becomes
    0.456 of the state rather than the measured 0.495. Per-stratum figures are
    intermediate here and the national figure is the output, so preserving the
    aggregate is the right trade -- but a consumer reading a single stratum row
    should know its width is set by that choice.
    """
    total = sum(shares.values())
    mean = sum(shares[k] / total * ratios[k] for k in ratios)
    if mean <= 0:
        raise SystemExit(f"{state}: stratum ratios average to {mean}; cannot normalise")
    return {k: v / mean for k, v in ratios.items()}, mean


def rescale_to_stratum(state_series, stratum_series, key, stratum):
    """How much wider a stratum's yield varies than its state's, and its own trend.

    NASS publishes no per-stratum yield for 2019-2024, so this cannot simply be
    the stratum's history: the window is node 2's and does not move. What the
    overlapping years do support is a measurement of SCALE. An irrigated crop's
    yield varies less from year to year than its state's blended average and a
    rainfed one varies more, and that ratio is what the quantile map needs,
    because the map reads a rank as a position in a distribution and it is the
    distribution's width that sets the anomaly it returns.

    So: fit each stratum's own trend over the years NASS does publish, measure
    the p10-p90 spread of both the stratum and the state over exactly those
    years, and return their ratio. The caller applies it to the state's
    full-window deviation series.

    What this assumes, and it is the weakest link in the bundle: that the ratio
    measured over the published years holds over the whole window, and that a
    stratum's year-to-year SHAPE is its state's. The second is the stronger
    claim -- in a year when irrigation is the whole story the two strata do not
    move together at all -- and it is stated in the output, the README and the
    Modelfile rather than only here.

    Returns (ratio, detail-dict for the meta file).
    """
    overlap = sorted(set(state_series) & set(stratum_series))
    if len(overlap) < MIN_RATIO_YEARS:
        raise SystemExit(
            f"{key}: the {stratum} series overlaps the state series in only "
            f"{len(overlap)} year(s) inside {PERIOD_START}-{PERIOD_END}, fewer than the "
            f"{MIN_RATIO_YEARS} this build requires. A dispersion ratio measured on that "
            f"few years would be noise; do not rescale on it."
        )
    stratum_over = {y: stratum_series[y] for y in overlap}
    state_over = {y: state_series[y] for y in overlap}
    stratum_dev = deviations_from(stratum_over)
    state_dev = deviations_from(state_over)
    stratum_spread = spread(stratum_dev)
    state_spread = spread(state_dev)
    if state_spread <= 0 or stratum_spread <= 0:
        raise SystemExit(f"{key}: degenerate spread; cannot form a dispersion ratio")
    ratio = stratum_spread / state_spread

    # The stratum's own trend LEVEL, fitted on its own published years. This is
    # what the production weight uses (acres x trend yield), so it must be the
    # stratum's yield level and not the state's -- irrigated corn out-yields the
    # state average in both of these states by a wide margin.
    years = sorted(stratum_series)
    intercept, slope, r2, rmse = fit_trend(years, [stratum_series[y] for y in years])
    detail = {
        "dispersion_ratio": round(ratio, 4),
        "ratio_period": f"{overlap[0]}-{overlap[-1]}",
        "ratio_years": len(overlap),
        "stratum_spread_p10_p90_pct": round(stratum_spread, 2),
        "state_spread_p10_p90_pct": round(state_spread, 2),
        "series": STRATUM_SHORT_DESC[stratum],
        "trend_fitted_over": f"{years[0]}-{years[-1]}",
        "trend_r2": round(r2, 4),
        "trend_rmse_bu_acre": round(rmse, 3),
    }
    return [ratio, intercept, slope, detail]


def main():
    regions, strata, shares = load_regions()
    filename = current_crops_filename()
    log(f"current survey export: {filename}")
    archive, last_modified = download(LISTING_URL + filename, CACHE / filename)

    by_series = read_state_yields(archive, regions)
    by_state = by_series[SHORT_DESC]
    expected = PERIOD_END - PERIOD_START + 1

    # Pass 1: measure each stratum against its state, then normalise each split
    # state's ratios so they recombine to that state's own swing (see
    # normalise_ratios). This has to happen before any row is written, because
    # a ratio depends on the other stratum of the same state.
    measured = {}
    for key, state in regions.items():
        stratum = strata[key]
        if stratum == "all":
            continue
        stratum_series = by_series[STRATUM_SHORT_DESC[stratum]].get(state, {})
        if not stratum_series:
            raise SystemExit(
                f"{key}: no {STRATUM_SHORT_DESC[stratum]!r} rows for {state}. "
                f"production_weights.csv declares this region a {stratum} stratum, "
                f"so the series that sizes its variability must exist."
            )
        measured[key] = rescale_to_stratum(
            by_state.get(state, {}), stratum_series, key, stratum
        )

    by_split_state = {}
    for key in measured:
        by_split_state.setdefault(regions[key], []).append(key)
    ratios = {}
    for state, keys in by_split_state.items():
        if len(keys) < 2:
            raise SystemExit(
                f"{state}: only {keys} present. A split state's ratios are normalised "
                f"against each other, so both strata must be in the region set."
            )
        raw = {k: measured[k][0] for k in keys}
        normalised, mean = normalise_ratios(raw, {k: shares[k] for k in keys}, state)
        ratios.update(normalised)
        log(f"{state}: raw ratios " + ", ".join(f"{k} x{raw[k]:.3f}" for k in keys)
            + f" average to {mean:.3f}; normalised to "
            + ", ".join(f"x{normalised[k]:.3f}" for k in keys))
        for k in keys:
            measured[k][3].update({
                "dispersion_ratio_measured": round(raw[k], 4),
                "dispersion_ratio": round(normalised[k], 4),
                "state_normalisation_divisor": round(mean, 4),
            })

    rows = []
    trends = {}
    rescalings = {}
    for key, state in regions.items():
        series = by_state.get(state, {})
        if len(series) != expected:
            missing = sorted(set(range(PERIOD_START, PERIOD_END + 1)) - set(series))
            raise SystemExit(
                f"{key} ({state}): {len(series)} of {expected} years; missing {missing}. "
                f"The quantile map needs a complete window."
            )
        years = sorted(series)
        values = [series[y] for y in years]
        # The state's own full-window fit. For an unsplit region this is the
        # region's fit; for a stratum it supplies only the year-to-year shape,
        # and the stratum's own trend level replaces intercept/slope below.
        state_intercept, state_slope, r2, rmse = fit_trend(years, values)
        intercept, slope = state_intercept, state_slope
        stratum = strata[key]

        if stratum == "all":
            ratio = 1.0
        else:
            _, intercept, slope, detail = measured[key]
            ratio = ratios[key]
            rescalings[key] = detail
            # The state trend statistics describe the state's fit, not this
            # stratum's; the stratum's own are in `detail`.
            r2, rmse = detail["trend_r2"], detail["trend_rmse_bu_acre"]

        trends[key] = {
            "intercept_bu_acre": round(intercept, 6),
            "slope_bu_acre_per_year": round(slope, 6),
            "r2": round(r2, 4),
            "rmse_bu_acre": round(rmse, 3),
        }
        for year in years:
            trend = intercept + slope * year
            if stratum == "all":
                observed = series[year]
                deviation = (observed - trend) / trend * 100
            else:
                # The state's shape, scaled to this stratum's width, placed on
                # this stratum's own trend level. yield_bu_acre is therefore a
                # DERIVED figure for a stratum row, not a published NASS value.
                state_trend = state_intercept + state_slope * year
                deviation = (series[year] - state_trend) / state_trend * 100 * ratio
                observed = trend * (1 + deviation / 100)
            rows.append({
                "region_key": key,
                "state": state,
                "year": year,
                "yield_bu_acre": f"{observed:.1f}",
                "trend_yield_bu_acre": f"{trend:.2f}",
                "deviation_pct": f"{deviation:.4f}",
            })
        observed_spread = max(
            float(r["deviation_pct"]) for r in rows if r["region_key"] == key
        ) - min(float(r["deviation_pct"]) for r in rows if r["region_key"] == key)
        note = "" if stratum == "all" else f", {stratum} rescaled x{ratio:.3f}"
        log(f"{key}: trend {intercept + slope * PERIOD_END:6.1f} bu/ac at {PERIOD_END}, "
            f"{slope:+.2f}/yr, r2 {r2:.2f}, deviation spread {observed_spread:.1f} pts{note}")

    with open(OUT_PATH, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    log(f"wrote   {OUT_PATH.name} ({len(rows)} rows)")

    META_PATH.write_text(json.dumps({
        "vintage": NASS_VINTAGE,
        "source": LISTING_URL + filename,
        "source_file": filename,
        "source_last_modified": last_modified,
        "series": SHORT_DESC,
        "selector": "AGG_LEVEL_DESC=STATE, SOURCE_DESC=SURVEY, FREQ_DESC=ANNUAL, "
                    "REFERENCE_PERIOD_DESC=YEAR (final estimate, not an in-season forecast)",
        "period": f"{PERIOD_START}-{PERIOD_END}",
        "period_start": PERIOD_START,
        "period_end": PERIOD_END,
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "detrending": "per-region ordinary least squares of yield on calendar year; "
                      "deviation_pct is the residual as a percentage of the fitted line",
        "trends": trends,
        "strata": {k: v for k, v in strata.items() if v != "all"},
        "stratum_rescaling": rescalings,
        "stratum_rescaling_method": (
            "NASS publishes a state-level annual IRRIGATED and NON-IRRIGATED corn "
            "yield for the split states, but both series end in 2018, so 2019-2024 "
            "are absent and they cannot be a stratum's history over this window. They "
            "are used instead to MEASURE SCALE: over the overlapping years, each "
            "stratum's detrended p10-p90 spread is divided by its state's over exactly "
            "the same years, and the resulting dispersion_ratio multiplies the state's "
            "full-window deviation series, AFTER being normalised so a split state's two "
            "ratios recombine to that state's own swing: the raw marginal ratios average "
            "to 1.086 (NE) and 1.394 (KS) rather than 1, because the two strata do not "
            "move together (they correlate 0.31 and 0.80 over the published years) and a "
            "shared-shape reconstruction therefore overshoots. Dividing by that mean "
            "keeps the measured irrigated-to-rainfed proportion exactly and stops the "
            "split inflating a state's weight in the national figure; the cost is that a "
            "stratum's spread is no longer its own measured marginal spread, which is "
            "recorded per region as dispersion_ratio_measured. Each stratum's trend LEVEL is fitted on its "
            "own published years and extrapolated over the rest of the window, so the "
            "production weight uses irrigated corn's yield level rather than the "
            "state's. A stratum row's yield_bu_acre is therefore DERIVED "
            "(trend x (1 + deviation)), not a published NASS figure; trend_yield_bu_acre "
            "and deviation_pct are what the runner reads. Unsplit regions have "
            "dispersion_ratio 1 and are untouched. ASSUMES the measured ratio holds "
            "over 2019-2024, and that a stratum's year-to-year shape is its state's -- "
            "the stronger of the two, because in a year when irrigation is the whole "
            "story the two strata do not move together at all."
        ),
        "regions_source": "production_weights.csv",
        "note": (
            "The period must match node 2's baseline window (1995-2024), because a "
            "percentile rank from node 2 is read as the same quantile of this "
            "distribution. The runner refuses to run if the two disagree. The region "
            "set and each region's stratum are read from production_weights.csv rather "
            "than redefined here, so the two committed tables cannot drift apart and "
            "no stratum is inferred from the spelling of a region key."
        ),
    }, indent=2) + "\n")
    log(f"wrote   {META_PATH.name}")


if __name__ == "__main__":
    main()
