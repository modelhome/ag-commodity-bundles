#!/usr/bin/env python3
"""
One-time build script for corn-price/production_weights.csv. Not part of the
model image.

Gives each of node 1's regions its share of US corn-for-grain production and
its irrigated share, so the per-region yield anomalies can be combined into one
national figure and so the rainfed bias in the western states is visible in the
output rather than implicit.

Source, public and needing no API key:

- USDA NASS, 2022 Census of Agriculture, state-level series from the Quick
  Stats bulk export at https://www.nass.usda.gov/datasets/qs.census2022.txt.gz
  (about 310 MB gzipped). This is the same file and the same vintage node 1
  used to place its region points, so the two nodes agree by construction. The
  Quick Stats *API* needs a key; the bulk export does not.

Four series are read, all at AGG_LEVEL_DESC = STATE and DOMAIN_DESC = TOTAL:

    CORN, GRAIN - PRODUCTION, MEASURED IN BU
    CORN, GRAIN - ACRES HARVESTED
    CORN, GRAIN, IRRIGATED - ACRES HARVESTED
    CORN, GRAIN - PRODUCTION, MEASURED IN BU   (NATIONAL, for the coverage share)

CLASS/PRODN/UTIL are pinned through SHORT_DESC because "CORN - PRODUCTION" also
covers silage, which is a different crop area.

A NASS value of (D) means the figure was withheld so an operation could not be
identified. It is treated as missing and recorded as such, never as zero -- a
withheld irrigated-acres figure would otherwise read as "this state does not
irrigate".

Usage:

    python build_weights.py            # writes production_weights.csv beside this file

Downloads are cached in .nass-cache/ (gitignored); delete it to force a refetch.
"""
import csv
import gzip
import json
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
CACHE = HERE / ".nass-cache"
OUT_PATH = HERE / "production_weights.csv"
META_PATH = HERE / "production_weights.meta.json"

NASS_URL = "https://www.nass.usda.gov/datasets/qs.census2022.txt.gz"
NASS_VINTAGE = "USDA NASS 2022 Census of Agriculture"
USER_AGENT = "modelhome-ag-commodity-bundles/corn-price (build_weights.py)"

# Node 1 owns the region set. These keys and their states come from
# agromet-bundles/crop-weather/regions.csv and are never redefined here; the
# runner joins the input's region_key against this table and fails loudly on a
# key with no row.
REGIONS = {
    "ia": "IA", "il": "IL", "mn": "MN", "ne": "NE", "in": "IN",
    "sd": "SD", "oh": "OH", "wi": "WI", "ks": "KS", "mo": "MO",
}

PRODUCTION = "CORN, GRAIN - PRODUCTION, MEASURED IN BU"
ACRES = "CORN, GRAIN - ACRES HARVESTED"
ACRES_IRRIGATED = "CORN, GRAIN, IRRIGATED - ACRES HARVESTED"
WANTED = {PRODUCTION, ACRES, ACRES_IRRIGATED}

# NASS withholds a value rather than publishing it when disclosure would
# identify an operation. These are the markers it uses in VALUE.
SUPPRESSED = {"(D)", "(Z)", "(NA)", "(X)", "(S)", ""}


def log(message):
    print(message, file=sys.stderr)


def download(url, path):
    """Fetch url to path unless it is already cached. Returns (path, last_modified)."""
    meta_path = path.with_suffix(path.suffix + ".headers.json")
    if path.exists():
        log(f"cached  {path.name}")
        if meta_path.exists():
            return path, json.loads(meta_path.read_text()).get("last_modified")
        return path, head_last_modified(url)
    path.parent.mkdir(parents=True, exist_ok=True)
    log(f"fetching {url}")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    partial = path.with_suffix(path.suffix + ".partial")
    with urllib.request.urlopen(request, timeout=600) as response, open(partial, "wb") as fh:
        last_modified = response.headers.get("Last-Modified")
        while chunk := response.read(1 << 20):
            fh.write(chunk)
    partial.rename(path)
    meta_path.write_text(json.dumps({"last_modified": last_modified}))
    log(f"saved   {path.name} ({path.stat().st_size / 1e6:.0f} MB)")
    return path, last_modified


