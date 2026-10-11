"""Load the cached MANH / KNYC files written by fetch_data.py into tidy pandas frames.

All timestamps are UTC (tz-aware). Temperatures are degrees Fahrenheit.

KNYC temperature semantics used throughout:
  ASOS forms a 5-minute average of 1-minute averages, rounds it to a whole degree F,
  then converts to tenths of a degree C for the METAR T-group. Converting the T-group
  back to F and rounding therefore recovers the exact whole-degree F value that the
  ASOS used. The Daily Climate Report (CLI) high is the highest of those whole-F values.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np
import pandas as pd

TGROUP = re.compile(r"\sT([01])(\d{3})([01])(\d{3})")
SIX_HR_MAX = re.compile(r"\s1([01])(\d{3})(?=\s|$)")
SIX_HR_MIN = re.compile(r"\s2([01])(\d{3})(?=\s|$)")
DAY_MAXMIN = re.compile(r"\s4([01])(\d{3})([01])(\d{3})(?=\s|$)")


def c_to_f(c):
    return np.asarray(c, dtype=float) * 9.0 / 5.0 + 32.0


def whole_f_from_tenths_c(sign: str, digits: str) -> int:
    c = int(digits) / 10.0 * (-1 if sign == "1" else 1)
    # ASOS stored a whole-F value; round-half-away is irrelevant because the
    # tenths-C encoding of an integer F never lands exactly on .5 F.
    return int(round(c * 9 / 5 + 32))


def load_manh(data_dir: Path, station: str = "MANH") -> pd.DataFrame:
    frames = []
    for path in sorted((data_dir / "nysm" / station).glob("*.json")):
        resp = json.loads(path.read_text())["response"]
        times = pd.to_datetime(resp["coords"]["time"]["data"], format="%Y%m%dT%H%M", utc=True)
        cols = {}
        for name, var in resp["data_vars"].items():
            scale = var.get("attrs", {}).get("scale_factor", 1.0)
            arr = np.array(var["data"][0], dtype=float)  # None -> nan
            cols[name] = arr * scale
        frames.append(pd.DataFrame(cols, index=times))
    df = pd.concat(frames).sort_index()
    df = df[~df.index.duplicated(keep="last")]
    out = pd.DataFrame(index=df.index)
    out["tair"] = c_to_f(df["tair"])
    if "tslo" in df:
        out["tslo"] = c_to_f(df["tslo"])
    out["relh"] = df.get("relh")
    out["srad"] = df.get("srad")
    out["pres"] = df.get("pres")
    out["wspd"] = df.get("wspd_sonic")
    out["wdir"] = df.get("wdir_sonic")
    out["precip"] = df.get("precip")
    # Basic plausibility screen; NYSM already QCs, but drop obvious sensor spikes.
    bad = (out["tair"] < -40) | (out["tair"] > 120)
    out.loc[bad, "tair"] = np.nan
    return out


def load_metar(data_dir: Path) -> pd.DataFrame:
    frames = [pd.read_csv(p, na_values=["M"]) for p in sorted((data_dir / "iem_metar").glob("*.csv"))]
    df = pd.concat(frames, ignore_index=True)
    df["valid"] = pd.to_datetime(df["valid"], utc=True)
    df = df.drop_duplicates(subset=["valid", "metar"]).sort_values("valid").reset_index(drop=True)

    def parse(metar: str):
        if not isinstance(metar, str):
            return (np.nan, np.nan, np.nan, np.nan, np.nan)
        t = TGROUP.search(metar)
        temp = whole_f_from_tenths_c(t.group(1), t.group(2)) if t else np.nan
        tx = SIX_HR_MAX.search(metar.split(" RMK ")[-1]) if " RMK " in metar else None
        tn = SIX_HR_MIN.search(metar.split(" RMK ")[-1]) if " RMK " in metar else None
        d24 = DAY_MAXMIN.search(metar)
        return (
            temp,
            whole_f_from_tenths_c(tx.group(1), tx.group(2)) if tx else np.nan,
            whole_f_from_tenths_c(tn.group(1), tn.group(2)) if tn else np.nan,
            whole_f_from_tenths_c(d24.group(1), d24.group(2)) if d24 else np.nan,
            whole_f_from_tenths_c(d24.group(3), d24.group(4)) if d24 else np.nan,
        )

    parsed = pd.DataFrame(df["metar"].map(parse).tolist(),
                          columns=["temp", "max6", "min6", "max24", "min24"])
    out = pd.concat([df, parsed], axis=1)
    # Fall back to the body temperature only when the T-group is absent.
    out["has_tgroup"] = out["temp"].notna()
    out.loc[out["temp"].isna(), "temp"] = out.loc[out["temp"].isna(), "tmpf"].round()
    out["routine"] = out["valid"].dt.minute == 51
    out["is_speci"] = out["metar"].astype(str).str.contains("SPECI") | ~out["routine"]
    return out


def load_1min(data_dir: Path) -> pd.Series:
    frames = []
    for p in sorted((data_dir / "iem_1min").glob("*.csv")):
        f = pd.read_csv(p, na_values=["M"])
        if len(f) == 0:
            continue
        frames.append(f)
    df = pd.concat(frames, ignore_index=True)
    df["valid"] = pd.to_datetime(df["valid(UTC)"], utc=True)
    s = df.set_index("valid")["tmpf"].astype(float).sort_index()
    s = s[~s.index.duplicated(keep="last")]
    return s


def load_cli(data_dir: Path) -> pd.DataFrame:
    rows = []
    for p in sorted((data_dir / "iem_cli").glob("*.json")):
        rows.extend(json.loads(p.read_text())["results"])
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["valid"]).dt.date
    df["high"] = pd.to_numeric(df["high"], errors="coerce")
    df = df.dropna(subset=["high"]).drop_duplicates("date", keep="last")
    return df.set_index("date")[["high", "high_time", "low", "product"]]


def load_nbm(data_dir: Path) -> pd.DataFrame:
    frames = [pd.read_csv(p) for p in sorted((data_dir / "iem_nbm").glob("*.csv"))]
    df = pd.concat(frames, ignore_index=True)
    df["runtime"] = pd.to_datetime(df["runtime"], utc=True, format="ISO8601")
    df["ftime"] = pd.to_datetime(df["ftime"], utc=True, format="ISO8601")
    return df.drop_duplicates(["runtime", "ftime"]).sort_values(["runtime", "ftime"]).reset_index(drop=True)


def climate_date(ts_utc: pd.Series | pd.DatetimeIndex):
    """NWS climate day for KNYC: midnight-to-midnight Local *Standard* Time (UTC-5 all year)."""
    return (pd.DatetimeIndex(ts_utc) - pd.Timedelta(hours=5)).date
