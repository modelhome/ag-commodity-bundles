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

# Node 1 owns the region set; see build_weights.py.
REGIONS = {
    "ia": "IA", "il": "IL", "mn": "MN", "ne": "NE", "in": "IN",
    "sd": "SD", "oh": "OH", "wi": "WI", "ks": "KS", "mo": "MO",
}

SHORT_DESC = "CORN, GRAIN - YIELD, MEASURED IN BU / ACRE"
SUPPRESSED = {"(D)", "(Z)", "(NA)", "(X)", "(S)", ""}


def log(message):
    print(message, file=sys.stderr)


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


def read_state_yields(archive):
    """{STATE_ALPHA: {year: bu/acre}} for the final annual estimate."""
    wanted_states = set(REGIONS.values())
    by_state = {}
    with gzip.open(archive, mode="rt", encoding="utf-8", errors="replace") as fh:
        for row in csv.DictReader(without_nulls(fh), delimiter="\t"):
            if row["SHORT_DESC"] != SHORT_DESC:
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
            by_state.setdefault(state, {})[year] = float(raw.replace(",", ""))
    return by_state


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


def main():
    filename = current_crops_filename()
    log(f"current survey export: {filename}")
    archive, last_modified = download(LISTING_URL + filename, CACHE / filename)

    by_state = read_state_yields(archive)
    expected = PERIOD_END - PERIOD_START + 1

    rows = []
    trends = {}
    for key, state in REGIONS.items():
        series = by_state.get(state, {})
        if len(series) != expected:
            missing = sorted(set(range(PERIOD_START, PERIOD_END + 1)) - set(series))
            raise SystemExit(
                f"{key} ({state}): {len(series)} of {expected} years; missing {missing}. "
                f"The quantile map needs a complete window."
            )
        years = sorted(series)
        values = [series[y] for y in years]
        intercept, slope, r2, rmse = fit_trend(years, values)
        trends[key] = {
            "intercept_bu_acre": round(intercept, 6),
            "slope_bu_acre_per_year": round(slope, 6),
            "r2": round(r2, 4),
            "rmse_bu_acre": round(rmse, 3),
        }
        for year in years:
            trend = intercept + slope * year
            deviation = (series[year] - trend) / trend * 100
            rows.append({
                "region_key": key,
                "state": state,
                "year": year,
                "yield_bu_acre": f"{series[year]:.1f}",
                "trend_yield_bu_acre": f"{trend:.2f}",
                "deviation_pct": f"{deviation:.4f}",
            })
        spread = max(float(r["deviation_pct"]) for r in rows if r["region_key"] == key) - \
            min(float(r["deviation_pct"]) for r in rows if r["region_key"] == key)
        log(f"{key}: trend {intercept + slope * PERIOD_END:6.1f} bu/ac at {PERIOD_END}, "
            f"+{slope:.2f}/yr, r2 {r2:.2f}, deviation spread {spread:.1f} pts")

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
        "detrending": "per-state ordinary least squares of yield on calendar year; "
                      "deviation_pct is the residual as a percentage of the fitted line",
        "trends": trends,
        "note": (
            "The period must match node 2's baseline window (1995-2024), because a "
            "percentile rank from node 2 is read as the same quantile of this "
            "distribution. The runner refuses to run if the two disagree."
        ),
    }, indent=2) + "\n")
    log(f"wrote   {META_PATH.name}")


if __name__ == "__main__":
    main()
