"""Descriptive findings for docs/MANH_KNYC_CALIBRATION.md.

  python3 research/manh_knyc/analysis.py --data DATA_DIR

Writes research/manh_knyc/results/descriptive.json.
"""
from __future__ import annotations

import argparse
import json
import pickle
from pathlib import Path

import numpy as np
import pandas as pd

from build import STATIONS
from load import climate_date


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=Path(__file__).parent / "results" / "descriptive.json")
    args = ap.parse_args()
    base = pickle.load(open(args.data / "base.pkl", "rb"))
    out: dict = {}

    # 1. What the hourly METAR measures: 5-minute mean of 1-minute temps ending at :51.
    one = base.kmin["t5"]
    r = base.routine
    t5_at = one.reindex(r.index)
    ok = t5_at.notna()
    out["metar_equals_rounded_5min_mean_ending_51"] = float((t5_at[ok] == r.loc[ok, "temp"]).mean())

    # 2. CLI high vs reconstructed 5-minute max and vs max hourly METAR.
    t5 = base.kmin["t5"]
    g = pd.DataFrame({"t5": t5.values, "cd": climate_date(t5.index), "ok": t5.notna().values})
    agg = g.groupby("cd").agg(max5=("t5", "max"), cover=("ok", "mean"))
    rr = r.copy()
    rr["cd"] = climate_date(rr.index)
    mx = rr.groupby("cd")["temp"].max().rename("max_metar")
    j = base.cli.join(agg, how="inner").join(mx, how="inner")
    full = j[j["cover"] > 0.995]
    out["cli_equals_max_5min_mean_full_coverage_days"] = {"days": int(len(full)), "share": float((full["high"] == full["max5"]).mean())}
    gap = (j["high"] - j["max_metar"]).clip(-1, 4)
    out["cli_minus_max_hourly_metar"] = {
        "days": int(len(j)),
        "share_by_gap": {str(int(k)): float(v) for k, v in gap.value_counts(normalize=True).sort_index().items()},
        "share_ge1_by_month": {int(m): float(v) for m, v in (j["high"] - j["max_metar"] >= 1).groupby(pd.to_datetime(j.index).month).mean().items()},
    }

    # 3. MANH - KNYC bias by local hour and month (KNYC 5-minute mean, 2-minute alignment).
    K = base.kmin["t5_raw"].shift(2).reindex(base.temps.index)
    diff = (base.temps["MANH"] - K).dropna()
    loc = diff.index.tz_convert("America/New_York")
    out["manh_minus_knyc_by_hour"] = {int(h): [round(float(m), 2), round(float(s), 2)] for h, (m, s) in diff.groupby(loc.hour).agg(["mean", "std"]).iterrows()}
    out["manh_minus_knyc_by_month"] = {int(h): [round(float(m), 2), round(float(s), 2)] for h, (m, s) in diff.groupby(loc.month).agg(["mean", "std"]).iterrows()}

    # 4. How well station changes track KNYC changes at different horizons.
    w = np.array([base.weights[s] for s in STATIONS])
    X = base.temps[list(STATIONS)]
    horizons = {}
    for steps in (1, 3, 6, 12, 24):
        dk = K.diff(steps)
        dm = X["MANH"].diff(steps)
        dz = pd.Series((X.diff(steps).to_numpy() * w).sum(1), index=X.index).where(X.diff(steps).notna().all(1))
        d = pd.DataFrame({"k": dk, "m": dm, "z": dz}).dropna()
        horizons[f"{steps * 5}min"] = {
            "corr_manh": round(float(d["k"].corr(d["m"])), 3),
            "corr_blend": round(float(d["k"].corr(d["z"])), 3),
            "slope_manh": round(float(np.polyfit(d["m"], d["k"], 1)[0]), 3),
        }
    out["change_correlation_by_horizon"] = horizons
    d = pd.DataFrame({"k": K.diff(12), "m": X["MANH"].diff(12)}).dropna()
    mo = d.index.tz_convert("America/New_York").month
    out["hourly_change_corr_manh_by_month"] = {int(m): round(float(gr["k"].corr(gr["m"])), 3) for m, gr in d.groupby(mo)}
    out["blend_weights"] = base.weights
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, indent=1))
    print(json.dumps(out, indent=1)[:4000])


if __name__ == "__main__":
    main()
