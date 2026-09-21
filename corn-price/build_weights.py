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

Six series are read, all at AGG_LEVEL_DESC = STATE and DOMAIN_DESC = TOTAL:

    CORN, GRAIN - PRODUCTION, MEASURED IN BU
    CORN, GRAIN - ACRES HARVESTED
    CORN, GRAIN, IRRIGATED - ACRES HARVESTED
    CORN, GRAIN, IRRIGATED, ENTIRE CROP - YIELD, MEASURED IN BU / ACRE
    CORN, GRAIN, IRRIGATED, NONE OF CROP - YIELD, MEASURED IN BU / ACRE
    CORN, GRAIN - PRODUCTION, MEASURED IN BU   (NATIONAL, for the coverage share)

The last two exist only to apportion a split state's production between its
irrigated and rainfed strata, because NASS publishes no irrigated production at
any aggregation level. See apportion_state.

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
import argparse
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
#
# This is a transcription, so `--regions <path>` takes node 1's regions.csv
# directly and the script checks the two agree, naming any difference. Pass it
# whenever node 1's checkout is to hand -- it turns a duplicated map into a
# verified one. The transcription stays as the default because these bundles are
# built and run one repo at a time and a build script must not require a sibling
# checkout to exist.
REGIONS = {
    "ia": "IA", "il": "IL", "mn": "MN",
    "ne_irrigated": "NE", "ne_rainfed": "NE",
    "in": "IN", "sd": "SD", "oh": "OH", "wi": "WI",
    "ks_irrigated": "KS", "ks_rainfed": "KS",
    "mo": "MO",
}

# Which regions are one stratum of a split state, and which stratum they are.
# Node 1 splits a state when irrigation covers 20 percent or more of its
# harvested corn acres (NE 52.7, KS 25.4); every other state keeps one row.
#
# Nothing here infers a stratum from the spelling of a region key -- this table
# says so, exactly as node 2's water_regime.csv does for its water regime. A key
# named "ne_irrigated" with no entry here would be treated as an unsplit region
# and the build would fail on the acre reconciliation rather than guess.
STRATA = {
    "ne_irrigated": "irrigated", "ne_rainfed": "rainfed",
    "ks_irrigated": "irrigated", "ks_rainfed": "rainfed",
}

PRODUCTION = "CORN, GRAIN - PRODUCTION, MEASURED IN BU"
ACRES = "CORN, GRAIN - ACRES HARVESTED"
ACRES_IRRIGATED = "CORN, GRAIN, IRRIGATED - ACRES HARVESTED"
# NASS publishes no irrigated PRODUCTION at any aggregation level, so a stratum's
# production cannot be read off. These two operation-class yields are what node 1
# used to apportion county production between its strata, and this script uses
# them the same way at state level, so the two nodes split a state by the same
# rule. They compare operations that irrigate their ENTIRE corn crop with those
# that irrigate NONE of it; an operation irrigating PART of its crop is in
# neither class, which is why these are a ratio for apportionment and never a
# yield level in their own right.
YIELD_ENTIRE = "CORN, GRAIN, IRRIGATED, ENTIRE CROP - YIELD, MEASURED IN BU / ACRE"
YIELD_NONE = "CORN, GRAIN, IRRIGATED, NONE OF CROP - YIELD, MEASURED IN BU / ACRE"
WANTED = {PRODUCTION, ACRES, ACRES_IRRIGATED, YIELD_ENTIRE, YIELD_NONE}

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


def regions_from_upstream(path):
    """{region_key: STATE} read from node 1's regions.csv, checked against REGIONS."""
    with open(path, newline="") as fh:
        upstream = {r["region_key"]: r["state"] for r in csv.DictReader(fh)}
    if upstream != REGIONS:
        only_upstream = sorted(set(upstream) - set(REGIONS))
        only_local = sorted(set(REGIONS) - set(upstream))
        changed = sorted(k for k in set(upstream) & set(REGIONS)
                         if upstream[k] != REGIONS[k])
        raise SystemExit(
            f"{path} disagrees with this script's region map. "
            f"only upstream: {only_upstream}; only here: {only_local}; "
            f"different state: {changed}. Update REGIONS deliberately -- the whole "
            f"flow shares one region set and node 1 owns it."
        )
    log(f"regions: verified against {path}")
    return upstream


