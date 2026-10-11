"""Airport neighbours west/south of Central Park: Newark (KEWR), Caldwell (KCDW), Wall Street (KJRB).

Each airport is used exactly as it can be used live:
  * 5-minute MADIS/ASOS HF reports (whole degrees C + wind). api.weather.gov serves the same
    values as the IEM report_type=1 archive (verified reading-for-reading) but 12-30 minutes
    late, so a reading stamped s is treated as visible at s + LATENCY_MIN. If that exact stamp is
    missing, the newest stamp up to 15 minutes older is used (pandas ffill(limit=3)).
  * Routine/special METARs (EWR :51, CDW :53, JRB :56) with T-group precision, visible
    METAR_LAG_MIN minutes after the observation time.
lib/weather/manh-nowcast.ts implements the same rules; tests/manh-nowcast.test.ts checks parity.
"""
from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pandas as pd

from load import climate_date

AIRPORTS = ("EWR", "CDW", "JRB")
LATENCY_MIN = 25
METAR_LAG_MIN = 3
METAR_MAX_AGE_MIN = (90, 180)  # last / previous METAR must be this fresh (stations go down for days)
TEMP_RANGE_F = (-40.0, 120.0)  # drops the M61/M61 (-61 C) missing-data sentinel
TGROUP = re.compile(r"\sT([01])(\d{3})([01])(\d{3})")


def tgroup_f(metar: str, whole_f: bool) -> float:
    m = TGROUP.search(metar) if isinstance(metar, str) else None
    if not m:
        return np.nan
    c = int(m.group(2)) / 10 * (-1 if m.group(1) == "1" else 1)
    f = c * 9 / 5 + 32
    return float(round(f)) if whole_f else f


def load_reports(data: Path, kind: str, station: str) -> pd.DataFrame:
    frames = [pd.read_csv(p, na_values=["M"]) for p in sorted((data / f"iem_{kind}_{station}").glob("*.csv"))]
    df = pd.concat([f for f in frames if len(f)], ignore_index=True)
    df["valid"] = pd.to_datetime(df["valid"], utc=True)
    df = df.drop_duplicates("valid").sort_values("valid").set_index("valid")
    # HF reports carry whole degrees C in the T-group; METARs carry an ASOS whole-F value.
    df["temp"] = df["metar"].map(lambda m: tgroup_f(m, whole_f=(kind == "metar")))
    df.loc[(df["temp"] < TEMP_RANGE_F[0]) | (df["temp"] > TEMP_RANGE_F[1]), "temp"] = np.nan
    rad = np.deg2rad(df["drct"])
    df["u"] = -df["sknt"] * np.sin(rad)
    df["v"] = -df["sknt"] * np.cos(rad)
    return df


class Airport:
    def __init__(self, data: Path, code: str, latency: int = LATENCY_MIN):
        self.code, self.latency = code, pd.Timedelta(minutes=latency)
        hf = load_reports(data, "hf", code)
        hf = hf[hf.index.minute % 5 == 0]
        grid = pd.date_range(hf.index.min().floor("D"), hf.index.max().ceil("D"), freq="5min")
        hf = hf.reindex(grid)
        self.temp = hf["temp"].ffill(limit=3)
        self.raw_temp = hf["temp"]
        self.wind_u = hf["u"].rolling(3, min_periods=1).mean()
        self.wind_v = hf["v"].rolling(3, min_periods=1).mean()
        met = load_reports(data, "metar", code).dropna(subset=["temp"])
        self.metar = met["temp"]
        self.metar_avail = met.index + pd.Timedelta(minutes=METAR_LAG_MIN)

    def at(self, series: pd.Series, times: pd.DatetimeIndex) -> np.ndarray:
        return series.reindex(times).to_numpy()

    def last_metar(self, tau: pd.DatetimeIndex, back: int = 0) -> np.ndarray:
        pos = np.searchsorted(self.metar_avail.values, tau.values, side="right") - 1 - back
        out = np.full(len(tau), np.nan)
        ok = pos >= 0
        out[ok] = self.metar.to_numpy()[pos[ok]]
        valid = np.full(len(tau), np.datetime64("NaT"), dtype="datetime64[ns]")
        valid[ok] = self.metar.index.values[pos[ok]]
        age = (tau.values - valid) / np.timedelta64(1, "m")
        out[~(age <= METAR_MAX_AGE_MIN[back])] = np.nan
        return out

    def day_max(self, tau: pd.DatetimeIndex, cdate: np.ndarray) -> np.ndarray:
        """Highest HF/METAR temperature of the climate day visible by tau."""
        hf = self.raw_temp.dropna()
        s = pd.concat([
            pd.DataFrame({"t": hf.to_numpy(), "avail": hf.index + self.latency}, index=hf.index),
            pd.DataFrame({"t": self.metar.to_numpy(), "avail": self.metar_avail}, index=self.metar.index),
        ])
        s["cdate"] = climate_date(s.index)
        s = s.sort_values("avail")
        out = np.full(len(tau), np.nan)
        frame = pd.DataFrame({"tau": tau, "cdate": cdate, "i": np.arange(len(tau))})
        by_date = {d: g for d, g in s.groupby("cdate")}
        for d, g in frame.groupby("cdate"):
            sd = by_date.get(d)
            if sd is None:
                continue
            cm = sd["t"].cummax().to_numpy()
            pos = np.searchsorted(sd["avail"].values, g["tau"].values, side="right") - 1
            ok = pos >= 0
            out[g["i"].to_numpy()[ok]] = cm[pos[ok]]
        return out


def load_airports(data: Path) -> dict[str, Airport]:
    return {code: Airport(data, code) for code in AIRPORTS}


def airport_features(airports: dict[str, Airport], tau: pd.DatetimeIndex, ref: np.ndarray,
                     cdate: np.ndarray | None = None) -> pd.DataFrame:
    """ref = last KNYC METAR (next-METAR model) or published max (daily models)."""
    cols: dict[str, np.ndarray] = {}
    for code, ap in airports.items():
        avail = (tau - ap.latency).floor("5min")
        cur = ap.at(ap.temp, avail)
        cols[f"{code}_hf_lvl"] = cur - ref
        cols[f"{code}_hf_d30"] = cur - ap.at(ap.temp, avail - pd.Timedelta(minutes=30))
        cols[f"{code}_hf_d60"] = cur - ap.at(ap.temp, avail - pd.Timedelta(minutes=60))
        cols[f"{code}_u"] = ap.at(ap.wind_u, avail)
        cols[f"{code}_v"] = ap.at(ap.wind_v, avail)
        last = ap.last_metar(tau)
        cols[f"{code}_met_lvl"] = last - ref
        cols[f"{code}_met_d60"] = last - ap.last_metar(tau, back=1)
        if cdate is not None:
            cols[f"{code}_daymax_lvl"] = ap.day_max(tau, cdate) - ref
    df = pd.DataFrame(cols)
    df["air_hf_lvl"] = df[[f"{c}_hf_lvl" for c in airports]].mean(axis=1)
    df["air_hf_d30"] = df[[f"{c}_hf_d30" for c in airports]].mean(axis=1)
    return df


def feature_names(daily: bool) -> list[str]:
    names = []
    for code in AIRPORTS:
        names += [f"{code}_hf_lvl", f"{code}_hf_d30", f"{code}_hf_d60", f"{code}_u", f"{code}_v",
                  f"{code}_met_lvl", f"{code}_met_d60"]
        if daily:
            names.append(f"{code}_daymax_lvl")
    return names + ["air_hf_lvl", "air_hf_d30"]
