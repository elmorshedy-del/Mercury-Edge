"""Write a one-day replay fixture so the TypeScript feature engine can be checked against
build.py (tests/manh-nowcast.test.ts).

  python3 research/manh_knyc/export_parity.py --data DATA_DIR --day 2025-07-14 \
      --out tests/fixtures/manh-nowcast-day.json
"""
from __future__ import annotations

import argparse
import json
import pickle
from pathlib import Path

import numpy as np
import pandas as pd

from build import STATIONS, nbm_daily
from load import load_manh
from train import A_BASE, A_MESO, B_BASE, B_MESO


def clean(v):
    return None if v is None or (isinstance(v, float) and not np.isfinite(v)) else float(v)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, required=True)
    ap.add_argument("--day", required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    base = pickle.load(open(args.data / "base.pkl", "rb"))
    a = pd.read_pickle(args.data / "model_a.pkl")
    b = pd.read_pickle(args.data / "model_b.pkl")
    day = pd.Timestamp(args.day).date()
    d0 = pd.Timestamp(args.day, tz="UTC") + pd.Timedelta(hours=5)
    lo, hi = d0 - pd.Timedelta(hours=6), d0 + pd.Timedelta(hours=25)

    obs = []
    man = load_manh(args.data).loc[lo:hi]
    for t, row in man.iterrows():
        obs.append({"time": int(t.value // 10**6), "station": "MANH", "tair": clean(row["tair"]),
                    "srad": clean(row["srad"]), "relh": clean(row["relh"]), "wspd": clean(row["wspd"]),
                    "wdir": clean(row["wdir"]), "pres": clean(row["pres"]), "precip": clean(row["precip"])})
    from build import _load_station_temp
    for st, ds in STATIONS.items():
        if st == "MANH":
            continue
        s = _load_station_temp(args.data, st, ds).loc[lo:hi]
        obs.extend({"time": int(t.value // 10**6), "station": st, "tair": clean(v)} for t, v in s.items())

    rep = base.reports[(base.reports["valid"] >= lo) & (base.reports["valid"] <= hi)].dropna(subset=["temp"])
    reports = [{"valid": int(r.valid.value // 10**6), "temp": float(r.temp), "max6": clean(r.max6),
                "routine": bool(r.routine and r.has_tgroup), "raw": r.metar} for r in rep.itertuples()]
    nb = nbm_daily(base.nbm)
    nb = nb[nb["cdate"] == day]
    nbm = [{"runtime": int(r.runtime.value // 10**6), "cdate": str(day), "txn": float(r.txn), "xnd": clean(r.xnd)}
           for r in nb.itertuples()]

    arows = a[(a["t0"] >= d0) & (a["t0"] < d0 + pd.Timedelta(hours=24))]
    a_out = [{"t0": int(r["t0"].value // 10**6), "tau": int(r["tau"].value // 10**6),
              "features": {f: clean(r[f]) for f in A_BASE + A_MESO}} for _, r in arows.iterrows()]
    brows = b[b["cdate"] == day]
    b_out = [{"tau": int(r["tau"].value // 10**6), "features": {f: clean(r[f]) for f in B_BASE + B_MESO}}
             for _, r in brows.iterrows()]
    args.out.write_text(json.dumps({
        "day": args.day, "start": int(lo.value // 10**6), "end": int(hi.value // 10**6),
        "observations": obs, "reports": reports, "nbm": nbm, "next_metar": a_out, "day_rows": b_out,
    }))
    print(args.out, len(obs), len(reports), len(a_out), len(b_out), round(args.out.stat().st_size / 1e6, 2), "MB")


if __name__ == "__main__":
    main()
