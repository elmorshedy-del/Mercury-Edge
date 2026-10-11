"""Do Newark (KEWR), Caldwell (KCDW) and Wall Street (KJRB) add to the Mesonet nowcast?

  python3 research/manh_knyc/fetch_data.py --out $DATA --only airports --airports EWR,CDW,JRB
  python3 research/manh_knyc/airports_check.py --data $DATA

Feeds are used exactly as live (see airports.py). Variants are scored out-of-sample
(train < 2025, test 2025-26) against the Mesonet-only model, with the 5-minute feeds treated as
--latency minutes late and, for an upper bound, as instantaneous.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.metrics import log_loss, roc_auc_score

from airports import AIRPORTS, Airport, airport_features
from train import (A_BASE, A_HI, A_LO, A_MESONET, B_BASE, B_MESONET, DAILY_MIN_LEAF, F_HI, H_HI, SPLIT,
                   SPLIT_DATE, multiclass, prep_a, prep_final, prep_hidden)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, required=True)
    ap.add_argument("--latency", type=int, default=25, help="minutes the 5-minute feeds are treated as late")
    args = ap.parse_args()
    live = {c: Airport(args.data, c, args.latency) for c in AIRPORTS}
    instant = {c: Airport(args.data, c, 0) for c in AIRPORTS}

    # ---- next METAR ----
    a, ya = prep_a(pd.read_pickle(args.data / "model_a.pkl"))
    tau = pd.DatetimeIndex(a["tau"])
    tr, te = (a["t0"] < SPLIT).to_numpy(), (a["t0"] >= SPLIT).to_numpy()
    n = A_HI - A_LO + 1
    lead = a["lead_min"].to_numpy()[te]
    base = a[A_BASE + A_MESONET]
    variants = [("Mesonet only", base)]
    real = airport_features(live, tau, a["y0"].to_numpy())
    zero = airport_features(instant, tau, a["y0"].to_numpy())
    variants.append(("+ EWR/CDW/JRB as published live", pd.concat([base, real], axis=1)))
    variants.append(("+ EWR/CDW/JRB with zero delay", pd.concat([base, zero], axis=1)))
    for code in AIRPORTS:
        variants.append((f"+ {code} only (live)", pd.concat([base, real[[c for c in real if c.startswith(code)]]], axis=1)))
    for label, X in variants:
        m = lgb.train(multiclass(n), lgb.Dataset(X[tr], ya[tr]), 150)
        P = m.predict(X[te])
        yt = ya[te]
        late = lead <= 11
        print(f"next METAR {label:34s} logloss={log_loss(yt, P, labels=range(n)):.4f} acc={(P.argmax(1) == yt).mean():.4f}"
              f" | last 15 min acc={(P[late].argmax(1) == yt[late]).mean():.4f}", flush=True)

    # ---- daily models ----
    b = pd.read_pickle(args.data / "model_b.pkl")
    for label, prep, n in (("hidden", prep_hidden, H_HI + 1), ("final", prep_final, F_HI + 1)):
        df, y = prep(b)
        tau = pd.DatetimeIndex(df["tau"])
        cdate = np.asarray(df["cdate"])
        key = pd.to_datetime(df["cdate"]).dt.date
        tr, te = (key < SPLIT_DATE).to_numpy(), (key >= SPLIT_DATE).to_numpy()
        hrs = df["hour_local"].astype(int).to_numpy()[te]
        known = df["known_max"].to_numpy()
        for name, extra in (("Mesonet only", None),
                            ("+ airports as published live", airport_features(live, tau, known, cdate)),
                            ("+ airports with zero delay", airport_features(instant, tau, known, cdate))):
            X = df[B_BASE + B_MESONET] if extra is None else pd.concat([df[B_BASE + B_MESONET], extra.set_index(df.index)], axis=1)
            m = lgb.train(multiclass(n, DAILY_MIN_LEAF), lgb.Dataset(X[tr], y[tr]), 50)
            P = m.predict(X[te])
            yt = y[te]
            pt = P[np.arange(len(yt)), yt]
            ll = -np.log(np.clip(pt, 1e-9, 1))
            pm = (hrs >= 13) & (hrs <= 18)
            auc = f" AUC(>=1)={roc_auc_score(yt >= 1, 1 - P[:, 0]):.4f}" if label == "hidden" else ""
            print(f"{label:6s} {name:30s} logloss={ll.mean():.4f} acc={(P.argmax(1) == yt).mean():.4f}"
                  f" | 1-6pm logloss={ll[pm].mean():.4f} acc={(P[pm].argmax(1) == yt[pm]).mean():.4f}{auc}", flush=True)


if __name__ == "__main__":
    main()
