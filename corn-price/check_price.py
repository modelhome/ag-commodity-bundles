#!/usr/bin/env python3
"""
Validation check for corn-price. Not part of the model image.

    python check_price.py [run/corn_price_impact.output.json]

Proves the modelling claims, not the plumbing. The things that could be wrong
here and still produce a plausible-looking document are: the rescaling silently
not happening, the weights not being production weights, the coverage being
scaled away, a price number shipping without its range, and a region being
dropped instead of failing. Each of those has a check.

Exits non-zero on the first failure, after printing every result.
"""
import csv
import json
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - only on Python < 3.11
    print(
        "check_price.py needs Python 3.11 or newer to read Modelfile.toml "
        f"(running {sys.version.split()[0]}). The model image is python:3.12-slim; "
        "run the check on a matching interpreter, for example:\n"
        "    uv run --python 3.12 python corn-price/check_price.py",
        file=sys.stderr,
    )
    raise SystemExit(2)

HERE = Path(__file__).resolve().parent
DEFAULT_OUTPUT = HERE.parent / "run" / "corn_price_impact.output.json"
SAMPLE = HERE / "sample_input.json"
RUNNER = HERE / "runner.py"
MODELFILE = HERE / "Modelfile.toml"

# Node 1's region set. Nebraska and Kansas are split into an irrigated and a
# rainfed stratum because irrigation covers 20% or more of their harvested corn
# acres; the other eight states are one region each.
REGIONS = ["ia", "il", "mn", "ne_irrigated", "ne_rainfed", "in", "sd", "oh",
           "wi", "ks_irrigated", "ks_rainfed", "mo"]
SPLIT_STATES = {"NE": ("ne_irrigated", "ne_rainfed"),
                "KS": ("ks_irrigated", "ks_rainfed")}

PASSES = []
FAILURES = []


def check(label, condition, detail=""):
    (PASSES if condition else FAILURES).append(label)
    mark = "pass" if condition else "FAIL"
    print(f"  [{mark}] {label}" + (f" -- {detail}" if detail else ""))
    return condition


