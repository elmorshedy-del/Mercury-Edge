"""Does Kalshi's NYC weather index add to the current model (Mesonet + airports)?

  python3 research/manh_knyc/fetch_data.py --out $DATA --only airports --airports LGA,FRG,ISP,TEB,HPN,SMQ
  python3 research/manh_knyc/index_check.py --data $DATA

The index is rebuilt for 2021-26 from the eight contributors' archived 5-minute reports
(kalshi_index.py) and read INDEX_LATENCY_MIN minutes late, as it is served live. Scored on
2025-26 (never trained on) with day-block bootstrap intervals from train.ablation.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

from kalshi_index import feature_names, index_features, rebuild_index
from train import (A_BASE, A_HI, A_LO, A_NOIDX as A_MESO, B_BASE, B_NOIDX as B_MESO, DAILY_MIN_LEAF, F_HI, H_HI,
                   SPLIT, SPLIT_DATE, ablation, multiclass, prep_a, prep_final, prep_hidden)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, required=True)
    ap.add_argument("--latencies", default="10,5")
    ap.add_argument("--out", type=Path, default=Path(__file__).parent / "results" / "index_check.json")
    args = ap.parse_args()
    index = rebuild_index(args.data)
    print("index rebuilt:", index.notna().sum(), "of", len(index), "stamps", flush=True)
    results: dict = {}

    a, ya = prep_a(pd.read_pickle(args.data / "model_a.pkl"))
    tau = pd.DatetimeIndex(a["tau"])
    tr, te = (a["t0"] < SPLIT).to_numpy(), (a["t0"] >= SPLIT).to_numpy()
    n = A_HI - A_LO + 1
    days = a.loc[te, "t0"].dt.tz_convert("America/New_York").dt.date.to_numpy()
    base = lgb.train(multiclass(n), lgb.Dataset(a.loc[tr, A_BASE + A_MESO], ya[tr]), 150)
    P0 = base.predict(a.loc[te, A_BASE + A_MESO])
    for lat in (int(x) for x in args.latencies.split(",")):
        X = pd.concat([a[A_BASE + A_MESO], index_features(index, tau, a["y0"].to_numpy(), latency_min=lat)
                       .set_index(a.index)], axis=1)
        m = lgb.train(multiclass(n), lgb.Dataset(X[tr], ya[tr]), 150)
        res = ablation(m.predict(X[te]), P0, ya[te], days)
        results[f"next_metar_latency_{lat}"] = res
        print(f"next METAR + index ({lat} min late): logloss {res['logloss_without_airports']:.4f} -> "
              f"{res['logloss_with_airports']:.4f} (CI {res['change_ci95'][0]:+.4f}..{res['change_ci95'][1]:+.4f}) "
              f"acc {res['acc_without_airports']:.4f} -> {res['acc_with_airports']:.4f}", flush=True)

    b = pd.read_pickle(args.data / "model_b.pkl")
    lat = int(args.latencies.split(",")[0])
    for label, prep, n in (("hidden_max", prep_hidden, H_HI + 1), ("final_high", prep_final, F_HI + 1)):
        df, y = prep(b)
        key = pd.to_datetime(df["cdate"]).dt.date
        tr, te = (key < SPLIT_DATE).to_numpy(), (key >= SPLIT_DATE).to_numpy()
        feats = index_features(index, pd.DatetimeIndex(df["tau"]), df["known_max"].to_numpy(),
                               np.asarray(df["cdate"]), latency_min=lat).set_index(df.index)
        X = pd.concat([df[B_BASE + B_MESO], feats], axis=1)
        m0 = lgb.train(multiclass(n, DAILY_MIN_LEAF), lgb.Dataset(df.loc[tr, B_BASE + B_MESO], y[tr]), 50)
        m1 = lgb.train(multiclass(n, DAILY_MIN_LEAF), lgb.Dataset(X[tr], y[tr]), 50)
        res = ablation(m1.predict(X[te]), m0.predict(df.loc[te, B_BASE + B_MESO]), y[te], df.loc[te, "cdate"].to_numpy())
        results[label] = res
        print(f"{label} + index ({lat} min late): logloss {res['logloss_without_airports']:.4f} -> "
              f"{res['logloss_with_airports']:.4f} (CI {res['change_ci95'][0]:+.4f}..{res['change_ci95'][1]:+.4f}) "
              f"acc {res['acc_without_airports']:.4f} -> {res['acc_with_airports']:.4f}", flush=True)
    args.out.write_text(json.dumps(results, indent=1))


if __name__ == "__main__":
    main()
