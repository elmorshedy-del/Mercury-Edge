"""Feature builders for the MANH -> KNYC calibration study.

Model A ("next METAR"): at a decision time tau between two routine KNYC METARs, predict the
whole-degree F temperature of the next routine METAR from the last METAR plus the intra-hour
path of MANH and five neighbouring NYS Mesonet / NYC Micronet stations.

Model B ("hidden max" and "final high"): at any 5-minute decision time in the NWS climate
day, predict how far the true running maximum (and the final CLI high) sits above the
highest KNYC value the public has seen so far.

Only information available at tau is used:
  * KNYC reports are usable PUBLISH_LAG minutes after their valid time.
  * Mesonet 5-minute values are stored by grid time; the live tool only uses a grid time
    after it has actually arrived (~1m50s latency), so the grid time is the decision time.
  * NBM text guidance is usable NBM_LAG minutes after its run time.

The composite ("blend") is a fixed weighted sum of station temperatures whose weights are fit
on pre-2025 data to explain 60-minute KNYC changes. Every blend quantity is a *difference*
between two times, re-normalised over the stations reporting at both times, so a station
dropping out never creates a jump.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from airports import Airport, airport_features, load_airports
from load import c_to_f, climate_date, load_1min, load_cli, load_manh, load_metar, load_nbm

LAT, LON = 40.7789, -73.9692  # KNYC / Central Park
PUBLISH_LAG = pd.Timedelta(minutes=3)
NBM_LAG = pd.Timedelta(minutes=75)
STATIONS = {"MANH": "nysm", "BRON": "nysm", "QUEE": "nysm", "BKLN": "nysm", "MHMHIL": "nyc", "MHCHEL": "nyc"}
NEIGHBORS = [s for s in STATIONS if s != "MANH"]
BETAS = (0.5, 1.0)


def solar_elevation(ts: pd.DatetimeIndex) -> np.ndarray:
    """NOAA low-precision solar elevation (degrees) for Central Park."""
    ts = pd.DatetimeIndex(ts)
    jd = ts.to_julian_date().values
    n = jd - 2451545.0
    L = np.deg2rad((280.460 + 0.9856474 * n) % 360)
    g = np.deg2rad((357.528 + 0.9856003 * n) % 360)
    lam = L + np.deg2rad(1.915) * np.sin(g) + np.deg2rad(0.020) * np.sin(2 * g)
    eps = np.deg2rad(23.439 - 0.0000004 * n)
    dec = np.arcsin(np.sin(eps) * np.sin(lam))
    ra = np.arctan2(np.cos(eps) * np.sin(lam), np.cos(lam))
    gmst = (18.697374558 + 24.06570982441908 * n) % 24
    ha = np.deg2rad(((gmst * 15 + LON) - np.rad2deg(ra) + 540) % 360 - 180)
    lat = np.deg2rad(LAT)
    el = np.arcsin(np.sin(lat) * np.sin(dec) + np.cos(lat) * np.cos(dec) * np.cos(ha))
    return np.rad2deg(el)


def clear_sky_ghi(elev_deg: np.ndarray) -> np.ndarray:
    s = np.sin(np.deg2rad(np.clip(elev_deg, 0, 90)))
    with np.errstate(divide="ignore", invalid="ignore"):
        ghi = 1098.0 * s * np.exp(-0.057 / np.where(s > 0, s, np.nan))
    return np.nan_to_num(ghi, nan=0.0)


@dataclass
class Base:
    man: pd.DataFrame          # MANH on a complete 5-minute UTC grid (all variables)
    temps: pd.DataFrame        # air temperature (F) of every station on the same grid
    kmin: pd.DataFrame         # KNYC minute grid: t5_raw (5-min mean), t5 (whole F)
    routine: pd.DataFrame      # routine METARs indexed by valid time
    reports: pd.DataFrame      # all METAR + SPECI rows
    cli: pd.DataFrame
    nbm: pd.DataFrame
    weights: dict[str, float] | None = None
    airports: dict[str, Airport] | None = None


def _load_station_temp(data_dir: Path, station: str, dataset: str) -> pd.Series:
    frames = []
    for path in sorted((data_dir / dataset / station).glob("*.json")):
        resp = json.loads(path.read_text())["response"]
        t = pd.to_datetime(resp["coords"]["time"]["data"], format="%Y%m%dT%H%M", utc=True)
        vals = np.array(resp["data_vars"]["tair"]["data"][0], dtype=float) * 0.1
        frames.append(pd.Series(c_to_f(vals), index=t))
    s = pd.concat(frames).sort_index()
    return s[~s.index.duplicated(keep="last")]


def load_base(data_dir: Path) -> Base:
    man = load_manh(data_dir)
    grid = pd.date_range(man.index.min(), man.index.max(), freq="5min")
    man = man.reindex(grid)
    for col in ("tair", "relh", "srad", "pres", "wspd"):
        man[col] = man[col].interpolate(limit=2, limit_area="inside")
    # NYSM precip is an accumulation that resets; convert to 5-minute increments (mm).
    man["precip_inc"] = man["precip"].diff().clip(lower=0).where(lambda x: x < 50)
    temps = pd.DataFrame(index=grid)
    temps["MANH"] = man["tair"]
    for st, ds in STATIONS.items():
        if st == "MANH":
            continue
        temps[st] = _load_station_temp(data_dir, st, ds).reindex(grid).interpolate(limit=2, limit_area="inside")
    one = load_1min(data_dir)
    mgrid = pd.date_range(one.index.min().floor("D"), one.index.max().ceil("D"), freq="1min")
    o = one.reindex(mgrid)
    t5_raw = o.rolling(5, min_periods=5).mean()
    kmin = pd.DataFrame({"t5_raw": t5_raw, "t5": np.floor(t5_raw + 0.5)})
    reports = load_metar(data_dir)
    routine = reports[reports["routine"] & reports["has_tgroup"]].drop_duplicates("valid").set_index("valid")
    return Base(man=man, temps=temps, kmin=kmin, routine=routine, reports=reports,
                cli=load_cli(data_dir), nbm=load_nbm(data_dir), airports=load_airports(data_dir))


def fit_blend_weights(base: Base, until: str = "2025-01-01") -> dict[str, float]:
    """Least-squares weights mapping 60-minute station changes onto 60-minute KNYC changes."""
    X = base.temps.copy()
    # KNYC's 5-minute mean ending 2 minutes earlier lines up best with the Mesonet stamp.
    X["K"] = base.kmin["t5_raw"].shift(2).reindex(X.index)
    d = X.diff(12)
    d = d[d.index < pd.Timestamp(until, tz="UTC")].dropna()
    cols = list(STATIONS)
    w, *_ = np.linalg.lstsq(d[cols].to_numpy(), d["K"].to_numpy(), rcond=None)
    return {c: float(v) for c, v in zip(cols, w)}


def blend_change(a: np.ndarray, b: np.ndarray, w: np.ndarray) -> np.ndarray:
    """Weighted station change b - a (arrays [..., S]) re-normalised over reporting stations."""
    diff = b - a
    ok = np.isfinite(diff)
    wsum = np.where(ok, w, 0.0).sum(axis=-1)
    num = np.where(ok, diff * w, 0.0).sum(axis=-1)
    total = w.sum()
    with np.errstate(invalid="ignore", divide="ignore"):
        out = num / wsum * total
    return np.where(wsum >= 0.5 * total, out, np.nan)


def _lookup(series: pd.Series, times: np.ndarray) -> np.ndarray:
    idx = pd.DatetimeIndex(np.asarray(times).ravel())
    if idx.tz is None:
        idx = idx.tz_localize("UTC")
    return series.reindex(idx).to_numpy(dtype=float).reshape(np.shape(times))


def add_time_features(df: pd.DataFrame, col: str = "tau") -> pd.DataFrame:
    t = pd.DatetimeIndex(df[col])
    local = t.tz_convert("America/New_York")
    hour = local.hour + local.minute / 60.0
    doy = local.dayofyear
    df["hour_local"] = hour
    df["hour_sin"] = np.sin(2 * np.pi * hour / 24)
    df["hour_cos"] = np.cos(2 * np.pi * hour / 24)
    df["doy_sin"] = np.sin(2 * np.pi * doy / 365.25)
    df["doy_cos"] = np.cos(2 * np.pi * doy / 365.25)
    df["month"] = local.month
    elev = solar_elevation(t)
    df["sun_elev"] = elev
    df["clear_sky"] = clear_sky_ghi(elev)
    return df


# ---------------------------------------------------------------------------------------
# Model A: next routine METAR
# ---------------------------------------------------------------------------------------
STEPS = np.arange(12)  # tau = t0 + 4 + 5*k minutes  ->  hh:55, hh+1:00, ..., hh+1:50
REL = np.arange(-31, 60, 5)  # window columns relative to t0 (minutes): hh:20 ... hh+1:50
FIRST = int(np.where(REL == 4)[0][0])


def build_model_a(base: Base) -> pd.DataFrame:
    w = np.array([base.weights[s] for s in STATIONS])
    r = base.routine
    t0 = r.index
    y0 = r["temp"].to_numpy(float)
    y1 = r["temp"].reindex(t0 + pd.Timedelta(minutes=60)).to_numpy(float)
    yprev = r["temp"].reindex(t0 - pd.Timedelta(minutes=60)).to_numpy(float)
    yprev2 = r["temp"].reindex(t0 - pd.Timedelta(minutes=120)).to_numpy(float)
    keep = ~np.isnan(y1)
    t0, y0, y1, yprev, yprev2 = t0[keep], y0[keep], y1[keep], yprev[keep], yprev2[keep]
    t0v = t0.values.astype("datetime64[ns]")
    win = t0v[:, None] + REL[None, :].astype("timedelta64[m]")
    taus = win[:, FIRST:]
    n, k = taus.shape

    # Station windows: [N, cols, S]
    Fs = np.stack([_lookup(base.temps[s], win) for s in STATIONS], axis=-1)
    prev_hr = np.stack([_lookup(base.temps[s], t0v - np.timedelta64(61, "m")) for s in STATIONS], axis=-1)
    F = Fs[:, :, 0]                                  # MANH
    V = F[:, FIRST:]
    m0 = F[:, FIRST - 1]                             # hh:50 sample, aligned with the METAR window
    anchor = Fs[:, FIRST - 1:FIRST, :]               # [N,1,S]
    Z = blend_change(np.broadcast_to(anchor, Fs[:, FIRST:, :].shape), Fs[:, FIRST:, :], w)
    Z15 = blend_change(Fs[:, FIRST - 3:-3, :], Fs[:, FIRST:, :], w)
    Zprev = blend_change(prev_hr, Fs[:, FIRST - 1, :], w)

    def cumext(M: np.ndarray, fn) -> np.ndarray:
        fill = -np.inf if fn is np.fmax else np.inf
        out = fn.accumulate(np.where(np.isnan(M), fill, M), axis=1)
        out[np.isinf(out)] = np.nan
        return out

    S = base.man["srad"]
    srad = _lookup(S, taus)
    srad30 = _lookup(S.rolling(6, min_periods=3).mean(), taus)
    srad_m30 = _lookup(S.rolling(6, min_periods=3).mean(), taus - np.timedelta64(30, "m"))
    wspd = _lookup(base.man["wspd"].rolling(3, min_periods=1).mean(), taus)
    wdir = _lookup(base.man["wdir"], taus)
    relh = _lookup(base.man["relh"], taus)
    P = base.man["pres"]
    pres_d60 = _lookup(P, taus) - _lookup(P, taus - np.timedelta64(60, "m"))
    precip30 = _lookup(base.man["precip_inc"].fillna(0.0).rolling(6, min_periods=1).sum(), taus)

    rep = lambda x: np.repeat(x, k)  # noqa: E731
    cols = {
        "t0": rep(t0v),
        "tau": taus.ravel(),
        "step": np.tile(STEPS, n),
        "lead_min": np.tile(56 - 5 * STEPS, n),
        "y0": rep(y0),
        "y1": rep(y1),
        "d_prev": rep(y0 - yprev),
        "d_prev2": rep(yprev - yprev2),
        "m0": rep(m0),
        "dm": (V - m0[:, None]).ravel(),
        "dm5": (V - F[:, FIRST - 1:-1]).ravel(),
        "dm15": (V - F[:, FIRST - 3:-3]).ravel(),
        "dmax": (cumext(V, np.fmax) - m0[:, None]).ravel(),
        "dmin": (cumext(V, np.fmin) - m0[:, None]).ravel(),
        "dm_prevhr": rep(m0 - prev_hr[:, 0]),
        "dm_pre30": rep(m0 - F[:, 0]),
        "offset": rep(m0 - y0),
        "dz": Z.ravel(),
        "dz15": Z15.ravel(),
        "dzmax": cumext(Z, np.fmax).ravel(),
        "dzmin": cumext(Z, np.fmin).ravel(),
        "dz_prevhr": rep(Zprev),
        "srad": srad.ravel(),
        "srad30": srad30.ravel(),
        "srad_d30": (srad30 - srad_m30).ravel(),
        "wspd": wspd.ravel(),
        "wdir_sin": np.sin(np.deg2rad(wdir)).ravel(),
        "wdir_cos": np.cos(np.deg2rad(wdir)).ravel(),
        "relh": relh.ravel(),
        "pres_d60": pres_d60.ravel(),
        "precip30": precip30.ravel(),
    }
    for j, s in enumerate(STATIONS):
        if s == "MANH":
            continue
        cols[f"{s}_dm"] = (Fs[:, FIRST:, j] - Fs[:, FIRST - 1, j][:, None]).ravel()
        cols[f"{s}_off"] = rep(Fs[:, FIRST - 1, j] - y0)
    rows = pd.DataFrame(cols)
    rows["tau"] = pd.to_datetime(rows["tau"], utc=True)
    rows["t0"] = pd.to_datetime(rows["t0"], utc=True)
    rows = add_time_features(rows)
    rows["kt"] = rows["srad30"] / np.maximum(rows["clear_sky"], 50.0)
    rows["dy"] = rows["y1"] - rows["y0"]
    # Hidden KNYC 5-minute value at tau (diagnostic only; never a feature)
    rows["k_tau"] = base.kmin["t5_raw"].reindex(pd.DatetimeIndex(rows["tau"])).to_numpy()
    if base.airports:
        air = airport_features(base.airports, pd.DatetimeIndex(rows["tau"]), rows["y0"].to_numpy())
        rows = pd.concat([rows, air.set_index(rows.index)], axis=1)
    return rows


# ---------------------------------------------------------------------------------------
# Model B: hidden running max and final CLI high
# ---------------------------------------------------------------------------------------

def nbm_daily(nbm: pd.DataFrame) -> pd.DataFrame:
    """For each NBM run: daytime max (TXN at 00Z) for each climate date and its spread."""
    nb = nbm.dropna(subset=["txn"]).copy()
    nb = nb[nb["ftime"].dt.hour == 0]
    # TXN at 00Z on day D+1 is the 12Z-00Z (daytime) max for local date D.
    nb["cdate"] = (nb["ftime"] - pd.Timedelta(hours=12)).dt.date
    nb["avail"] = nb["runtime"] + NBM_LAG
    return nb[["runtime", "avail", "cdate", "txn", "xnd"]].sort_values("avail")


def path_features(taus: np.ndarray, T: np.ndarray, w: np.ndarray,
                  rt0: np.ndarray, ry: np.ndarray, prefix: str) -> dict[str, np.ndarray]:
    """Implied-KNYC path features for one climate day.

    taus: decision grid (datetime64, 5-minute, also the station grid), T: [len(taus)+pad, S]
    station temps on grid `grid_all` (taus with a 12-step pre-day pad), rt0/ry: routine METAR
    times/temps of the day. For each closed METAR-to-METAR hour the implied peak is the
    straight line between the two METARs plus beta times the blend's deviation from its own
    straight line. For the open hour since the last published METAR the implied value is
    y_last + beta * blend change.
    """
    pad = 12
    grid_all = np.concatenate([taus[0] - np.arange(pad, 0, -1) * np.timedelta64(5, "m"), taus])
    pos = {t: i for i, t in enumerate(grid_all.astype("datetime64[m]"))}
    nt = len(taus)
    out = {f"{prefix}exc_closed": np.full(nt, -np.inf)}
    for b in BETAS:
        out[f"{prefix}imp_closed_b{b}"] = np.full(nt, -np.inf)
    anchors = [pos.get((t - np.timedelta64(1, "m")).astype("datetime64[m]")) for t in rt0]
    for i in range(len(rt0) - 1):
        ia, ib = anchors[i], anchors[i + 1]
        if ia is None or ib is None or ib - ia != 12:
            continue
        seg = T[ia:ib + 1]                                 # anchor a, 11 interior points, anchor b
        frac = np.arange(13) / 12.0
        ta_vals, tb_vals = seg[0], seg[-1]
        line = ta_vals[None, :] + (tb_vals - ta_vals)[None, :] * frac[:, None]
        dev = blend_change(line[1:-1], seg[1:-1], w)        # blend deviation from its own line
        if not np.isfinite(dev).any():
            continue
        yline = ry[i] + (ry[i + 1] - ry[i]) * frac[1:-1]
        avail = rt0[i + 1] + PUBLISH_LAG.to_timedelta64()
        mask = taus >= avail
        exc = np.nanmax(dev)
        out[f"{prefix}exc_closed"] = np.where(mask, np.maximum(out[f"{prefix}exc_closed"], exc), out[f"{prefix}exc_closed"])
        for b in BETAS:
            key = f"{prefix}imp_closed_b{b}"
            imp = np.nanmax(yline + b * dev)
            out[key] = np.where(mask, np.maximum(out[key], imp), out[key])
    # open interval
    pub = rt0 + PUBLISH_LAG.to_timedelta64()
    li = np.searchsorted(pub, taus, side="right") - 1
    dz_open = np.full(nt, np.nan)
    dzmax_open = np.full(nt, np.nan)
    run_max, cur_li = -np.inf, -2
    for j in range(nt):
        if li[j] < 0 or anchors[li[j]] is None:
            continue
        if li[j] != cur_li:
            run_max, cur_li = -np.inf, li[j]
            # include grid points between the anchor and the first decision after publication
            a = anchors[li[j]]
            for g in range(a + 1, pad + j):
                v = blend_change(T[a], T[g], w)
                if np.isfinite(v):
                    run_max = max(run_max, float(v))
        v = blend_change(T[anchors[li[j]]], T[pad + j], w)
        dz_open[j] = v
        if np.isfinite(v):
            run_max = max(run_max, float(v))
        dzmax_open[j] = run_max if np.isfinite(run_max) else np.nan
    y_last = np.where(li >= 0, ry[np.maximum(li, 0)], np.nan)
    out[f"{prefix}dz_open"] = dz_open
    out[f"{prefix}dzmax_open"] = dzmax_open
    for b in BETAS:
        out[f"{prefix}imp_open_b{b}"] = y_last + b * dzmax_open
        out[f"{prefix}imp_now_b{b}"] = y_last + b * dz_open
    out["y_last"] = y_last
    for key, val in out.items():
        out[key] = np.where(np.isinf(val), np.nan, val)
    return out


def build_model_b(base: Base, step: str = "5min") -> pd.DataFrame:
    w_all = np.array([base.weights[s] for s in STATIONS])
    w_manh = np.array([1.0 if s == "MANH" else 0.0 for s in STATIONS])
    temps = base.temps
    srad = base.man["srad"]
    t5 = base.kmin["t5"]
    reports = base.reports.dropna(subset=["temp"]).copy()
    reports["cdate"] = climate_date(reports["valid"])
    routine = base.routine.copy()
    routine["cdate"] = climate_date(routine.index)
    nbm_by_date = {d: g for d, g in nbm_daily(base.nbm).groupby("cdate")}
    cli = base.cli["high"]

    start = max(temps.index.min(), base.kmin.index.min()).ceil("D")
    end = min(temps.index.max(), base.reports["valid"].max()).floor("D")
    days = pd.date_range(start, end - pd.Timedelta(days=1), freq="D").date
    out = []
    for d in days:
        if d not in cli.index:
            continue
        d0 = pd.Timestamp(d, tz="UTC") + pd.Timedelta(hours=5)   # LST midnight
        d1 = d0 + pd.Timedelta(days=1)
        taus = pd.date_range(d0 + pd.Timedelta(minutes=5), d1, freq=step)
        tv = taus.values
        mv = temps["MANH"].reindex(taus).to_numpy()
        if np.isnan(mv).mean() > 0.1:
            continue
        rep = reports[reports["cdate"] == d]
        rou = routine[routine["cdate"] == d]
        if len(rou) < 20:
            continue
        # --- publicly known max at each tau (METAR/SPECI + in-day 6-hour max groups) ---
        rv = rep["valid"].values + PUBLISH_LAG.to_timedelta64()
        rt = rep["temp"].to_numpy(float)
        order = np.argsort(rv)
        rv, rt = rv[order], rt[order]
        idx = np.searchsorted(rv, tv, side="right")
        cm = np.maximum.accumulate(rt)
        known = np.where(idx > 0, cm[np.maximum(idx - 1, 0)], -np.inf)
        six = np.full(len(taus), -np.inf)
        syn = rep[rep["max6"].notna() & rep["valid"].dt.hour.isin([11, 17, 23])]
        for vt, mx in zip(syn["valid"].values, syn["max6"].to_numpy(float)):
            six = np.where(tv >= vt + PUBLISH_LAG.to_timedelta64(), np.maximum(six, mx), six)
        known_all = np.maximum(known, six)
        # --- true running max from the 1-minute reconstruction ---
        tr = t5.reindex(pd.date_range(d0, d1, freq="1min"))
        cover = tr.notna().cumsum().reindex(taus).to_numpy() / ((taus - d0) / pd.Timedelta(minutes=1) + 1)
        runmax = tr.cummax().reindex(taus).to_numpy()
        # --- implied KNYC path from MANH alone and from the six-station blend ---
        grid_all = pd.date_range(taus[0] - pd.Timedelta(minutes=60), taus[-1], freq="5min")
        T = temps.reindex(grid_all)[list(STATIONS)].to_numpy()
        rt0 = rou.index.values
        ry = rou["temp"].to_numpy(float)
        pf = path_features(tv, T, w_manh, rt0, ry, "m_")
        pz = path_features(tv, T, w_all, rt0, ry, "z_")
        # NBM
        txn = np.full(len(taus), np.nan)
        xnd = np.full(len(taus), np.nan)
        nb = nbm_by_date.get(d)
        if nb is not None:
            pos = np.searchsorted(nb["avail"].values, tv, side="right") - 1
            ok = pos >= 0
            txn[ok] = nb["txn"].to_numpy(float)[pos[ok]]
            xnd[ok] = nb["xnd"].to_numpy(float)[pos[ok]]
        mday = pd.Series(mv).cummax().to_numpy()
        df = pd.DataFrame({
            "cdate": d,
            "tau": taus,
            "mins_into_day": ((taus - d0) / pd.Timedelta(minutes=1)).astype(float),
            "known_metar": known,
            "known_max": known_all,
            "six_known": np.isfinite(six).astype(int),
            "m_now": mv,
            "m_below_daymax": mv - mday,
            "m_slope30": mv - temps["MANH"].reindex(taus - pd.Timedelta(minutes=30)).to_numpy(),
            "srad": srad.reindex(taus).to_numpy(),
            "srad30": srad.rolling(6, min_periods=3).mean().reindex(taus).to_numpy(),
            "txn": txn,
            "xnd": xnd,
            "runmax": runmax,
            "cover": cover,
            "cli": float(cli.loc[d]),
        })
        for key, val in {**pf, **pz}.items():
            df[key] = val
        out.append(df)
    res = pd.concat(out, ignore_index=True)
    res = res[np.isfinite(res["known_max"])].reset_index(drop=True)
    res["y_below"] = res["y_last"] - res["known_max"]
    for p in ("m_", "z_"):
        for b in BETAS:
            res[f"{p}gap_closed_b{b}"] = res[f"{p}imp_closed_b{b}"] - res["known_max"]
            res[f"{p}gap_open_b{b}"] = res[f"{p}imp_open_b{b}"] - res["known_max"]
            res[f"{p}gap_now_b{b}"] = res[f"{p}imp_now_b{b}"] - res["known_max"]
    res["txn_gap"] = res["txn"] - res["known_max"]
    res["hid"] = res["runmax"] - res["known_max"]
    res["fin"] = res["cli"] - res["known_max"]
    res = add_time_features(res)
    res["kt"] = res["srad30"] / np.maximum(res["clear_sky"], 50.0)
    if base.airports:
        air = airport_features(base.airports, pd.DatetimeIndex(res["tau"]), res["known_max"].to_numpy(),
                               np.asarray(res["cdate"]))
        res = pd.concat([res, air.set_index(res.index)], axis=1)
    return res
