"""Does KLGA (LaGuardia, ~8 km ENE of Central Park) add anything to the Mesonet nowcast?

  python3 research/manh_knyc/fetch_data.py --out $DATA --only lga
  python3 research/manh_knyc/lga_check.py --data $DATA

1. Lead/lag of 15-minute temperature changes, LGA vs KNYC, split by LGA wind regime.
2. Next-METAR model with LGA features added, using LGA's live 5-minute feed as it is actually
   published: whole-degree Celsius (verified 143/143 against archived MADISHF rows) and
   ~20-30 minutes late on api.weather.gov / IEM.
3. Hidden-max and final-high models with LGA wind/temperature features (25 minutes late).

Result (Oct 2026): no meaningful gain - see docs/MANH_KNYC_CALIBRATION.md.
"""
from __future__ import annotations

import argparse
import pickle
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.metrics import log_loss, roc_auc_score

from train import (A_BASE, A_HI, A_LO, A_MESO, B_BASE, B_MESO, DAILY_MIN_LEAF, F_HI, H_HI, SPLIT,
                   SPLIT_DATE, multiclass, prep_a, prep_final, prep_hidden)


def load_lga(data: Path):
    frames = [pd.read_csv(p, na_values=["M"]) for p in sorted((data / "iem_1min_LGA").glob("*.csv"))]
    L = pd.concat([f for f in frames if len(f)], ignore_index=True)
    L["valid"] = pd.to_datetime(L["valid(UTC)"], utc=True)
    L = L.drop_duplicates("valid").set_index("valid").sort_index()
    idx = pd.date_range(L.index.min().floor("D"), L.index.max().ceil("D"), freq="1min")
    L = L.reindex(idx)
    L5 = L["tmpf"].rolling(5, min_periods=4).mean()
    hf = np.round((np.round(L5) - 32) * 5 / 9) * 9 / 5 + 32      # live feed: whole degrees C
    u = -L["sknt"] * np.sin(np.deg2rad(L["drct"]))
    v = -L["sknt"] * np.cos(np.deg2rad(L["drct"]))
    return L5, hf, u, v


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, required=True)
    args = ap.parse_args()
    base = pickle.load(open(args.data / "base.pkl", "rb"))
    L5, hf, u, v = load_lga(args.data)
    g = pd.date_range(L5.index[0], L5.index[-1], freq="5min")

    # 1. lead/lag by regime (daytime 9am-7pm local)
    U30, V30 = u.rolling(30, min_periods=20).mean().reindex(g), v.rolling(30, min_periods=20).mean().reindex(g)
    spd = np.hypot(U30, V30)
    dirn = (np.rad2deg(np.arctan2(-U30, -V30)) + 360) % 360
    regime = pd.Series("calm", index=g)
    regime[(spd >= 4) & (dirn >= 20) & (dirn < 150)] = "NE-E-SE"
    regime[(spd >= 4) & (dirn >= 150) & (dirn < 240)] = "S-SW"
    regime[(spd >= 4) & ((dirn >= 240) | (dirn < 20))] = "W-NW-N"
    k = base.kmin["t5_raw"].reindex(g)
    lga = L5.reindex(g)
    dk = k.shift(-3) - k
    hr = g.tz_convert("America/New_York").hour
    for name in ("NE-E-SE", "S-SW", "W-NW-N"):
        m = (regime == name).to_numpy() & (hr >= 9) & (hr <= 19)
        cells = []
        for lag in (-6, -3, -1, 0, 1, 3, 6):
            dl = lga.shift(-lag) - lga.shift(-lag + 3)
            cells.append(f"{lag * 5:+d}m:{pd.concat([dk, dl], axis=1)[m].dropna().corr().iloc[0, 1]:.2f}")
        print(f"{name:8s} corr(KNYC next 15m, LGA 15m change ending t+lag): {' '.join(cells)}")

    # 2. next METAR
    a, ya = prep_a(pd.read_pickle(args.data / "model_a.pkl"))
    tau, t0 = pd.DatetimeIndex(a["tau"]), pd.DatetimeIndex(a["t0"])
    hf5, U10, V10 = hf.reindex(g), u.rolling(10, min_periods=5).mean().reindex(g), v.rolling(10, min_periods=5).mean().reindex(g)

    def lga_feats(latency: int) -> pd.DataFrame:
        avail = (tau - pd.Timedelta(minutes=latency)).floor("5min")
        anchor = t0 - pd.Timedelta(minutes=1)
        cur, anc = hf5.reindex(avail).to_numpy(), hf5.reindex(anchor).to_numpy()
        return pd.DataFrame({
            "lga_d": np.where(avail >= anchor, cur - anc, np.nan),
            "lga_d15": cur - hf5.reindex(avail - pd.Timedelta(minutes=15)).to_numpy(),
            "lga_off": anc - a["y0"].to_numpy(),
            "lga_u": U10.reindex(avail).to_numpy(), "lga_v": V10.reindex(avail).to_numpy(),
        }, index=a.index)

    tr, te = (a["t0"] < SPLIT).to_numpy(), (a["t0"] >= SPLIT).to_numpy()
    n = A_HI - A_LO + 1
    for label, X in [("Mesonet only", a[A_BASE + A_MESO])] + [
            (f"+ LGA live feed, {lat} min late", pd.concat([a[A_BASE + A_MESO], lga_feats(lat)], axis=1)) for lat in (0, 20, 30)]:
        m = lgb.train(multiclass(n), lgb.Dataset(X[tr], ya[tr]), 150)
        P = m.predict(X[te])
        print(f"next METAR  {label:30s} logloss={log_loss(ya[te], P, labels=range(n)):.4f} acc={(P.argmax(1) == ya[te]).mean():.4f}")

    # 3. daily models, LGA 25 minutes late
    b = pd.read_pickle(args.data / "model_b.pkl")
    U30f, V30f = u.rolling(30, min_periods=15).mean().reindex(g), v.rolling(30, min_periods=15).mean().reindex(g)
    extra = ["lga_u", "lga_v", "lga_d60", "lga_minus_known"]
    for label, prep, n in (("final", prep_final, F_HI + 1), ("hidden", prep_hidden, H_HI + 1)):
        df, y = prep(b)
        avail = (pd.DatetimeIndex(df["tau"]) - pd.Timedelta(minutes=25)).floor("5min")
        df = df.assign(lga_u=U30f.reindex(avail).to_numpy(), lga_v=V30f.reindex(avail).to_numpy(),
                       lga_d60=hf5.reindex(avail).to_numpy() - hf5.reindex(avail - pd.Timedelta(minutes=60)).to_numpy(),
                       lga_minus_known=hf5.reindex(avail).to_numpy() - df["known_max"].to_numpy())
        key = pd.to_datetime(df["cdate"]).dt.date
        tr, te = (key < SPLIT_DATE).to_numpy(), (key >= SPLIT_DATE).to_numpy()
        for name, feats in (("Mesonet only", B_BASE + B_MESO), ("+ LGA (25 min late)", B_BASE + B_MESO + extra)):
            m = lgb.train(multiclass(n, DAILY_MIN_LEAF), lgb.Dataset(df.loc[tr, feats], y[tr]), 50)
            P = m.predict(df.loc[te, feats])
            yt = y[te]
            ll = -np.log(np.clip(P[np.arange(len(yt)), yt], 1e-9, 1)).mean()
            auc = f" AUC(>=1)={roc_auc_score(yt >= 1, 1 - P[:, 0]):.4f}" if label == "hidden" else ""
            print(f"{label:6s} {name:22s} logloss={ll:.4f} acc={(P.argmax(1) == yt).mean():.4f}{auc}")


if __name__ == "__main__":
    main()
