# MANH → KNYC calibration study

Reproducible pipeline behind `docs/MANH_KNYC_CALIBRATION.md` and the live
`/api/weather-dashboard/manh-nowcast` panel.

```bash
pip install pandas numpy lightgbm scikit-learn
DATA=/path/to/cache   # ~1.5 GB once everything is cached; not committed

# 1. Download (cached per window; safe to re-run)
python3 research/manh_knyc/fetch_data.py --out $DATA --start 2021-01-01
for st in BRON QUEE BKLN; do
  python3 research/manh_knyc/fetch_data.py --out $DATA --only manh --nysm-stations $st --start 2021-01-01
done
for st in MHMHIL MHCHEL; do
  python3 research/manh_knyc/fetch_data.py --out $DATA --only manh --nysm-dataset nyc --nysm-stations $st --start 2021-01-01
done

# 2. Build datasets (base.pkl, model_a.pkl, model_b.pkl in $DATA)
python3 - <<'PY'
import pickle, sys; from pathlib import Path
sys.path.insert(0, "research/manh_knyc")
from build import load_base, fit_blend_weights, build_model_a, build_model_b
D = Path("$DATA")
base = load_base(D); base.weights = fit_blend_weights(base)
pickle.dump(base, open(D / "base.pkl", "wb"))
build_model_a(base).to_pickle(D / "model_a.pkl")
build_model_b(base).to_pickle(D / "model_b.pkl")
PY

# 3. Walk-forward evaluation + export of the deployed model
python3 research/manh_knyc/train.py --data $DATA \
  --export lib/weather/manh-nowcast-model.json --fixtures tests/fixtures/manh-nowcast-gbm.json

# 4. Descriptive statistics and the TypeScript parity fixture
python3 research/manh_knyc/analysis.py --data $DATA
python3 research/manh_knyc/export_parity.py --data $DATA --day 2025-07-14 --out tests/fixtures/manh-nowcast-day.json
npm test
```

| File | Role |
|---|---|
| `fetch_data.py` | NYS Mesonet timeseries API, IEM METAR / 1-minute ASOS / CLI / NBM downloads |
| `load.py` | Parsers; KNYC T-group → whole °F, 6-hour max groups, LST climate day |
| `build.py` | Feature builders for the next-METAR and daily-high models (no look-ahead) |
| `train.py` | Train on 2021–2024, score 2025–2026, export compact LightGBM trees |
| `analysis.py` | Descriptive findings (`results/descriptive.json`) |
| `export_parity.py` | One-day replay so `tests/manh-nowcast.test.ts` proves TS == Python features |
| `lga_check.py` | Tests KLGA (1-minute history, live feed simulated as whole °C and 20–30 min late) as an extra signal; needs `fetch_data.py --only lga` |

`results/metrics.json` and `results/descriptive.json` hold the numbers quoted in the doc.

Retrain every few months (or after any NYSM/ASOS siting or sensor change): rerun steps 1–4 with
a later `--end` and commit the regenerated model, metrics and fixtures together.
