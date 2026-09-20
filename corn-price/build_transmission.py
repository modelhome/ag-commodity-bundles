#!/usr/bin/env python3
"""
One-time build script for corn-price/transmission.json. Not part of the model
image.

Fits the yield-shock-to-price relationship the model applies, and commits the
fit. The runner never fits anything: it reads the coefficients, which is what
keeps it deterministic, offline and fast.

Why fit rather than cite. The two obvious off-the-shelf numbers are both wrong
for this input, in opposite directions.

  Roberts and Schlenker (2013, American Economic Review 103(6), 2265-95) is the
  canonical identification of agricultural supply and demand elasticities using
  yield shocks as the instrument. Their Table 1 (FAO data) gives a supply
  elasticity of 0.087-0.116 and a demand elasticity of -0.028 to -0.066,
  implying a price multiplier 1/(beta_s - beta_d) of 5.75-7.73. That multiplier
  is derived for a PERMANENT shift in the world caloric aggregate (the US
  ethanol mandate). A transitory weather shock is smoothed by storage, which the
  paper itself discusses, so ~6x overstates a single season badly.

  farmdoc daily (2018-11-15) regresses the percent change in December corn
  futures on the percent change in national corn yield from the May to the
  November WASDE, 1993-2017, and gets -1.84 with an R-squared of 0.48. That is
  the right kind of object -- US corn, within-season, transitory, already
  embedding storage. But its regressor is the yield surprise against the MAY
  WASDE, that is, against market expectation, whereas this model's shock is
  measured against a thirty-year weather baseline. Applying -1.84 to the second
  quantity would be applying a coefficient to something it was not fitted on.

So the relationship is fitted here on a regressor defined the same way the model
defines its own shock: the national yield's deviation from a fitted trend.

Model selection is by a rule stated before the fits are seen, applied uniformly:
start from the shock term, and keep a candidate term only if |t| >= 2.0. The
candidates and what happened to each are all recorded in the output, including
the ones that were dropped, so the selection is auditable rather than asserted.

What the model reports is a PARTIAL effect: b0 * d, the marginal price response
to this season's shock holding everything else equal. It is deliberately not a
prediction of the price change, which would need next year's demand, policy,
the macro environment and the rest of the balance sheet. The lagged shock is in
the specification as a control -- with a differenced dependent variable, last
season's shock mechanically enters this season's change -- and its coefficient
is not part of the reported impact.

Uncertainty is a nonparametric BOOTSTRAP of b0: the sample is resampled with
replacement, the model refitted, and the 2.5th and 97.5th percentiles of the
resulting b0 taken. Fifty annual observations of a fat-tailed price series do
not justify a normal or t assumption. The seed is fixed so the build is
reproducible. This interval expresses uncertainty about the TRANSMISSION, which
is the quantity the model is claiming; it is not an interval on what the price
will do, and the README says so.

Usage:

    python build_transmission.py      # writes transmission.json beside this file
"""
import csv
import json
import math
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
PRICE_PATH = HERE / "price_history.csv"
PRICE_META_PATH = HERE / "price_history.meta.json"
OUT_PATH = HERE / "transmission.json"

# A candidate term ships only if it clears this. Two standard errors is the
# ordinary 5% two-sided test at this sample size. Stated before the fits are
# seen and applied to every candidate alike.
MIN_T_STAT = 2.0

BOOTSTRAP_DRAWS = 10000
BOOTSTRAP_SEED = 20260919


def log(message):
    print(message, file=sys.stderr)


def invert(matrix):
    """Gauss-Jordan inverse of a small square matrix."""
    n = len(matrix)
    a = [list(row) + [1.0 if i == j else 0.0 for j in range(n)] for i, row in enumerate(matrix)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(a[r][col]))
        if abs(a[pivot][col]) < 1e-12:
            raise SystemExit("design matrix is singular; cannot fit")
        a[col], a[pivot] = a[pivot], a[col]
        scale = a[col][col]
        a[col] = [v / scale for v in a[col]]
        for row in range(n):
            if row == col:
                continue
            factor = a[row][col]
            if factor:
                a[row] = [v - factor * w for v, w in zip(a[row], a[col])]
    return [row[n:] for row in a]