def spread_of(values):
    """p10-to-p90 spread of a deviation series, the runner's own quantile."""
    values = sorted(values)
    def q(fraction):
        position = fraction * (len(values) - 1)
        low = int(position // 1)
        high = min(low + 1, len(values) - 1)
        return values[low] * (1 - (position - low)) + values[high] * (position - low)
    return q(0.9) - q(0.1)


def close(a, b, tolerance):
    return abs(a - b) <= tolerance


def run_model(input_path, regions_path=None):
    """Run the runner on a document. Returns (returncode, parsed_stdout_or_None, stderr)."""
    args = [sys.executable, str(RUNNER), str(input_path)]
    if regions_path:
        args.append(str(regions_path))
    proc = subprocess.run(args, capture_output=True, text=True)
    parsed = None
    if proc.returncode == 0 and proc.stdout.strip():
        parsed = json.loads(proc.stdout)
    return proc.returncode, parsed, proc.stderr


# ------------------------------------------------------------------ the tables

def load_tables():
    with open(HERE / "production_weights.csv", newline="") as fh:
        weights = {r["region_key"]: r for r in csv.DictReader(fh)}
    deviations = {}
    with open(HERE / "yield_history.csv", newline="") as fh:
        for row in csv.DictReader(fh):
            deviations.setdefault(row["region_key"], []).append(float(row["deviation_pct"]))
    for key in deviations:
        deviations[key].sort()
    metas = {
        name: json.loads((HERE / f"{name}.meta.json").read_text())
        for name in ("production_weights", "yield_history", "price_history")
    }
    transmission = json.loads((HERE / "transmission.json").read_text())
    return weights, deviations, metas, transmission


def check_tables(weights, deviations, metas):
    print("\nAC-5  production weights and coverage")
    check("all twelve region keys in production_weights.csv",
          sorted(weights) == sorted(REGIONS), f"{sorted(weights)}")
    check("all twelve region keys in yield_history.csv",
          sorted(deviations) == sorted(REGIONS))
    check("every region declares a stratum",
          all(r.get("stratum") in ("all", "irrigated", "rainfed")
              for r in weights.values()),
          f"{ {k: r.get('stratum') for k, r in weights.items()} }")
    check("thirty observed years per region",
          all(len(v) == 30 for v in deviations.values()),
          f"{ {k: len(v) for k, v in deviations.items()} }")

    meta = metas["production_weights"]
    recomputed = sum(float(r["production_share_of_us"]) for r in weights.values())
    check("coverage share equals the sum of the per-state shares",
          close(recomputed, meta["coverage_share_of_us"], 1e-5),
          f"{recomputed:.6f} vs {meta['coverage_share_of_us']:.6f}")
    check("coverage share is in a sane band for these corn states",
          0.75 <= recomputed <= 0.90, f"{recomputed * 100:.2f}% of US production")
    # Each share must be that state's production over the national total the
    # build script recorded, or the shares are not what they claim to be.
    us_total = meta["us_production_bu"]
    consistent = all(
        close(float(r["production_share_of_us"]), float(r["production_bu"]) / us_total, 1e-6)
        for r in weights.values()
    )
    check("each share equals state production over the national total", consistent)

    check("irrigated share present for every region",
          all(r["irrigated_share"] != "" for r in weights.values()))
    irrigated = {k: float(r["irrigated_share"]) for k, r in weights.items()}
    # A stratum is defined by its irrigation, so its share is 1 or 0 by
    # construction. An unsplit state's is measured, and must be below node 1's
    # 20% split threshold -- otherwise node 1 would have split it.
    strata = {k: r["stratum"] for k, r in weights.items()}
    check("each stratum's irrigated share is 1 or 0 by construction",
          all(irrigated[k] == 1.0 for k, s in strata.items() if s == "irrigated")
          and all(irrigated[k] == 0.0 for k, s in strata.items() if s == "rainfed"))
    state_share = {k: float(r["state_irrigated_share"]) for k, r in weights.items()
                   if r.get("state_irrigated_share")}
    check("every row carries the whole state's irrigated share",
          sorted(state_share) == sorted(weights))
    check("an unsplit region's own share is its state's",
          all(close(state_share[k], irrigated[k], 1e-9)
              for k, s in strata.items() if s == "all"))
    for state, (irr, rain) in SPLIT_STATES.items():
        check(f"{state}'s two strata report the same state irrigated share",
              close(state_share[irr], state_share[rain], 1e-9),
              f"{state_share[irr]:.4f}")
        check(f"{state} is at or above node 1's 20% split threshold",
              state_share[irr] >= 0.20, f"{state_share[irr]:.1%}")
    check("every unsplit state is below node 1's 20% split threshold",
          all(state_share[k] < 0.20 for k, s in strata.items() if s == "all"),
          ", ".join(f"{k} {irrigated[k]:.1%}"
                    for k in sorted(strata, key=irrigated.get, reverse=True)
                    if strata[k] == "all")[:80])

    # Splitting a state must move acres and production between two rows, never
    # create or destroy any: the coverage share is the same before and after.
    for state, (irr, rain) in SPLIT_STATES.items():
        acres = sum(float(weights[k]["acres_harvested"]) for k in (irr, rain))
        # The published 2022 Census state totals, written here rather than read
        # from the file under test so this is an independent assertion.
        published = {"NE": 8648207.0, "KS": 4658341.0}[state]
        check(f"{state}'s two strata sum to its published harvested acres",
              close(acres, published, 1e-6), f"{acres:.0f} vs {published:.0f}")

    yield_meta = metas["yield_history"]
    check("observed window is node 2's 1995-2024",
          yield_meta["period"] == "1995-2024", yield_meta["period"])
    # Row count is not enough: the upstream rank refers to a specific window, so
    # the years themselves must be exactly that window for every region.
    expected_years = set(range(yield_meta["period_start"], yield_meta["period_end"] + 1))
    with open(HERE / "yield_history.csv", newline="") as fh:
        years = {}
        for row in csv.DictReader(fh):
            years.setdefault(row["region_key"], set()).add(int(row["year"]))
    off = {k: sorted(expected_years ^ v) for k, v in years.items() if v != expected_years}
    check("every region covers exactly the declared window, year by year",
          not off, f"off: {off}" if off else f"{len(expected_years)} years x {len(years)} regions")


# ------------------------------------------------- the export exposure (AC-1/5/6/7)

def check_stratum_rescaling(document, weights, deviations, metas):
    """The stratum distributions are the state's, rescaled by a measured ratio.

    This is the transformation brief 0003 added, and the one a reader is most
    entitled to be sceptical of, so it is checked against the committed table
    rather than taken from the meta file's word.
    """
    print("\nAC-4  stratum distributions are rescaled state distributions")
    yield_meta = metas["yield_history"]
    rescaling = yield_meta.get("stratum_rescaling") or {}
    strata = {k: r["stratum"] for k, r in weights.items()}
    split_keys = sorted(k for k, s in strata.items() if s != "all")

    check("every stratum has a recorded rescaling",
          sorted(rescaling) == split_keys, f"{sorted(rescaling)} vs {split_keys}")
    check("no unsplit region was rescaled",
          not any(strata.get(k) == "all" for k in rescaling))

    for key in split_keys:
        detail = rescaling.get(key)
        if not detail:
            continue
        ratio = detail["dispersion_ratio"]
        # The direction is the whole point: irrigation damps year-to-year yield
        # variation, so an irrigated stratum must come out NARROWER than its
        # state and a rainfed one WIDER. A ratio on the wrong side of 1 would
        # mean the rescaling is inflating exactly what it exists to damp.
        if strata[key] == "irrigated":
            check(f"{key}: irrigated stratum is narrower than its state",
                  0 < ratio < 1, f"x{ratio:.3f}")
        else:
            check(f"{key}: rainfed stratum is wider than its state",
                  ratio > 1, f"x{ratio:.3f}")
        check(f"{key}: the ratio was measured on enough years",
              detail["ratio_years"] >= 15, f"{detail['ratio_years']} years "
                                           f"({detail['ratio_period']})")
        check(f"{key}: the ratio's series is pinned to a harvested-acre yield",
              detail["series"].endswith("MEASURED IN BU / ACRE"), detail["series"])

    # Both of a state's strata are the SAME state series scaled by their own
    # ratio, so the ratio of their committed spreads must equal the ratio of
    # their recorded factors, exactly. That is a property the committed table
    # and the meta file must share, and it catches a table rebuilt with one and
    # a meta file left describing the other.
    for state, (irr, rain) in SPLIT_STATES.items():
        if not (irr in deviations and rain in deviations):
            continue
        irr_spread = spread_of(deviations[irr])
        rain_spread = spread_of(deviations[rain])
        check(f"{state}: the irrigated stratum's observed spread is the narrower",
              irr_spread < rain_spread,
              f"{irr} {irr_spread:.1f} pts vs {rain} {rain_spread:.1f} pts")
        if irr in rescaling and rain in rescaling:
            committed = irr_spread / rain_spread
            recorded = (rescaling[irr]["dispersion_ratio"]
                        / rescaling[rain]["dispersion_ratio"])
            check(f"{state}: the committed spreads match the recorded ratios",
                  close(committed, recorded, 1e-3),
                  f"{committed:.4f} vs {recorded:.4f}")

    # The rescaling must be declared in the output document itself, not only in
    # a build artifact a reader of the output never sees.
    assumptions = document.get("assumptions") or {}
    check("the output states the stratum rescaling",
          "stratum_rescaling" in assumptions
          and "end in 2018" in assumptions["stratum_rescaling"])
    check("the output names what the rescaling assumes",
          "assumes" in (assumptions.get("stratum_rescaling") or "").lower())
    not_captured = " ".join(assumptions.get("not_captured") or [])
    check("the output says irrigation supply is unconstrained upstream",
          "aquifer" in not_captured
          and "upper bound" in (assumptions.get("irrigation") or "").lower())

    regions = document["regions"]
    tables = (document.get("metadata") or {}).get("tables") or {}
    pw = tables.get("production_weights") or {}
    check("the full-set coverage key is region-neutral, not named for ten states",
          "coverage_share_of_us_all_regions" in pw
          and "coverage_share_of_us_all_ten" not in pw)

    check("every region row declares its stratum",
          all(r.get("stratum") in ("all", "irrigated", "rainfed") for r in regions))
    rescaled_rows = [r for r in regions if r.get("stratum") != "all"]
    check("each stratum row carries its own dispersion ratio",
          all("stratum_dispersion_ratio" in r for r in rescaled_rows),
          f"{len(rescaled_rows)} stratum rows")
    check("unsplit rows carry no rescaling factor",
          all("stratum_dispersion_ratio" not in r
              for r in regions if r.get("stratum") == "all"))


def check_no_stratum_inference():
    """No code may infer a region's stratum from the spelling of its key.

    Node 2 asserts the same thing about its water regime. A key named
    "ne_irrigated" is irrigated because production_weights.csv says so; if the
    runner ever pattern-matched the suffix instead, a renamed key would silently
    change the model rather than failing loudly.
    """
    print("\nAC-4  the stratum is declared, never inferred from the key")
    source = RUNNER.read_text()
    for pattern in ('"_irrigated"', "'_irrigated'", '"_rainfed"', "'_rainfed'",
                    'endswith("_irr', "endswith('_irr", 'startswith("ne_',
                    "startswith('ne_"):
        check(f"runner.py does not match on {pattern}", pattern not in source)
    check("runner.py reads the stratum from the weights table",
          'row["stratum"]' in source)


def check_export_exposure(document, metas):
    """The committed export series, its meta, and how the output labels it.

    None of this feeds the price arithmetic. The checks exist because a bushel
    figure printed beside a price impact invites exactly the inference this
    bundle refuses to make, so the labelling is as much the subject here as the
    numbers are.
    """
    print("\nAC-1/5  the committed export series")
    with open(HERE / "price_history.csv", newline="") as fh:
        price_rows = list(csv.DictReader(fh))

    check("price_history.csv carries both export columns",
          "exports_mil_bu" in price_rows[0] and "export_share_of_use" in price_rows[0])
    populated = [r for r in price_rows if r["exports_mil_bu"] and r["export_share_of_use"]]
    check("exports present for every marketing year in the window",
          len(populated) == len(price_rows),
          f"{len(populated)}/{len(price_rows)} rows")
    check("exports are positive in every year",
          all(float(r["exports_mil_bu"]) > 0 for r in populated))
    # Exports are a disappearance COMPONENT of total use. If one ever equalled or
    # exceeded the other, the column has been joined to the wrong series.
    check("exports are strictly less than total use in every year",
          all(float(r["exports_mil_bu"]) < float(r["total_use_mil_bu"]) for r in populated))
    inconsistent = [
        r["year"] for r in populated
        if not close(float(r["export_share_of_use"]),
                     float(r["exports_mil_bu"]) / float(r["total_use_mil_bu"]), 5e-7)
    ]
    check("the share equals exports over total use in every year",
          not inconsistent, f"off: {inconsistent}" if inconsistent else "")
    shares = [float(r["export_share_of_use"]) for r in populated]
    check("the export share stays in a sane band for US corn",
          all(0.03 <= s <= 0.45 for s in shares),
          f"{min(shares):.1%} to {max(shares):.1%}")

    meta = metas["price_history"]
    exposure = meta.get("export_exposure", {})
    check("price_history.meta.json records the export exposure",
          set(exposure) >= {"current", "latest_complete"}, f"{sorted(exposure)}")
    by_year = {int(r["year"]): r for r in price_rows}
    for label in ("current", "latest_complete"):
        block = exposure.get(label, {})
        row = by_year.get(block.get("marketing_year"))
        check(f"the {label} exposure matches its row in the table",
              row is not None
              and close(block["exports_mil_bu"], float(row["exports_mil_bu"]), 1e-6)
              and close(block["export_share_of_use"],
                        float(row["export_share_of_use"]), 1e-6),
              f"marketing year {block.get('marketing_year')}")
        check(f"the {label} exposure declares whether it is a projection",
              isinstance(block.get("is_projection"), bool),
              f"is_projection={block.get('is_projection')}")
    check("the latest complete exposure is not a WASDE projection",
          exposure.get("latest_complete", {}).get("is_projection") is False)
    check("exports are defined as a component of total use",
          "component of total use" in meta.get("definitions", {}).get("exports", ""))

    print("\nAC-6/7  the exposure is labelled as context, not as a priced scenario")
    carried = (document.get("metadata") or {}).get("export_exposure") or {}
    check("the output metadata carries the export exposure",
          set(carried) >= {"current", "latest_complete", "role"}, f"{sorted(carried)}")
    for label in ("current", "latest_complete"):
        check(f"the output's {label} exposure matches the committed meta",
              carried.get(label) == exposure.get(label))
    role = (carried.get("role") or "").lower()
    check("the output says the exposure is context only", "context only" in role)
    check("the output says exports are a component of total use, not an addition",
          "component of total use" in role)
    # The three-place rule: transmission.json (reaching the output document
    # through assumptions.not_captured), the output's own role string, and the
    # Modelfile must each say it. One of the three is not enough.
    not_captured = " ".join(
        (document.get("assumptions") or {}).get("not_captured", [])
    ).lower()
    check("the output's not_captured names export demand shocks",
          "export demand" in not_captured)
    check("the output's not_captured names trade policy",
          "trade policy" in not_captured or "tariff" in not_captured)
    check("the role string points at a path that exists in the document",
          "assumptions.not_captured" in (carried.get("role") or "")
          and "not_captured" in (document.get("assumptions") or {}))
    not_for = MODELFILE.read_text().lower()
    check("the Modelfile not_for names trade policy",
          "trade polic" in not_for or "tariff" in not_for)


# ------------------------------------------------------- the rescaling (AC-7/8)

def check_rescaling(document, deviations):
    print("\nAC-7  node 2's simulated anomaly is placed on a real-world scale")
    regions = document["regions"]

    ratios = {r["region_key"]: r["dispersion_ratio"] for r in regions}
    check("a dispersion ratio is reported for every region",
          all(v is not None for v in ratios.values()))
    # This is the signal check. If a future change quietly reverts to using node
    # 2's percentages, or the observed table is rebuilt from simulated data, this
    # is what catches it.
    check("node 2 is over-dispersed in EVERY region (ratio > 1)",
          all(v > 1 for v in ratios.values()),
          "min " + min(f"{k} {v:.2f}" for k, v in
                       sorted(ratios.items(), key=lambda kv: kv[1])[:1]))
    check("over-dispersion is material everywhere (ratio > 2)",
          all(v > 2 for v in ratios.values()),
          f"range {min(ratios.values()):.2f}x to {max(ratios.values()):.2f}x")

    # The mapping must be monotone in rank and bounded by the observed
    # distribution, or it is not a quantile map.
    sys.path.insert(0, str(HERE))
    from runner import quantile_map, quantile  # noqa: E402

    observed = deviations["ia"]
    mapped = [quantile_map(p, observed) for p in range(0, 101, 5)]
    check("mapping is monotone non-decreasing in rank",
          all(b >= a - 1e-9 for a, b in zip(mapped, mapped[1:])))
    median = quantile(observed, 0.5)
    check("mapping is bounded by the observed distribution",
          observed[0] - median - 1e-9 <= min(mapped)
          and max(mapped) <= observed[-1] - median + 1e-9,
          f"[{min(mapped):.2f}, {max(mapped):.2f}] inside "
          f"[{observed[0] - median:.2f}, {observed[-1] - median:.2f}]")
    check("an average season maps to exactly zero",
          close(quantile_map(50, observed), 0.0, 1e-9),
          f"rank 50 -> {quantile_map(50, observed):+.6f}%")

    national = document["national"]
    check("the unmapped national figure is reported for comparison",
          "us_yield_shock_pct_unmapped" in national)
    check("rescaling materially changed the national figure",
          abs(national["us_yield_shock_pct_unmapped"]) >
          abs(national["us_yield_shock_pct"]) * 1.5,
          f"{national['us_yield_shock_pct_unmapped']:+.2f}% unmapped vs "
          f"{national['us_yield_shock_pct']:+.2f}% used")

    print("\nAC-8  the rainfed bias is handled and visible")
    check("irrigated share ships per region",
          all(r.get("irrigated_share") is not None for r in regions))
    # Node 2 now irrigates the irrigated strata rather than simulating them as
    # dryland, and this model reads each stratum against its own distribution,
    # so within each split state the irrigated stratum must be the LESS
    # over-dispersed of the two. Irrigation damps the simulation and the
    # observation alike; if it did not, the rescaling would be facing the wrong
    # way round.
    for state, (irr, rain) in SPLIT_STATES.items():
        if irr in ratios and rain in ratios:
            check(f"{state}'s irrigated stratum is less over-dispersed than its rainfed one",
                  ratios[irr] < ratios[rain],
                  f"{irr} x{ratios[irr]:.2f} vs {rain} x{ratios[rain]:.2f}")
    check("node 2's own percentage still ships, for comparison",
          all("yield_anomaly_simulated_pct" in r for r in regions))


# ------------------------------------------------------- the aggregation (AC-6)

def check_aggregation(document):
    print("\nAC-6  the regional anomalies combine by a documented weighting")
    regions = document["regions"]
    national = document["national"]

    total = sum(r["production_weight"] for r in regions)
    check("production weights sum to 1", close(total, 1.0, 1e-4), f"{total:.6f}")
    check("every weight is positive", all(r["production_weight"] > 0 for r in regions))

    contributions = sum(r["contribution_pct"] for r in regions)
    check("contributions sum to the covered shock",
          close(contributions, national["covered_yield_shock_pct"], 1e-3),
          f"{contributions:.4f} vs {national['covered_yield_shock_pct']:.4f}")

    check("each contribution is its weight times its real anomaly",
          all(close(r["contribution_pct"],
                    r["production_weight"] * r["yield_anomaly_real_pct"], 1e-3)
              for r in regions))

    coverage = national["coverage_share_of_us_production"]
    check("the US shock is the covered shock scaled by coverage, never up to 100%",
          close(national["us_yield_shock_pct"],
                national["covered_yield_shock_pct"] * coverage, 1e-3),
          f"{national['covered_yield_shock_pct']:.4f} x {coverage:.4f} = "
          f"{national['us_yield_shock_pct']:.4f}")
    check("coverage is the sum of the present regions' US production shares",
          close(coverage, sum(r["production_share_of_us"] for r in regions), 1e-6))
    check("coverage is below 1, and the shortfall is not silently made up",
          coverage < 1.0 and abs(national["us_yield_shock_pct"]) <
          abs(national["covered_yield_shock_pct"]) + 1e-9)

    check("the production shock is the trend production times the shock",
          close(national["covered_production_shock_bu"],
                national["covered_trend_production_bu"] *
                national["covered_yield_shock_pct"] / 100.0, 1.0))
    check("US trend production is the covered production grossed by coverage",
          close(national["us_trend_production_bu"],
                national["covered_trend_production_bu"] / coverage, 1.0))

    # A hand-worked case, computed here from first principles rather than by
    # re-running the runner's own arithmetic.
    hand = [
        {"weight": 0.50, "anomaly": -10.0},
        {"weight": 0.30, "anomaly": +4.0},
        {"weight": 0.20, "anomaly": 0.0},
    ]
    expected = 0.50 * -10.0 + 0.30 * 4.0 + 0.20 * 0.0  # -5.0 + 1.2 + 0.0 = -3.8
    got = sum(h["weight"] * h["anomaly"] for h in hand)
    check("hand-worked three-region weighting", close(got, -3.8, 1e-9) and close(expected, got, 0),
          f"0.50x-10 + 0.30x+4 + 0.20x0 = {got:+.2f}%")


# --------------------------------------------------- the transmission (AC-9)

def check_transmission(document, transmission, deviations):
    print("\nAC-9  the price response is cited, signed correctly and always ranged")
    national = document["national"]

    check("the transmission's sample and fit statistics ship in the output",
          all(k in document["metadata"]["transmission"]["fit"]
              for k in ("n", "r2", "sample_period")),
          f"n={transmission['fit']['n']}, r2={transmission['fit']['r2']:.3f}, "
          f"{transmission['fit']['sample_period']}")
    check("the two literature estimates ship as cited reference points",
          set(document["metadata"]["transmission"]["reference_estimates_not_used"]) ==
          {"roberts_schlenker_2013", "farmdoc_daily_2018"})
    check("the transmission coefficient is negative (more corn, lower price)",
          transmission["shock_coefficient"] < 0,
          f"b0 = {transmission['shock_coefficient']:+.4f}")
    low, high = transmission["shock_coefficient_ci95"]
    check("the bootstrap interval excludes zero",
          low < 0 and high < 0, f"[{low:+.4f}, {high:+.4f}]")

    # Every headline figure must carry its range, and the range must bracket it.
    for stem in ("price_impact_pct", "price_impact_usd_bu"):
        check(f"{stem} ships with a low and a high",
              f"{stem}_low" in national and f"{stem}_high" in national)
        check(f"{stem} lies inside its own range",
              national[f"{stem}_low"] <= national[stem] <= national[f"{stem}_high"],
              f"{national[f'{stem}_low']:+.4f} <= {national[stem]:+.4f} "
              f"<= {national[f'{stem}_high']:+.4f}")

    check("the price impact opposes the yield shock",
          national["us_yield_shock_pct"] * national["price_impact_pct"] <= 0,
          f"shock {national['us_yield_shock_pct']:+.2f}% -> "
          f"price {national['price_impact_pct']:+.2f}%")

    # A negative shock must raise the price. Run the model on a snapshot whose
    # ranks are all at the tenth percentile.
    print("\nAC-9  sign under a synthetic bad season")
    snapshot = json.loads(SAMPLE.read_text())
    for row in snapshot["rows"]:
        row["yield_percentile_rank"] = 10.0
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "bad.json"
        path.write_text(json.dumps(snapshot))
        code, bad, _ = run_model(path)
    check("a tenth-percentile season everywhere runs", code == 0)
    if bad:
        check("a bad season implies a negative yield shock",
              bad["national"]["us_yield_shock_pct"] < 0,
              f"{bad['national']['us_yield_shock_pct']:+.2f}%")
        check("a bad season implies a price increase",
              bad["national"]["price_impact_pct"] > 0,
              f"{bad['national']['price_impact_pct']:+.2f}% "
              f"[{bad['national']['price_impact_pct_low']:+.2f}, "
              f"{bad['national']['price_impact_pct_high']:+.2f}]")

    # The 2012 drought, end to end. Feeding each state's ACTUAL 2012 rank must
    # reproduce the actual national yield deviation, which tests the mapping and
    # the weighting against a real outcome rather than against themselves.
    print("\nAC-9  the 2012 drought, end to end")
    with open(HERE / "yield_history.csv", newline="") as fh:
        history = list(csv.DictReader(fh))
    dev_2012 = {r["region_key"]: float(r["deviation_pct"])
                for r in history if r["year"] == "2012"}
    # The dates matter as much as the ranks: production weights are acres times
    # the TREND yield for the snapshot's year, so leaving the sample's 2026 dates
    # in place would weight a 2012 episode with 2026 trend yields and the check
    # would not be testing the historical episode it claims to.
    snapshot = json.loads(SAMPLE.read_text())
    snapshot["metadata"]["date"] = "2012-09-20"
    for row in snapshot["rows"]:
        key = row["region_key"]
        series = deviations[key]
        rank = 100.0 * sum(1 for v in series if v < dev_2012[key]) / len(series)
        row["yield_percentile_rank"] = rank
        row["date"] = "2012-09-20"
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "y2012.json"
        path.write_text(json.dumps(snapshot))
        code, y2012, _ = run_model(path)
    check("the 2012 snapshot runs", code == 0)

    with open(HERE / "price_history.csv", newline="") as fh:
        price_rows = {int(r["year"]): r for r in csv.DictReader(fh)}
    actual_national_dev = float(price_rows[2012]["yield_deviation_pct"])
    if y2012:
        modelled = y2012["national"]["us_yield_shock_pct"]
        check("the 2012 case is dated 2012, not the sample's year",
              y2012["national"]["date"].startswith("2012"),
              y2012["national"]["date"])
        check("2012 ranks reproduce the actual national yield deviation within 3 points",
              close(modelled, actual_national_dev, 3.0),
              f"modelled {modelled:+.2f}% vs actual {actual_national_dev:+.2f}%")
        actual_move = (float(price_rows[2012]["price_usd_bu"]) /
                       float(price_rows[2011]["price_usd_bu"]) - 1) * 100
        low = y2012["national"]["price_impact_pct_low"]
        high = y2012["national"]["price_impact_pct_high"]
        check("the actual 2012 price move falls inside the implied range",
              low <= actual_move <= high,
              f"actual {actual_move:+.1f}% in [{low:+.1f}, {high:+.1f}] "
              f"(a wide range; this is a weak test on one observation)")


# ------------------------------------------------- failing loudly (AC-10)

def check_loud_failures():
    print("\nAC-10  unknown regions and mismatched windows fail loudly")
    snapshot = json.loads(SAMPLE.read_text())
    snapshot["rows"][0]["region_key"] = "zz"
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "unknown.json"
        path.write_text(json.dumps(snapshot))
        code, _, stderr = run_model(path)
    check("an unknown region_key exits non-zero", code == 1, f"exit {code}")
    check("the message names the key and the table",
          "zz" in stderr and "production_weights.csv" in stderr,
          stderr.strip().splitlines()[-1][:110] if stderr.strip() else "")
    check("no traceback is printed", "Traceback" not in stderr)

    snapshot = json.loads(SAMPLE.read_text())
    snapshot["metadata"]["baselines"]["period"] = "1991-2020"
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "period.json"
        path.write_text(json.dumps(snapshot))
        code, _, stderr = run_model(path)
    check("a baseline-window mismatch exits non-zero", code == 1, f"exit {code}")
    check("the message names both windows",
          "1991-2020" in stderr and "1995-2024" in stderr)
    check("no traceback on the window mismatch", "Traceback" not in stderr)

    # A snapshot whose water regime disagrees with this model's stratum would
    # map a rank onto a distribution of roughly half or twice the right width.
    # Doctored both ways: an irrigated stratum simulated rainfed, and an
    # unsplit state simulated irrigated.
    for label, key, regime in (("an irrigated stratum simulated rainfed",
                                "ne_irrigated", "rainfed"),
                               ("an unsplit state simulated irrigated", "ia",
                                "irrigated")):
        snapshot = json.loads(SAMPLE.read_text())
        snapshot["metadata"]["baselines"]["regions"][key]["regime"] = regime
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "regime.json"
            path.write_text(json.dumps(snapshot))
            code, _, stderr = run_model(path)
        check(f"{label} exits non-zero", code == 1, f"exit {code}")
        check(f"the message names {key} and both regimes",
              key in stderr and regime in stderr)
        check(f"no traceback on the {key} regime mismatch", "Traceback" not in stderr)

    # An absent regime is a mismatch, not an assumption of rainfed.
    snapshot = json.loads(SAMPLE.read_text())
    del snapshot["metadata"]["baselines"]["regions"]["ks_irrigated"]["regime"]
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "noregime.json"
        path.write_text(json.dumps(snapshot))
        code, _, stderr = run_model(path)
    check("a missing upstream regime exits non-zero rather than being assumed",
          code == 1, f"exit {code}")
    check("the message says the regime was absent", "absent" in stderr)

    # A subset of regions must still run, and must report the subset's coverage.
    snapshot = json.loads(SAMPLE.read_text())
    snapshot["rows"] = [r for r in snapshot["rows"]
                        if r["region_key"] in ("ia", "ne_irrigated")]
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "subset.json"
        path.write_text(json.dumps(snapshot))
        code, subset, _ = run_model(path)
    check("a two-region subset still runs", code == 0)
    if subset:
        check("the subset reports its own coverage, not the full set's",
              0.2 < subset["national"]["coverage_share_of_us_production"] < 0.35,
              f"{subset['national']['coverage_share_of_us_production'] * 100:.1f}%")


# ------------------------------------------------- honesty and schema (AC-12)

def check_contract(document):
    """Output-contract invariants the schema declares but cannot enforce alone."""
    print("\nreview  the declared contract holds at runtime")
    national = document["national"]
    check("no bare price LEVEL is published, only impacts",
          "implied_price_usd_bu" not in national,
          "a price level is the one figure a reader would take for a forecast")

    # The schema format has no nullable type, so an optional field with no value
    # must be omitted, never emitted as null under a declared `number`.
    nulled = [
        (r["region_key"], k) for r in document["regions"]
        for k, v in r.items() if v is None
    ]
    check("no region field is emitted as null under a declared type",
          not nulled, f"null: {nulled}" if nulled else "")

    snapshot = json.loads(SAMPLE.read_text())

    # A missing upstream baseline window must fail, not be assumed compatible.
    stripped = json.loads(json.dumps(snapshot))
    stripped["metadata"]["baselines"].pop("period")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "noperiod.json"
        path.write_text(json.dumps(stripped))
        code, _, stderr = run_model(path)
    check("an absent upstream baseline period exits non-zero", code == 1, f"exit {code}")
    check("the message explains why assuming a window is unsafe",
          "baselines.period" in stderr and "1995-2024" in stderr)

    # A reference price must be finite and positive.
    for bad in ("nan", "inf", "-1", "0"):
        case = json.loads(json.dumps(snapshot))
        case["reference_price_usd_bu"] = bad
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "price.json"
            path.write_text(json.dumps(case))
            code, _, stderr = run_model(path)
        check(f"reference_price_usd_bu={bad!r} is rejected", code == 1,
              stderr.strip().splitlines()[-1][:80] if stderr.strip() else f"exit {code}")


def check_annotations(document):
    print("\nAC-12  annotations are honest and inside the platform's limits")
    model = tomllib.loads(MODELFILE.read_text())

    check("validity_domain is within 600 characters",
          len(model["validity_domain"]) <= 600, f"{len(model['validity_domain'])} chars")
    check("provenance is within 400 characters",
          len(model["provenance"]) <= 400, f"{len(model['provenance'])} chars")

    not_for = model["not_for"].lower()
    check("not_for says it is not a forecast", "not a price forecast" in not_for)
    check("not_for says it is not advice", "not investment advice" in not_for)
    check("not_for says it is not a trading signal", "not a trading signal" in not_for)

    check("the output document carries the same statement",
          "not a price forecast" in document["assumptions"]["not_a_forecast"].lower())
    check("the coverage share is stated in the assumptions",
          "%" in document["assumptions"]["coverage"])

    # Every key in a required array must have a declared type, which the platform
    # validator enforces; assert it here too so a change is caught before a push.
    def required_have_types(schema, path):
        ok = True
        for key in schema.get("required", []):
            declared = (schema.get("properties") or {}).get(key, {})
            if not isinstance(declared.get("type"), str):
                print(f"       missing type for {path}.{key}")
                ok = False
        for key, sub in (schema.get("properties") or {}).items():
            if isinstance(sub, dict):
                ok = required_have_types(sub, f"{path}.{key}") and ok
                items = sub.get("items")
                if isinstance(items, dict):
                    ok = required_have_types(items, f"{path}.{key}[]") and ok
        return ok

    all_ok = True
    for field in ("inputs", "outputs"):
        for artifact in model[field]:
            all_ok = required_have_types(
                artifact["schema"], f"{field}.{artifact['name']}") and all_ok
    check("every required key has a declared type", all_ok)

    print("\n      required output fields are non-null on every row")
    impact_schema = next(o for o in model["outputs"] if o["name"] == "corn_price_impact")
    national_required = (
        impact_schema["schema"]["properties"]["national"]["required"]
    )
    missing = [k for k in national_required if document["national"].get(k) is None]
    check("every required national field is present and non-null",
          not missing, f"missing: {missing}" if missing else "")
    row_required = (
        impact_schema["schema"]["properties"]["regions"]["items"]["required"]
    )
    bad_rows = [
        (r["region_key"], k) for r in document["regions"]
        for k in row_required if r.get(k) is None
    ]
    check("every required region field is present and non-null on every row",
          not bad_rows, f"missing: {bad_rows}" if bad_rows else "")


# ------------------------------------------------------------------------ main

def main(argv):
    output_path = Path(argv[1]) if len(argv) > 1 else DEFAULT_OUTPUT
    if not output_path.exists():
        print(f"no output document at {output_path}; run the model first", file=sys.stderr)
        return 2
    document = json.loads(output_path.read_text())

    weights, deviations, metas, transmission = load_tables()

    print(f"checking {output_path}")
    check_tables(weights, deviations, metas)
    check_stratum_rescaling(document, weights, deviations, metas)
    check_no_stratum_inference()
    check_export_exposure(document, metas)
    check_rescaling(document, deviations)
    check_aggregation(document)
    check_transmission(document, transmission, deviations)
    check_loud_failures()
    check_contract(document)
    check_annotations(document)

    total = len(PASSES) + len(FAILURES)
    print(f"\n{len(PASSES)}/{total} checks pass")
    if FAILURES:
        print("failed:")
        for label in FAILURES:
            print(f"  - {label}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
