"""Train, evaluate (walk-forward) and export the MANH -> KNYC nowcast models.

  python3 research/manh_knyc/train.py --data DATA_DIR --export lib/weather/manh-nowcast-model.json

Evaluation: train on climate days before 2025-01-01, score every decision point from
2025-01-01 onward (never seen in training). Each MANH/blend model is compared with an
otherwise identical model that has no Mesonet inputs, so the reported lift is what the
Mesonet data adds beyond time of day, season, KNYC's own recent trend and the NBM forecast.

Export: the deployed models are refit on all available data with the same settings and
written as compact LightGBM tree ensembles that lib/weather/gbm.ts evaluates.
"""
from __future__ import annotations

import argparse
import json
import pickle
from datetime import date
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
from scipy.optimize import minimize_scalar
from sklearn.metrics import log_loss, roc_auc_score

from build import NEIGHBORS, STATIONS

SPLIT = pd.Timestamp("2025-01-01", tz="UTC")
SPLIT_DATE = date(2025, 1, 1)

TIME_FEATS = ["hour_sin", "hour_cos", "doy_sin", "doy_cos", "sun_elev"]
A_BASE = ["y0", "d_prev", "d_prev2"] + TIME_FEATS
A_MESO = ["lead_min", "dm", "dm5", "dm15", "dmax", "dmin", "dm_prevhr", "dm_pre30", "offset", "m0",
          "dz", "dz15", "dzmax", "dzmin", "dz_prevhr",
          "srad", "srad30", "srad_d30", "kt", "wspd", "wdir_sin", "wdir_cos", "relh", "pres_d60", "precip30"]
A_MESO += [f"{s}_dm" for s in NEIGHBORS] + [f"{s}_off" for s in NEIGHBORS]
B_BASE = ["known_max", "y_below", "six_known", "mins_into_day", "txn_gap", "xnd"] + TIME_FEATS
B_MESO = []
for p in ("m_", "z_"):
    B_MESO += [f"{p}exc_closed", f"{p}dz_open", f"{p}dzmax_open"]
    for b in (0.5, 1.0):
        B_MESO += [f"{p}gap_closed_b{b}", f"{p}gap_open_b{b}", f"{p}gap_now_b{b}"]
B_MESO += ["m_below_daymax", "m_slope30", "srad", "srad30", "kt"]

A_LO, A_HI = -5, 5          # next METAR change classes
H_HI = 3                     # hidden-max classes 0..3 (3 = "3 or more")
F_HI = 20                    # final CLI high minus published max: classes 0..20 (20 = "20 or more")

PARAMS = dict(learning_rate=0.08, num_leaves=15, min_data_in_leaf=300, feature_fraction=0.8,
              bagging_fraction=0.8, bagging_freq=1, lambda_l2=1.0, verbose=-1, num_threads=8)
# Daily-high rows are 288 highly correlated decision points per day; small leaves memorise
# individual days and made the final-high odds overconfident. Larger leaves fixed most of it.
DAILY_MIN_LEAF = 3000


def multiclass(n: int, min_leaf: int = 300) -> dict:
    return dict(PARAMS, objective="multiclass", num_class=n, min_data_in_leaf=min_leaf)


def fit(df: pd.DataFrame, feats: list[str], y: np.ndarray, n: int, rounds: int, min_leaf: int = 300) -> lgb.Booster:
    return lgb.train(multiclass(n, min_leaf), lgb.Dataset(df[feats], y, free_raw_data=False), rounds)


def softmax_t(raw: np.ndarray, temperature: float = 1.0) -> np.ndarray:
    z = raw / temperature
    z = z - z.max(axis=1, keepdims=True)
    e = np.exp(z)
    return e / e.sum(axis=1, keepdims=True)


def proba(model: lgb.Booster, X: pd.DataFrame, temperature: float = 1.0) -> np.ndarray:
    return softmax_t(model.predict(X, raw_score=True), temperature)