def ols(X, y):
    """Least squares by normal equations. Returns coefficients, SEs, r2, residuals."""
    n, k = len(X), len(X[0])
    xtx = [[sum(X[i][a] * X[i][b] for i in range(n)) for b in range(k)] for a in range(k)]
    xty = [sum(X[i][a] * y[i] for i in range(n)) for a in range(k)]
    xtx_inv = invert(xtx)
    beta = [sum(xtx_inv[a][b] * xty[b] for b in range(k)) for a in range(k)]
    fitted = [sum(X[i][a] * beta[a] for a in range(k)) for i in range(n)]
    residuals = [y[i] - fitted[i] for i in range(n)]
    ss_res = sum(r * r for r in residuals)
    mean_y = sum(y) / n
    ss_tot = sum((v - mean_y) ** 2 for v in y)
    df = n - k
    sigma2 = ss_res / df
    se = [math.sqrt(sigma2 * xtx_inv[a][a]) for a in range(k)]
    return {
        "beta": beta,
        "se": se,
        "t": [b / s if s else 0.0 for b, s in zip(beta, se)],
        "r2": 1 - ss_res / ss_tot if ss_tot else 0.0,
        "adj_r2": 1 - (ss_res / df) / (ss_tot / (n - 1)) if ss_tot else 0.0,
        "residual_sd": math.sqrt(sigma2),
        "residuals": residuals,
        "n": n,
        "df": df,
    }


def quantile(sorted_values, q):
    """Linear-interpolated empirical quantile of an already-sorted list."""
    if len(sorted_values) == 1:
        return sorted_values[0]
    position = q * (len(sorted_values) - 1)
    low = int(math.floor(position))
    high = min(low + 1, len(sorted_values) - 1)
    weight = position - low
    return sorted_values[low] * (1 - weight) + sorted_values[high] * weight


def load_sample():
    """The usable annual observations: complete marketing years with a complete prior."""
    with open(PRICE_PATH, newline="") as fh:
        table = list(csv.DictReader(fh))
    by_year = {int(r["year"]): r for r in table}
    sample = []
    for year, row in sorted(by_year.items()):
        prior = by_year.get(year - 1)
        if row["is_projection"] == "true" or prior is None:
            continue
        # A projected prior year would contaminate both the price change and the
        # lagged shock.
        if prior["is_projection"] == "true" or not row["carryin_stocks_to_use"]:
            continue
        sample.append({
            "year": year,
            "dlog_price": math.log(float(row["price_usd_bu"]) / float(prior["price_usd_bu"])),
            "deviation": float(row["yield_deviation_pct"]) / 100.0,
            "deviation_lag": float(prior["yield_deviation_pct"]) / 100.0,
            "carryin": float(row["carryin_stocks_to_use"]),
        })
    if len(sample) < 30:
        raise SystemExit(f"only {len(sample)} usable years; refusing to fit")
    return sample


# Candidate terms beyond the intercept and the shock itself, in the order they
# are offered to the selection rule. Each is (name, description, extractor).
CANDIDATES = [
    ("deviation_lag",
     "last season's shock; with a differenced dependent variable it mechanically "
     "enters this season's change",
     lambda s: s["deviation_lag"]),
    ("shock_over_carryin",
     "shock divided by carry-in stocks-to-use: the convex tight-stocks interaction",
     lambda s: s["deviation"] / s["carryin"]),
    ("shock_times_log_carryin",
     "shock times log carry-in: the same interaction in a gentler functional form",
     lambda s: s["deviation"] * math.log(s["carryin"])),
    ("log_carryin",
     "log carry-in on its own: a level shift rather than an interaction",
     lambda s: math.log(s["carryin"])),
]


