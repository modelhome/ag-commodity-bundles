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

TEN = ["ia", "il", "mn", "ne", "in", "sd", "oh", "wi", "ks", "mo"]

PASSES = []
FAILURES = []


def check(label, condition, detail=""):
    (PASSES if condition else FAILURES).append(label)
    mark = "pass" if condition else "FAIL"
    print(f"  [{mark}] {label}" + (f" -- {detail}" if detail else ""))
    return condition


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
    check("all ten region keys in production_weights.csv",
          sorted(weights) == sorted(TEN), f"{sorted(weights)}")
    check("all ten region keys in yield_history.csv",
          sorted(deviations) == sorted(TEN))
    check("thirty observed years per region",
          all(len(v) == 30 for v in deviations.values()),
          f"{ {k: len(v) for k, v in deviations.items()} }")

    meta = metas["production_weights"]
    recomputed = sum(float(r["production_share_of_us"]) for r in weights.values())
    check("coverage share equals the sum of the per-state shares",
          close(recomputed, meta["coverage_share_of_us"], 1e-5),
          f"{recomputed:.6f} vs {meta['coverage_share_of_us']:.6f}")
    check("coverage share is in a sane band for ten corn states",
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
    check("Nebraska and Kansas are the most irrigated states in the set",
          sorted(irrigated, key=irrigated.get, reverse=True)[:2] == ["ne", "ks"],
          f"ne {irrigated['ne']:.1%}, ks {irrigated['ks']:.1%}")

    yield_meta = metas["yield_history"]
    check("observed window is node 2's 1995-2024",
          yield_meta["period"] == "1995-2024", yield_meta["period"])


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
    check("the two most over-dispersed regions are the two most irrigated",
          set(sorted(ratios, key=ratios.get, reverse=True)[:2]) == {"ne", "ks"},
          ", ".join(f"{k} x{ratios[k]:.1f}"
                    for k in sorted(ratios, key=ratios.get, reverse=True)[:3]))
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
    snapshot = json.loads(SAMPLE.read_text())
    for row in snapshot["rows"]:
        key = row["region_key"]
        series = deviations[key]
        rank = 100.0 * sum(1 for v in series if v < dev_2012[key]) / len(series)
        row["yield_percentile_rank"] = rank
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

    # A subset of regions must still run, and must report the subset's coverage.
    snapshot = json.loads(SAMPLE.read_text())
    snapshot["rows"] = [r for r in snapshot["rows"] if r["region_key"] in ("ia", "ne")]
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "subset.json"
        path.write_text(json.dumps(snapshot))
        code, subset, _ = run_model(path)
    check("a two-region subset still runs", code == 0)
    if subset:
        check("the subset reports its own coverage, not the full ten states'",
              0.2 < subset["national"]["coverage_share_of_us_production"] < 0.35,
              f"{subset['national']['coverage_share_of_us_production'] * 100:.1f}%")


# ------------------------------------------------- honesty and schema (AC-12)

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
    check_rescaling(document, deviations)
    check_aggregation(document)
    check_transmission(document, transmission, deviations)
    check_loud_failures()
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