def head_last_modified(url):
    """The server's Last-Modified for an already-cached file, for the meta record."""
    request = urllib.request.Request(url, method="HEAD", headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.headers.get("Last-Modified")
    except OSError as exc:  # offline rebuild of an already-cached file
        log(f"warning: could not read Last-Modified ({exc})")
        return None


def parse_value(raw):
    """A NASS VALUE as a float, or None when the figure was withheld."""
    raw = (raw or "").strip()
    if raw in SUPPRESSED:
        return None
    return float(raw.replace(",", ""))


def read_state_series(archive):
    """{STATE_ALPHA: {series: value}} plus the national production total."""
    by_state = {}
    national = {}
    withheld = []
    with gzip.open(archive, mode="rt", encoding="utf-8", errors="replace") as fh:
        for row in csv.DictReader(fh, delimiter="\t"):
            short_desc = row["SHORT_DESC"]
            if short_desc not in WANTED or row["DOMAIN_DESC"] != "TOTAL":
                continue
            level = row["AGG_LEVEL_DESC"]
            if level not in ("STATE", "NATIONAL"):
                continue
            value = parse_value(row["VALUE"])
            if level == "NATIONAL":
                national[short_desc] = value
                continue
            state = row["STATE_ALPHA"]
            if value is None:
                withheld.append((state, short_desc))
                continue
            by_state.setdefault(state, {})[short_desc] = value
    log(f"nass: {len(by_state)} states, {len(withheld)} withheld state-series values")
    return by_state, national, withheld


def main():
    archive, last_modified = download(NASS_URL, CACHE / "qs.census2022.txt.gz")
    by_state, national, withheld = read_state_series(archive)

    us_production = national.get(PRODUCTION)
    if not us_production:
        raise SystemExit(
            f"no NATIONAL row for {PRODUCTION!r}; the NASS export may have changed"
        )

    missing = [k for k, state in REGIONS.items() if state not in by_state]
    if missing:
        raise SystemExit(f"no state rows for region_key(s) {missing}; export may have changed")

    rows = []
    for key, state in REGIONS.items():
        series = by_state[state]
        production = series.get(PRODUCTION)
        acres = series.get(ACRES)
        if production is None or acres is None:
            raise SystemExit(f"{key}: missing production or acres; cannot weight this region")
        irrigated = series.get(ACRES_IRRIGATED)
        withheld_here = (state, ACRES_IRRIGATED) in withheld
        rows.append({
            "region_key": key,
            "state": state,
            "production_bu": f"{production:.0f}",
            "acres_harvested": f"{acres:.0f}",
            # Blank, not 0, when NASS withheld the figure: the runner must not
            # read a withheld value as "this state does not irrigate".
            "acres_irrigated": "" if irrigated is None else f"{irrigated:.0f}",
            "irrigated_share": "" if irrigated is None else f"{irrigated / acres:.4f}",
            "production_share_of_us": f"{production / us_production:.6f}",
            "method": (
                "state totals as published; irrigated share is "
                "CORN, GRAIN, IRRIGATED - ACRES HARVESTED over CORN, GRAIN - ACRES HARVESTED"
                + (" (irrigated acres withheld for disclosure)" if withheld_here else "")
            ),
            "source": f"{NASS_VINTAGE}, Quick Stats bulk export",
        })

    covered = sum(float(r["production_share_of_us"]) for r in rows)
    log(f"coverage: {covered * 100:.2f}% of US corn-for-grain production")

    with open(OUT_PATH, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    log(f"wrote   {OUT_PATH.name} ({len(rows)} regions)")

    META_PATH.write_text(json.dumps({
        "vintage": NASS_VINTAGE,
        "source": NASS_URL,
        "source_last_modified": last_modified,
        "series": sorted(WANTED),
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "us_production_bu": us_production,
        "coverage_share_of_us": round(covered, 6),
        "regions": sorted(REGIONS),
        "withheld": [f"{s}: {d}" for s, d in withheld if s in REGIONS.values()],
        "note": (
            "Region keys originate in agromet-bundles/crop-weather/regions.csv and are "
            "not redefined here. A NASS (D) value means the figure was withheld for "
            "disclosure and is recorded as missing, never as zero."
        ),
    }, indent=2) + "\n")
    log(f"wrote   {META_PATH.name}")


if __name__ == "__main__":
    main()
