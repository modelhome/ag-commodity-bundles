#!/usr/bin/env python3
"""
One-time build script for corn-price/price_history.csv. Not part of the model
image.

Assembles the national corn balance sheet the transmission is fitted on, and
the two current values the runner uses as defaults: the carry-in stocks-to-use
ratio and a reference price.

Source, public and needing no API key:

- USDA ERS Feed Grains Database, "Feed Grains Yearbook Tables -- All Years"
  machine-readable CSV, https://www.ers.usda.gov/media/5766/
  feed-grains-yearbook-tables-all-years.csv (about 18 MB). ERS discontinued the
  Feed Grains custom query and its CSV in May 2025 and moved to these yearbook
  tables in January 2026, so this is the current location; if it moves again the
  NASS bulk series is the fallback for yield and price, but it carries no
  balance sheet, so stocks-to-use would be lost with it.

Two ERS tables are read, both US annual, marketing year Sep-Aug:

  Table 1  area harvested, yield per harvested acre, production, price received
  Table 4  beginning stocks, ending stocks, total use, exports

Definitions, because "stocks-to-use" is ambiguous in the wild:

  stocks_to_use          ending stocks / total use, same marketing year. The
                         figure normally quoted. It is *endogenous* to the
                         season's own yield -- a short crop draws stocks down --
                         so it must not be used to condition that season's price
                         response.
  export_share_of_use    exports / total use, same marketing year. Exports are a
                         *component* of total use, not an addition to it, so a
                         lost-export scenario reduces the denominator too.
                         Endogenous within the marketing year in the same way
                         stocks_to_use is -- exports respond to the price they
                         sit beside -- so it is exposure context, never a
                         conditioner of the transmission.
  carryin_stocks_to_use  the PRIOR marketing year's stocks_to_use, which by the
                         balance-sheet identity is this year's beginning stocks
                         over last year's use. Predetermined at planting, known
                         before the crop is grown, and therefore the conditioner
                         the transmission uses.

The current marketing year is a WASDE projection, not an outcome. It is written
to the table with is_projection = true, excluded from the fit, and used only as
the source of the runner's defaults.

Usage:

    python build_price_history.py     # writes price_history.csv beside this file

Downloads are cached in .ers-cache/ (gitignored); delete it to force a refetch.
"""
import csv
import json
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
CACHE = HERE / ".ers-cache"
OUT_PATH = HERE / "price_history.csv"
META_PATH = HERE / "price_history.meta.json"

ERS_URL = (
    "https://www.ers.usda.gov/media/5766/feed-grains-yearbook-tables-all-years.csv"
)
ERS_VINTAGE = "USDA ERS Feed Grains Database, Feed Grains Yearbook Tables -- All Years"
USER_AGENT = "modelhome-ag-commodity-bundles/corn-price (build_price_history.py)"

TABLE_1 = "Table 1--Corn, sorghum, barley, and oats"
TABLE_4 = "Table 4--Corn: Supply and disappearance"


def log(message):
    print(message, file=sys.stderr)


def download(url, path):
    """Fetch url to path unless it is already cached. Returns (path, last_modified)."""
    headers_path = path.with_suffix(path.suffix + ".headers.json")
    if path.exists():
        log(f"cached  {path.name}")
        if headers_path.exists():
            return path, json.loads(headers_path.read_text()).get("last_modified")
        return path, None
    path.parent.mkdir(parents=True, exist_ok=True)
    log(f"fetching {url}")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    partial = path.with_suffix(path.suffix + ".partial")
    with urllib.request.urlopen(request, timeout=600) as response, open(partial, "wb") as fh:
        last_modified = response.headers.get("Last-Modified")
        while chunk := response.read(1 << 20):
            fh.write(chunk)
    partial.rename(path)
    headers_path.write_text(json.dumps({"last_modified": last_modified}))
    log(f"saved   {path.name} ({path.stat().st_size / 1e6:.1f} MB)")
    return path, last_modified


