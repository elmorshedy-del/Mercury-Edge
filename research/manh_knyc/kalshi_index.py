"""Kalshi's NYC weather index (api: /trade-api/v2/live_data/weather/nyc).

Reverse-engineered (Oct 2026) against its full history (Aug 28 - Oct 11 2026, every weekly
"cal" config version):

    index = 32 + 1.8 * mean_i( whole-degree-C 5-minute running temperature at airport i )

over eight ASOS airports: Caldwell, Newark, Teterboro, Somerset (NJ), LaGuardia, White Plains,
Farmingdale and Islip. It matched exactly at 9,375 of 9,377 five-minute stamps. Central Park,
JFK and Wall Street are NOT contributors. When a station is missing Kalshi reports
"contributors": 7, status "degraded", with a value that is not a plain mean of the other seven,
so only 8-contributor values are used (degraded minutes were common in the launch weeks,
Aug 29 - Sep 13, and are 0-4% of minutes per day since).

The 5-minute stamps coincide with the stations' MADIS HF reports, so the index can be rebuilt
for 2021-2026 from the IEM HF archive. Live, Kalshi serves it every minute about 5-6 minutes
after the stamp - much faster than the 12-30 minutes api.weather.gov needs for the same
airports - so features read the stamp INDEX_LATENCY_MIN minutes before the decision time.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from airports import load_reports
from load import climate_date

INDEX_STATIONS = ("CDW", "EWR", "TEB", "SMQ", "LGA", "HPN", "FRG", "ISP")
INDEX_LATENCY_MIN = 5  # Kalshi lags ~5.4 min; decisions run ~2 min after the Mesonet stamp
CONFIG_PREFIX = "nyc-temperature-v1.0"  # live values are trusted only under this formula version
MIN_CONTRIBUTORS = 8  # Kalshi's 7-contributor "degraded" values are not a plain 7-station mean


def rebuild_index(data: Path) -> pd.Series:
    """Index value (F) on a 5-minute UTC grid, NaN unless all eight stations report."""
    cols = {}
    for code in INDEX_STATIONS:
        hf = load_reports(data, "hf", code)
        hf = hf[hf.index.minute % 5 == 0]
        cols[code] = np.round((hf["temp"] - 32) * 5 / 9)
    X = pd.DataFrame(cols)
    grid = pd.date_range(X.index.min().floor("D"), X.index.max().ceil("D"), freq="5min")
    X = X.reindex(grid)
    n = X.notna().sum(axis=1)
    return (32 + 1.8 * X.mean(axis=1)).where(n >= MIN_CONTRIBUTORS)


def index_features(index: pd.Series, tau: pd.DatetimeIndex, ref: np.ndarray,
                   cdate: np.ndarray | None = None, latency_min: int = INDEX_LATENCY_MIN) -> pd.DataFrame:
    avail = (tau - pd.Timedelta(minutes=latency_min)).floor("5min")
    filled = index.ffill(limit=1)  # one late/missing stamp falls back to the previous one
    at = lambda t: filled.reindex(t).to_numpy()  # noqa: E731
    cur = at(avail)
    cols = {
        "kidx_lvl": cur - ref,
        "kidx_d15": cur - at(avail - pd.Timedelta(minutes=15)),
        "kidx_d30": cur - at(avail - pd.Timedelta(minutes=30)),
        "kidx_d60": cur - at(avail - pd.Timedelta(minutes=60)),
    }
    if cdate is not None:
        s = index.dropna()
        frame = pd.DataFrame({"t": s.to_numpy(), "avail": s.index + pd.Timedelta(minutes=latency_min),
                              "cdate": climate_date(s.index)})
        by_date = {d: g for d, g in frame.groupby("cdate")}
        out = np.full(len(tau), np.nan)
        rows = pd.DataFrame({"tau": tau, "cdate": cdate, "i": np.arange(len(tau))})
        for d, g in rows.groupby("cdate"):
            sd = by_date.get(d)
            if sd is None:
                continue
            cm = sd["t"].cummax().to_numpy()
            pos = np.searchsorted(sd["avail"].values, g["tau"].values, side="right") - 1
            ok = pos >= 0
            out[g["i"].to_numpy()[ok]] = cm[pos[ok]]
        cols["kidx_daymax_lvl"] = out - ref
    return pd.DataFrame(cols)


def feature_names(daily: bool) -> list[str]:
    return ["kidx_lvl", "kidx_d15", "kidx_d30", "kidx_d60"] + (["kidx_daymax_lvl"] if daily else [])