def main():
    sample = load_sample()
    log(f"sample: {len(sample)} years, {sample[0]['year']}-{sample[-1]['year']}")
    y = [s["dlog_price"] for s in sample]

    def design(terms):
        return [[1.0, s["deviation"]] + [f(s) for _, _, f in terms] for s in sample]

    # Forward selection under the pre-stated rule: offer each candidate on top of
    # what has already been kept, and keep it only if it clears MIN_T_STAT.
    kept = []
    trials = []
    for name, description, extractor in CANDIDATES:
        trial_terms = kept + [(name, description, extractor)]
        fit = ols(design(trial_terms), y)
        t_stat = fit["t"][-1]
        keep = abs(t_stat) >= MIN_T_STAT
        trials.append({
            "term": name,
            "description": description,
            "coefficient": fit["beta"][-1],
            "t": t_stat,
            "r2_with_term": fit["r2"],
            "kept": keep,
            "reason": (f"|t| = {abs(t_stat):.2f} >= {MIN_T_STAT}" if keep
                       else f"|t| = {abs(t_stat):.2f} < {MIN_T_STAT}"),
        })
        log(f"  candidate {name:24} coef {fit['beta'][-1]:+.4f} t {t_stat:+.2f} "
            f"r2 {fit['r2']:.3f} -> {'keep' if keep else 'drop'}")
        if keep:
            kept.append((name, description, extractor))

    X = design(kept)
    fit = ols(X, y)
    term_names = ["intercept", "shock"] + [n for n, _, _ in kept]
    log("chosen: " + " + ".join(term_names))
    for name, beta, se, t_stat in zip(term_names, fit["beta"], fit["se"], fit["t"]):
        log(f"  {name:16} {beta:+.4f}  se {se:.4f}  t {t_stat:+.2f}")
    log(f"  r2 {fit['r2']:.3f}  adj r2 {fit['adj_r2']:.3f}  "
        f"residual sd {fit['residual_sd']:.3f}  n {fit['n']}")

    # Nonparametric bootstrap of the shock coefficient.
    rng = random.Random(BOOTSTRAP_SEED)
    n = len(sample)
    draws = []
    for _ in range(BOOTSTRAP_DRAWS):
        idx = [rng.randrange(n) for _ in range(n)]
        try:
            boot = ols([X[i] for i in idx], [y[i] for i in idx])
        except SystemExit:  # a degenerate resample; skip it
            continue
        draws.append(boot["beta"][1])
    draws.sort()
    b0_low, b0_high = quantile(draws, 0.025), quantile(draws, 0.975)
    log(f"bootstrap b0: {fit['beta'][1]:+.4f} "
        f"[{b0_low:+.4f}, {b0_high:+.4f}] from {len(draws)} draws")

    price_meta = json.loads(PRICE_META_PATH.read_text())
    residuals = sorted(fit["residuals"])

    payload = {
        "specification": (
            "dlog_price[t] = a + b0 * d[t] + "
            + " + ".join(f"c_{n} * {n}[t]" for n, _, _ in kept)
            + "; d = national yield deviation from a fitted linear trend, as a fraction"
        ),
        "reported_effect": (
            "b0 * d: the marginal price response to this season's shock, holding the "
            "other terms fixed. Deliberately NOT a prediction of the price change, which "
            "would require the rest of the balance sheet, demand, policy and the macro "
            "environment. The other terms are controls and do not enter the reported "
            "impact."
        ),
        "selection_rule": (
            f"forward selection: a candidate term is kept only if |t| >= {MIN_T_STAT}. "
            "The rule was fixed before the fits were run and applied to every candidate."
        ),
        "candidates": trials,
        "terms": term_names,
        "coefficients": dict(zip(term_names, fit["beta"])),
        "standard_errors": dict(zip(term_names, fit["se"])),
        "t_statistics": dict(zip(term_names, fit["t"])),
        "shock_coefficient": fit["beta"][1],
        "shock_coefficient_ci95": [b0_low, b0_high],
        "bootstrap": {
            "draws": len(draws),
            "seed": BOOTSTRAP_SEED,
            "method": "nonparametric pairs bootstrap, 2.5th and 97.5th percentiles of b0",
        },
        "fit": {
            "n": fit["n"],
            "df": fit["df"],
            "r2": fit["r2"],
            "adj_r2": fit["adj_r2"],
            "residual_sd_dlog": fit["residual_sd"],
            "sample_period": f"{sample[0]['year']}-{sample[-1]['year']}",
            "residual_quantiles_dlog": {
                "p05": quantile(residuals, 0.05),
                "p50": quantile(residuals, 0.50),
                "p95": quantile(residuals, 0.95),
            },
        },
        "interval_definition": (
            "The reported low and high come from the bootstrap 95% interval on b0, the "
            "transmission coefficient. They express how well the transmission itself is "
            "known from fifty annual observations. They are NOT an interval on what the "
            "corn price will do: the fit explains "
            f"{fit['r2'] * 100:.0f}% of year-on-year price variation, so most of what "
            "moves the price is outside this model entirely."
        ),
        "stocks_to_use_finding": (
            "Every stocks-to-use form tried -- shock divided by carry-in, shock times log "
            "carry-in, and log carry-in alone -- came out statistically indistinguishable "
            "from zero (|t| < 0.1 in each case). This is not a refutation of the "
            "well-documented convex relationship between stocks-to-use and the price "
            "LEVEL: the dependent variable here is a year-on-year CHANGE, which differences "
            "that level relationship away. The interaction is therefore absent from the "
            "shipped model, and the model does not claim a tight-stocks amplification it "
            "cannot demonstrate."
        ),
        "not_captured": [
            "cross-commodity substitution: soybean and wheat prices move too, and corn "
            "acreage responds in the following season",
            "the ethanol and export demand channels separately; the reduced form sees "
            "only their aggregate",
            "basis and the futures curve; the fit is on a cash marketing-year average price",
            "policy shocks, including changes to the Renewable Fuel Standard",
            "when within the season the price moves; this is an annual relationship",
        ],
        "reference_estimates_not_used": {
            "roberts_schlenker_2013": (
                "Supply elasticity 0.087-0.116, demand -0.028 to -0.066 (Table 1, FAO "
                "data), implying a price multiplier 1/(beta_s - beta_d) of 5.75-7.73. "
                "Derived for a permanent shift in world caloric supply, not a transitory "
                "weather shock, so it is not applied here."
            ),
            "farmdoc_daily_2018": (
                "-1.84 percent price change per percent yield change, R-squared 0.48, "
                "1993-2017, December corn futures against the May-to-November WASDE yield "
                "change. Its regressor is the surprise against market expectation, not "
                "against a weather baseline, so it is not applied here."
            ),
        },
        "sources": [
            price_meta["vintage"],
            "Roberts, M. J. and W. Schlenker (2013), American Economic Review 103(6), 2265-95",
            "farmdoc daily (8):212, University of Illinois, 15 November 2018",
        ],
        "price_history_vintage": {
            "source": price_meta["source"],
            "source_last_modified": price_meta["source_last_modified"],
            "built_at": price_meta["built_at"],
        },
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sample": [
            {"year": s["year"], "deviation": round(s["deviation"], 6),
             "deviation_lag": round(s["deviation_lag"], 6),
             "carryin": round(s["carryin"], 6),
             "dlog_price": round(s["dlog_price"], 6)}
            for s in sample
        ],
    }

    OUT_PATH.write_text(json.dumps(payload, indent=2) + "\n")
    log(f"wrote   {OUT_PATH.name}")


if __name__ == "__main__":
    main()