def apportion_state(state, series):
    """Split one state's published acres and production between its two strata.

    Acres are published: the irrigated stratum takes
    CORN, GRAIN, IRRIGATED - ACRES HARVESTED and the rainfed stratum takes the
    remainder, so the two always sum to the published state total exactly.

    Production is not published per stratum anywhere, so it is apportioned in
    proportion to acres times that stratum's operation-class yield, then
    normalised back onto the published state production. Normalising is what
    keeps the coverage share invariant: splitting a state must move production
    between two rows, never create or destroy any, so the ten-state coverage
    figure is the same 82.47% before and after.

    Returns {stratum: {"acres": a, "production": p, "yield": y}}.
    """
    acres = series.get(ACRES)
    production = series.get(PRODUCTION)
    acres_irrigated = series.get(ACRES_IRRIGATED)
    yield_entire = series.get(YIELD_ENTIRE)
    yield_none = series.get(YIELD_NONE)
    for name, value in (("acres", acres), ("production", production),
                        ("irrigated acres", acres_irrigated),
                        ("entire-crop yield", yield_entire),
                        ("none-of-crop yield", yield_none)):
        if value is None:
            raise SystemExit(
                f"{state}: {name} is missing or withheld, so this state cannot be "
                f"split into strata. Node 1 splits it, so node 3 must too; fix the "
                f"series rather than falling back to one unsplit row."
            )
    if acres_irrigated > acres:
        raise SystemExit(
            f"{state}: irrigated acres ({acres_irrigated:.0f}) exceed harvested acres "
            f"({acres:.0f}); the NASS export may have changed"
        )
    acres_rainfed = acres - acres_irrigated
    raw_irrigated = acres_irrigated * yield_entire
    raw_rainfed = acres_rainfed * yield_none
    total_raw = raw_irrigated + raw_rainfed
    if total_raw <= 0:
        raise SystemExit(f"{state}: cannot apportion production between strata")
    return {
        "irrigated": {
            "acres": acres_irrigated,
            "production": production * raw_irrigated / total_raw,
            "yield": yield_entire,
        },
        "rainfed": {
            "acres": acres_rainfed,
            "production": production * raw_rainfed / total_raw,
            "yield": yield_none,
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--regions",
        help="path to agromet-bundles/crop-weather/regions.csv; when given, the "
             "region map is checked against node 1's own table and the build fails "
             "on any difference",
    )
    args = parser.parse_args()
    if args.regions:
        regions_from_upstream(args.regions)

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

    # Apportion each split state once, so both of its strata are derived from
    # one reconciliation rather than two independent ones.
    split = {}
    for key, state in REGIONS.items():
        if key in STRATA and state not in split:
            split[state] = apportion_state(state, by_state[state])

    rows = []
    for key, state in REGIONS.items():
        series = by_state[state]
        stratum = STRATA.get(key)
        withheld_here = (state, ACRES_IRRIGATED) in withheld

        if stratum is None:
            production = series.get(PRODUCTION)
            acres = series.get(ACRES)
            if production is None or acres is None:
                raise SystemExit(
                    f"{key}: missing production or acres; cannot weight this region"
                )
            irrigated = series.get(ACRES_IRRIGATED)
            # Blank, not 0, when NASS withheld the figure: the runner must not
            # read a withheld value as "this state does not irrigate".
            acres_irrigated = "" if irrigated is None else f"{irrigated:.0f}"
            irrigated_share = "" if irrigated is None else f"{irrigated / acres:.4f}"
            method = (
                "state totals as published, not split into strata: irrigation covers "
                "less than node 1's 20 percent threshold here. Irrigated share is "
                "CORN, GRAIN, IRRIGATED - ACRES HARVESTED over CORN, GRAIN - ACRES HARVESTED"
                + (" (irrigated acres withheld for disclosure)" if withheld_here else "")
            )
        else:
            part = split[state][stratum]
            acres = part["acres"]
            production = part["production"]
            # A stratum is defined by its irrigation, so its irrigated share is
            # 1 or 0 by construction rather than measured.
            acres_irrigated = f"{acres:.0f}" if stratum == "irrigated" else "0"
            irrigated_share = "1.0000" if stratum == "irrigated" else "0.0000"
            method = (
                f"{stratum} stratum of a state node 1 splits: acres are "
                + ("CORN, GRAIN, IRRIGATED - ACRES HARVESTED as published"
                   if stratum == "irrigated"
                   else "CORN, GRAIN - ACRES HARVESTED minus the published irrigated acres")
                + ". NASS publishes no irrigated production at any level, so the state's "
                  "published production is apportioned between the two strata in "
                  "proportion to acres times that stratum's operation-class yield "
                  f"({YIELD_ENTIRE if stratum == 'irrigated' else YIELD_NONE}, "
                  f"{part['yield']:.1f} bu/acre), then normalised back onto the published "
                  "state total so the two strata sum to it exactly and the coverage share "
                  "is unchanged. This mixes an operation-class yield with an area split, "
                  "the same apportionment node 1 uses for its stratum weights. Operations "
                  "irrigating their entire crop out-yield those irrigating none in these "
                  "two states, but the sign of that gap flips in Iowa and Ohio, so a "
                  "stratum is not 'the better half'"
            )

        rows.append({
            "region_key": key,
            "state": state,
            # "all" for a state node 1 does not split. Declared, never inferred
            # from the spelling of the key -- build_yield_history.py and the
            # runner both read the stratum from this column.
            "stratum": stratum or "all",
            "production_bu": f"{production:.0f}",
            "acres_harvested": f"{acres:.0f}",
            "acres_irrigated": acres_irrigated,
            "irrigated_share": irrigated_share,
            "production_share_of_us": f"{production / us_production:.6f}",
            "method": method,
            "source": f"{NASS_VINTAGE}, Quick Stats bulk export",
        })

    # The split must move production between rows, never create or destroy it.
    for state, parts in split.items():
        published_acres = by_state[state][ACRES]
        published_production = by_state[state][PRODUCTION]
        acres_sum = sum(p["acres"] for p in parts.values())
        production_sum = sum(p["production"] for p in parts.values())
        if abs(acres_sum - published_acres) > 0.5:
            raise SystemExit(
                f"{state}: stratum acres sum to {acres_sum:.0f} but NASS publishes "
                f"{published_acres:.0f}"
            )
        if abs(production_sum - published_production) > 1.0:
            raise SystemExit(
                f"{state}: stratum production sums to {production_sum:.0f} but NASS "
                f"publishes {published_production:.0f}"
            )
        log(f"{state}: split reconciles to published acres and production")

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
        "strata": STRATA,
        "stratum_apportionment": (
            "A split state's irrigated stratum takes its published "
            "CORN, GRAIN, IRRIGATED - ACRES HARVESTED and the rainfed stratum the "
            "remainder. NASS publishes no irrigated production at any aggregation level, "
            "so the published state production is apportioned between the two in "
            "proportion to acres times the operation-class yields "
            f"({YIELD_ENTIRE} and {YIELD_NONE}), then normalised back onto the published "
            "state total. Acres and production therefore sum to the published state "
            "figures exactly and the coverage share is unchanged by the split."
        ),
        "withheld": [f"{s}: {d}" for s, d in withheld if s in REGIONS.values()],
        "regions_verified_against": args.regions or None,
        "note": (
            "Region keys originate in agromet-bundles/crop-weather/regions.csv and are "
            "not redefined here; pass --regions <that file> to have the build check the "
            "two agree. A NASS (D) value means the figure was withheld for disclosure "
            "and is recorded as missing, never as zero. build_yield_history.py reads its "
            "region set from this table, so the two never drift apart. Which regions are "
            "strata is declared in this script's STRATA map and in region_strata.csv, "
            "never inferred from the spelling of a region key."
        ),
    }, indent=2) + "\n")
    log(f"wrote   {META_PATH.name}")


if __name__ == "__main__":
    main()