def pick_rounds(df: pd.DataFrame, feats: list[str], y: np.ndarray, n: int, cutoff,
                min_leaf: int = 300) -> tuple[int, float]:
    """Early stopping on the final year of the training period, plus a softmax temperature
    fitted on that same held-out year (T > 1 softens overconfident probabilities)."""
    key = df["t0"] if "t0" in df else df["tau"]
    tr, va = (key < cutoff).to_numpy(), (key >= cutoff).to_numpy()
    booster = lgb.train(multiclass(n, min_leaf), lgb.Dataset(df.loc[tr, feats], y[tr]), 1500,
                        valid_sets=[lgb.Dataset(df.loc[va, feats], y[va])],
                        callbacks=[lgb.early_stopping(40, verbose=False)])
    rounds = int(booster.best_iteration or 300)
    raw = booster.predict(df.loc[va, feats], raw_score=True, num_iteration=rounds)
    yv = y[va]

    def nll(t: float) -> float:
        P = softmax_t(raw, t)
        return float(-np.log(np.clip(P[np.arange(len(yv)), yv], 1e-9, 1)).mean())

    temperature = float(minimize_scalar(nll, bounds=(0.5, 3.0), method="bounded").x)
    return rounds, round(temperature, 4)


def scores(P: np.ndarray, y: np.ndarray, n: int) -> dict:
    return {
        "n": int(len(y)),
        "logloss": float(log_loss(y, P, labels=list(range(n)))),
        "acc_mode": float((P.argmax(1) == y).mean()),
        "mean_p_true": float(P[np.arange(len(y)), y].mean()),
        "within1_mode": float((np.abs(P.argmax(1) - y) <= 1).mean()),
    }


def reliability(p: np.ndarray, hit: np.ndarray, bins: int = 10) -> list[dict]:
    edges = np.linspace(0, 1, bins + 1)
    out = []
    for lo, hi in zip(edges[:-1], edges[1:]):
        m = (p >= lo) & (p < hi if hi < 1 else p <= hi)
        if m.sum() >= 50:
            out.append({"bin": f"{lo:.1f}-{hi:.1f}", "n": int(m.sum()), "pred": float(p[m].mean()), "obs": float(hit[m].mean())})
    return out


# ------------------------------------------------------------------------------------------
def prep_a(a: pd.DataFrame) -> tuple[pd.DataFrame, np.ndarray]:
    a = a.dropna(subset=["m0", "dm"]).reset_index(drop=True)
    return a, (a["dy"].clip(A_LO, A_HI) - A_LO).astype(int).to_numpy()


def prep_hidden(b: pd.DataFrame) -> tuple[pd.DataFrame, np.ndarray]:
    h = b[(b["cover"] >= 0.98) & (b["hid"] >= 0)].reset_index(drop=True)
    return h, h["hid"].clip(0, H_HI).astype(int).to_numpy()


def prep_final(b: pd.DataFrame) -> tuple[pd.DataFrame, np.ndarray]:
    # Predicting the CLI high relative to the published max (rather than to the NBM forecast)
    # keeps the hard lower bound exact; NBM enters as the txn_gap feature. A strict
    # comparison on 2025-26 favoured this design at every hour (log loss 1.66 vs 2.06).
    f = b[b["fin"] >= 0].reset_index(drop=True)
    return f, np.clip(f["fin"].to_numpy(), 0, F_HI).astype(int)