def read_series(rows, attribute, table_prefix):
    """{year: amount} for one US annual corn series from one ERS table."""
    out = {}
    for row in rows:
        if row["attribute"] != attribute:
            continue
        if not row["table_name"].startswith(table_prefix):
            continue
        amount = (row["amount"] or "").strip()
        if not amount:
            continue
        out[int(row["year"])] = float(amount)
    if not out:
        raise SystemExit(
            f"no rows for {attribute!r} in {table_prefix!r}; the ERS export may have changed"
        )
    return out


def fit_trend(years, values):
    """Ordinary least squares of value on year. Returns (intercept, slope, r2)."""
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
    return intercept, slope, (1 - ss_res / ss_tot if ss_tot else 0.0)


def main():
    path, last_modified = download(ERS_URL, CACHE / "feed-grains-all-years.csv")
    with open(path, newline="") as fh:
        rows = [
            r for r in csv.DictReader(fh)
            if r["commodity"] == "Corn"
            and r["geography"] == "United States"
            and r["frequency"] == "Annual"
        ]
    log(f"ers: {len(rows)} US annual corn rows")

    area = read_series(rows, "Area harvested for grain", TABLE_1)
    yield_ = read_series(rows, "Yield per harvested acre", TABLE_1)
    production = read_series(rows, "Production", TABLE_1)
    price = read_series(rows, "Price received by farmers", TABLE_1)
    beginning = read_series(rows, "Beginning stocks", TABLE_4)
    ending = read_series(rows, "Ending stocks", TABLE_4)
    total_use = read_series(rows, "Total use", TABLE_4)
    # Exports are a disappearance line in the same Table 4 as the stocks series.
    # Carried as exposure context for a downstream trade-policy model; nothing in
    # this bundle's arithmetic reads it.
    exports = read_series(rows, "Exports", TABLE_4)

    # The balance sheet is the binding constraint on the window.
    years = sorted(set(ending) & set(total_use) & set(yield_) & set(price))
    # Marketing year Y runs Sep Y to Aug Y+1, so Y is complete only once August
    # of Y+1 is past. Taking "this year minus one" would mark the in-progress year
    # final whenever the script is rebuilt between January and August, putting a
    # WASDE projection into the trend and the transmission fit.
    # Exports must cover the whole window. Unlike area or production, which are
    # written as blanks when a year is absent, a missing export value would make
    # a table that builds cleanly and only fails validation afterwards -- so it
    # fails here, naming the years, the way read_series fails on a missing
    # attribute. Never widen the window intersection above with exports: that
    # would silently drop balance-sheet years instead.
    missing_exports = [y for y in years if y not in exports]
    if missing_exports:
        raise SystemExit(
            f"no export value for {missing_exports} in {TABLE_4!r}; exports must "
            "cover every marketing year in the balance-sheet window"
        )

    now = datetime.now(timezone.utc)
    latest_complete = now.year - 1 if now.month >= 9 else now.year - 2
    log(f"balance sheet {years[0]}-{years[-1]}; latest complete marketing year {latest_complete}")

    # Detrend the national yield over the complete years only, so a projected
    # current year cannot tilt the trend the deviations are measured against.
    complete = [y for y in years if y <= latest_complete]
    intercept, slope, r2 = fit_trend(complete, [yield_[y] for y in complete])
    log(f"national yield trend: {slope:.3f} bu/acre/yr, r2 {r2:.3f}")

    out = []
    for year in years:
        trend = intercept + slope * year
        stocks_to_use = ending[year] / total_use[year]
        prior = year - 1
        carryin = (
            ending[prior] / total_use[prior]
            if prior in ending and prior in total_use else None
        )
        out.append({
            "year": year,
            "area_harvested_mil_acres": f"{area[year]:.3f}" if year in area else "",
            "yield_bu_acre": f"{yield_[year]:.4f}",
            "trend_yield_bu_acre": f"{trend:.4f}",
            "yield_deviation_pct": f"{(yield_[year] - trend) / trend * 100:.4f}",
            "production_mil_bu": f"{production[year]:.3f}" if year in production else "",
            "beginning_stocks_mil_bu": f"{beginning[year]:.3f}" if year in beginning else "",
            "ending_stocks_mil_bu": f"{ending[year]:.3f}",
            "total_use_mil_bu": f"{total_use[year]:.3f}",
            "exports_mil_bu": f"{exports[year]:.3f}",
            # Fraction of a denominator that already contains the numerator.
            "export_share_of_use": f"{exports[year] / total_use[year]:.6f}",
            "stocks_to_use": f"{stocks_to_use:.6f}",
            "carryin_stocks_to_use": "" if carryin is None else f"{carryin:.6f}",
            "price_usd_bu": f"{price[year]:.4f}",
            "is_projection": "true" if year > latest_complete else "false",
        })

    with open(OUT_PATH, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(out[0].keys()))
        writer.writeheader()
        writer.writerows(out)
    log(f"wrote   {OUT_PATH.name} ({len(out)} years)")

    current = out[-1]
    complete_row = next(r for r in reversed(out) if r["year"] == latest_complete)

    def exposure(row):
        """The export exposure of one marketing year, for a downstream consumer."""
        return {
            "marketing_year": row["year"],
            "exports_mil_bu": float(row["exports_mil_bu"]),
            "export_share_of_use": float(row["export_share_of_use"]),
            "is_projection": row["is_projection"] == "true",
        }

    META_PATH.write_text(json.dumps({
        "vintage": ERS_VINTAGE,
        "source": ERS_URL,
        "source_last_modified": last_modified,
        "tables": [TABLE_1, TABLE_4],
        "marketing_year": "Sep-Aug",
        "period": f"{years[0]}-{years[-1]}",
        "latest_complete_marketing_year": latest_complete,
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "national_yield_trend": {
            "intercept_bu_acre": round(intercept, 6),
            "slope_bu_acre_per_year": round(slope, 6),
            "r2": round(r2, 4),
            "fitted_over": f"{complete[0]}-{complete[-1]}",
        },
        "defaults": {
            "reference_price_usd_bu": float(current["price_usd_bu"]),
            "reference_price_year": current["year"],
            "reference_price_is_projection": current["is_projection"] == "true",
            "carryin_stocks_to_use": float(current["carryin_stocks_to_use"]),
            "carryin_stocks_to_use_year": current["year"],
        },
        # Both years, because a downstream consumer choosing a denominator should
        # not have to guess whether it has an outcome or a WASDE projection.
        "export_exposure": {
            "current": exposure(current),
            "latest_complete": exposure(complete_row),
        },
        "definitions": {
            "stocks_to_use": "ending stocks / total use, same marketing year; endogenous "
                             "to that season's yield and therefore not used to condition it",
            "exports": "a disappearance component of total use, in the same Table 4 as "
                       "the stocks series. Already inside total_use_mil_bu, so it must "
                       "never be added to it",
            "export_share_of_use": "exports / total use, same marketing year. Exposure "
                                   "context for a downstream trade-policy model, not an "
                                   "input to this bundle's arithmetic and not a "
                                   "conditioner of the transmission: exports are "
                                   "endogenous within the marketing year, responding to "
                                   "the price they are reported beside",
            "carryin_stocks_to_use": "the prior marketing year's stocks_to_use; "
                                     "predetermined at planting and the conditioner the "
                                     "transmission uses",
        },
        "note": (
            "The current marketing year is a WASDE projection, flagged is_projection and "
            "excluded from the trend fit and the transmission fit. It is the source of the "
            "runner's default reference price and carry-in ratio."
        ),
    }, indent=2) + "\n")
    log(f"wrote   {META_PATH.name}")


if __name__ == "__main__":
    main()