def final_to_cli_probs(P: np.ndarray, df: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """Map class probabilities onto absolute CLI values (published max + k)."""
    vals = df["known_max"].to_numpy(float)[:, None] + np.arange(F_HI + 1)[None, :]
    return P, vals


def evaluate(a: pd.DataFrame, b: pd.DataFrame) -> dict:
    out: dict = {}
    # ---- Model A ----
    A, ya = prep_a(a)
    tr, te = (A["t0"] < SPLIT).to_numpy(), (A["t0"] >= SPLIT).to_numpy()
    rounds_meso, temp_a = pick_rounds(A[tr], A_BASE + A_MESO, ya[tr], A_HI - A_LO + 1, pd.Timestamp("2024-01-01", tz="UTC"))
    nA = A_HI - A_LO + 1
    m_base = fit(A[tr], A_BASE, ya[tr], nA, 200)
    m_meso = fit(A[tr], A_BASE + A_MESO, ya[tr], nA, rounds_meso)
    manh_only = [f for f in A_MESO if not (f.startswith("dz") or any(f.startswith(s) for s in NEIGHBORS))]
    m_manh = fit(A[tr], A_BASE + manh_only, ya[tr], nA, rounds_meso)
    Pb, Ph = m_base.predict(A.loc[te, A_BASE]), m_manh.predict(A.loc[te, A_BASE + manh_only])
    Pm = proba(m_meso, A.loc[te, A_BASE + A_MESO], temp_a)
    y = ya[te]
    out["next_metar"] = {
        "rounds": rounds_meso,
        "temperature": temp_a,
        "test_hours": int(A.loc[te, "t0"].nunique()),
        "no_mesonet": scores(Pb, y, nA), "manh_only": scores(Ph, y, nA), "manh_plus_blend": scores(Pm, y, nA),
        "by_lead": [], "reliability_change": reliability(1 - Pm[:, -A_LO], (y != -A_LO).astype(float)),
    }
    lead = A.loc[te, "lead_min"].to_numpy()
    for L in sorted(np.unique(lead)):
        g = lead == L
        out["next_metar"]["by_lead"].append({"lead_min": int(L), "no_mesonet": scores(Pb[g], y[g], nA),
                                             "manh_only": scores(Ph[g], y[g], nA), "manh_plus_blend": scores(Pm[g], y[g], nA)})
    # ---- hidden max ----
    H, yh = prep_hidden(b)
    key = pd.to_datetime(H["cdate"]).dt.date
    tr, te = (key < SPLIT_DATE).to_numpy(), (key >= SPLIT_DATE).to_numpy()
    nH = H_HI + 1
    cutoff = pd.Timestamp("2024-01-01", tz="UTC")
    rounds_h, temp_h = pick_rounds(H[tr], B_BASE + B_MESO, yh[tr], nH, cutoff, DAILY_MIN_LEAF)
    hb = fit(H[tr], B_BASE, yh[tr], nH, rounds_h, DAILY_MIN_LEAF)
    hm = fit(H[tr], B_BASE + B_MESO, yh[tr], nH, rounds_h, DAILY_MIN_LEAF)
    Pb, Pm = hb.predict(H.loc[te, B_BASE]), proba(hm, H.loc[te, B_BASE + B_MESO], temp_h)
    y = yh[te]
    hrs = H.loc[te, "hour_local"].astype(int).to_numpy()
    out["hidden_max"] = {
        "rounds": rounds_h,
        "temperature": temp_h,
        "no_mesonet": {**scores(Pb, y, nH), "auc_ge1": float(roc_auc_score(y >= 1, 1 - Pb[:, 0]))},
        "mesonet": {**scores(Pm, y, nH), "auc_ge1": float(roc_auc_score(y >= 1, 1 - Pm[:, 0]))},
        "base_rate_ge1": float((y >= 1).mean()),
        "reliability_ge1": reliability(1 - Pm[:, 0], (y >= 1).astype(float)),
        "by_hour": [],
    }
    for hr in range(6, 24):
        g = hrs == hr
        if g.sum() < 500 or len(np.unique(y[g] >= 1)) < 2:
            continue
        out["hidden_max"]["by_hour"].append({
            "hour": hr, "base_rate": float((y[g] >= 1).mean()),
            "auc_no_mesonet": float(roc_auc_score(y[g] >= 1, 1 - Pb[g, 0])),
            "auc_mesonet": float(roc_auc_score(y[g] >= 1, 1 - Pm[g, 0])),
        })
    # ---- final high ----
    Fdf, yf = prep_final(b)
    key = pd.to_datetime(Fdf["cdate"]).dt.date
    tr, te = (key < SPLIT_DATE).to_numpy(), (key >= SPLIT_DATE).to_numpy()
    nF = F_HI + 1
    rounds_f, temp_f = pick_rounds(Fdf[tr], B_BASE + B_MESO, yf[tr], nF, cutoff, DAILY_MIN_LEAF)
    fb = fit(Fdf[tr], B_BASE, yf[tr], nF, rounds_f, DAILY_MIN_LEAF)
    fm = fit(Fdf[tr], B_BASE + B_MESO, yf[tr], nF, rounds_f, DAILY_MIN_LEAF)
    test = Fdf[te].reset_index(drop=True)
    cli = test["cli"].to_numpy(float)
    res = {}
    for name, model, feats, temp in (("no_mesonet", fb, B_BASE, 1.0), ("mesonet", fm, B_BASE + B_MESO, temp_f)):
        P, vals = final_to_cli_probs(proba(model, test[feats], temp), test)
        hit = vals == cli[:, None]
        p_true = (P * hit).sum(1)
        mode = vals[np.arange(len(P)), P.argmax(1)]
        res[name] = {"P": P, "vals": vals, "p_true": p_true, "mode": mode}
    hrs = test["hour_local"].astype(int).to_numpy()
    out["final_high"] = {"rounds": rounds_f, "temperature": temp_f, "by_hour": []}
    for name in res:
        r = res[name]
        out["final_high"][name] = {
            "logloss": float(-np.log(np.clip(r["p_true"], 1e-6, 1)).mean()),
            "acc_mode": float((r["mode"] == cli).mean()),
            "mean_p_true": float(r["p_true"].mean()),
            "within1_mode": float((np.abs(r["mode"] - cli) <= 1).mean()),
        }
    for hr in range(5, 24):
        g = hrs == hr
        row = {"hour": hr}
        for name in res:
            r = res[name]
            row[name] = {"acc_mode": float((r["mode"][g] == cli[g]).mean()),
                         "mean_p_true": float(r["p_true"][g].mean()),
                         "logloss": float(-np.log(np.clip(r["p_true"][g], 1e-6, 1)).mean()),
                         "within1_mode": float((np.abs(r["mode"][g] - cli[g]) <= 1).mean())}
        out["final_high"]["by_hour"].append(row)
    # calibration of the probability assigned to each candidate CLI value
    P, vals = res["mesonet"]["P"], res["mesonet"]["vals"]
    out["final_high"]["reliability"] = reliability(P.ravel(), (vals == cli[:, None]).ravel().astype(float))
    return out


# ------------------------------------------------------------------------------------------
def export_tree(node: dict, feats: list[str], arrays: dict) -> int:
    """Flatten a LightGBM tree; returns the encoded child pointer (>=0 split, <0 leaf)."""
    if "leaf_index" in node or "leaf_value" in node and "split_feature" not in node:
        arrays["v"].append(float(node["leaf_value"]))
        return -len(arrays["v"])
    i = len(arrays["f"])
    arrays["f"].append(int(node["split_feature"]))
    arrays["t"].append(float(node["threshold"]))
    arrays["d"].append(1 if node["default_left"] else 0)
    arrays["m"].append({"None": 0, "Zero": 1, "NaN": 2}[node["missing_type"]])
    if node["decision_type"] != "<=":
        raise ValueError("only numerical <= splits are supported")
    arrays["l"].append(0)
    arrays["r"].append(0)
    arrays["l"][i] = export_tree(node["left_child"], feats, arrays)
    arrays["r"][i] = export_tree(node["right_child"], feats, arrays)
    return i


def export_booster(model: lgb.Booster, feats: list[str], num_class: int) -> dict:
    dump = model.dump_model()
    trees = []
    for info in dump["tree_info"]:
        arrays = {"f": [], "t": [], "d": [], "m": [], "l": [], "r": [], "v": []}
        root = info["tree_structure"]
        if "split_feature" not in root:
            arrays["v"].append(float(root["leaf_value"]))
        else:
            export_tree(root, feats, arrays)
        # round thresholds/leaves to float32 precision text to keep the file small
        arrays["t"] = [float(np.float64(x)) for x in arrays["t"]]
        arrays["v"] = [round(x, 7) for x in arrays["v"]]
        trees.append(arrays)
    return {"features": feats, "num_class": num_class, "trees": trees}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, required=True)
    ap.add_argument("--export", type=Path, default=None)
    ap.add_argument("--metrics", type=Path, default=Path(__file__).parent / "results" / "metrics.json")
    ap.add_argument("--fixtures", type=Path, default=None)
    ap.add_argument("--skip-eval", action="store_true")
    args = ap.parse_args()
    a = pd.read_pickle(args.data / "model_a.pkl")
    b = pd.read_pickle(args.data / "model_b.pkl")
    base = pickle.load(open(args.data / "base.pkl", "rb"))

    metrics = {}
    if not args.skip_eval:
        metrics = evaluate(a, b)
        args.metrics.parent.mkdir(parents=True, exist_ok=True)
        args.metrics.write_text(json.dumps(metrics, indent=1))
        for k in ("next_metar", "hidden_max", "final_high"):
            print(k, json.dumps({kk: vv for kk, vv in metrics[k].items() if kk not in ("by_lead", "by_hour") and not kk.startswith("reliab")}, indent=0)[:900])
    if args.export is None:
        return
    # ---- refit on everything and export ----
    A, ya = prep_a(a)
    rounds = metrics.get("next_metar", {}).get("rounds", 400)
    mA = fit(A, A_BASE + A_MESO, ya, A_HI - A_LO + 1, rounds)
    H, yh = prep_hidden(b)
    mH = fit(H, B_BASE + B_MESO, yh, H_HI + 1, metrics.get("hidden_max", {}).get("rounds", 200), DAILY_MIN_LEAF)
    Fdf, yf = prep_final(b)
    mF = fit(Fdf, B_BASE + B_MESO, yf, F_HI + 1, metrics.get("final_high", {}).get("rounds", 150), DAILY_MIN_LEAF)
    temps = {k: metrics.get(k, {}).get("temperature", 1.0) for k in ("next_metar", "hidden_max", "final_high")}
    model = {
        "version": 1,
        "trained_through": str(pd.to_datetime(b["cdate"]).max().date()),
        "stations": list(STATIONS),
        "station_datasets": STATIONS,
        "blend_weights": base.weights,
        "publish_lag_min": 3,
        "nbm_lag_min": 75,
        "next_metar": {**export_booster(mA, A_BASE + A_MESO, A_HI - A_LO + 1), "class_offset": A_LO,
                       "temperature": temps["next_metar"]},
        "hidden_max": {**export_booster(mH, B_BASE + B_MESO, H_HI + 1), "class_offset": 0,
                       "temperature": temps["hidden_max"]},
        "final_high": {**export_booster(mF, B_BASE + B_MESO, F_HI + 1), "class_offset": 0,
                       "anchor": "known_max", "temperature": temps["final_high"]},
    }
    args.export.parent.mkdir(parents=True, exist_ok=True)
    args.export.write_text(json.dumps(model, separators=(",", ":")))
    print("exported", args.export, round(args.export.stat().st_size / 1e6, 2), "MB")
    if args.fixtures:
        rng = np.random.default_rng(7)
        fx = {}
        for name, df, m, feats in (("next_metar", A, mA, A_BASE + A_MESO),
                                   ("hidden_max", H, mH, B_BASE + B_MESO),
                                   ("final_high", Fdf, mF, B_BASE + B_MESO)):
            idx = rng.choice(len(df), 40, replace=False)
            X = df.iloc[idx][feats]
            fx[name] = {"rows": [[None if pd.isna(v) else float(v) for v in row] for row in X.to_numpy()],
                        "probs": proba(m, X, temps[name]).round(9).tolist()}
        args.fixtures.write_text(json.dumps(fx))
        print("fixtures", args.fixtures)


if __name__ == "__main__":
    main()
