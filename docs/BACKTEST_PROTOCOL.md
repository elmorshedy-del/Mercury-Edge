# Cross-city backtest protocol

This protocol keeps operational latency research separate from meteorological forecasting. The former can be tested with clocks and contract logic; the latter needs a leakage-free, regime-aware model.

## 1. Freeze the research questions

Run three independent studies rather than pooling them into one attractive headline.

### A. Market repricing latency

Question: after a settlement-compatible report is publicly received, how long does an already-impossible bracket retain a nontrivial price?

Primary outcomes:

- source receipt to first quote;
- source receipt to `yes_ask ≤ 0.05`;
- executable-proxy count and percentage;
- gross and fee-adjusted profit for a fixed one-contract rule;
- sensitivity to 30, 60, and 90-second quote tolerances.

Climate change has little direct bearing on this mechanical study. Source infrastructure, market participation, city, day of week, market age, and time of day are the important regimes.

### B. Publication latency

Question: how long after the underlying high did a DSM or CLI reveal it, and how quickly did the market react after publication?

Never collapse these into one duration:

- occurrence → official product issue;
- product issue → local ingestion;
- product issue → market response.

This study identifies hidden intrahour-high windows, but does not claim the high was tradable before a verifiable public source exposed it.

### C. Trajectory mispricing

Question: given only data public at an alert cutoff, was the market distribution miscalibrated relative to a meteorological forecast distribution?

The first candidate model should be transparent:

- operational forecast blend as a prior;
- observed temperature residual to the same forecast vintage;
- 30-minute, 1-hour, 2-hour, and 3-hour slopes;
- slope decay and time since the running high;
- dew point, pressure, wind vector, cloud, and precipitation changes;
- solar elevation, remaining daylight, and estimated time-to-peak;
- upwind gradients only when direction, persistence, distance, and coupling gates pass.

Do not use market prices as a weather-model feature. Compare the independently produced weather posterior to the market only after the posterior is frozen.

## 2. City-day panel

The initial universe is the 20 official stations in `lib/config.ts`. Keep each market attached to its exact settlement station; never substitute a nearby airport or city sensor.

For every city-day, retain:

- raw report and product identifier;
- observation, source receipt, ingestion, and quote timestamps;
- source precision and receipt-quality class;
- all contract bands, results, and quote candles;
- final official outcome and product vintage;
- data gaps and provider failures.

Deduplicate raw records without collapsing distinct timestamps. A backfill is a new discovery, not a rewrite of an earlier snapshot.

## 3. No-lookahead construction

At each cutoff, build features from records with `received_at <= cutoff`. A product observed earlier but issued later cannot enter earlier features. Forecast residuals must compare against a model cycle that was itself available by the cutoff.

Use expanding-window evaluation:

1. train through year `Y-1`;
2. calibrate only with completed days;
3. predict every eligible cutoff in year `Y`;
4. advance to the next year without refitting on future outcomes.

Hyperparameters are selected inside the training window. Keep a final recent period untouched until model design is frozen.

## 4. Climate-regime change

A 2005 observation is not automatically interchangeable with a 2026 observation. Run at least four trajectory-model views:

- recent era: 2016 onward;
- full era: 2005 onward with year and temperature-anomaly controls;
- rolling 10-year window;
- exponentially weighted history with the half-life selected inside training folds.

Control for month, alert time, cloud regime, wind regime, recent precipitation, and standardized daily anomaly. Report whether coefficients and calibration drift across eras. Older data can still improve rare-regime estimates, but sparse groups should shrink toward recent full-sample behavior.

Latency models should instead stratify by platform/source era because API and market microstructure changes dominate climate trends.

## 5. Metrics and uncertainty

Forecast quality:

- exact-degree and two-degree-bracket Brier score;
- multiclass log loss;
- calibration error and reliability plots;
- maximum-temperature mean absolute error (MAE) and root mean squared error (RMSE);
- peak-time MAE;
- coverage of 50% and 80% intervals.

Trading screen quality:

- number of independent city-days;
- win rate with Wilson interval;
- mean and median net return with city-day block bootstrap intervals;
- worst drawdown under a fixed sizing rule;
- results after fees and conservative slippage;
- results by city, alert time, liquidity, and evidence tier.

Cluster resampling by city-day so multiple brackets from one weather event do not masquerade as independent wins. Publish every predeclared threshold sensitivity, not only the best-looking one.

## 6. Promotion gates

A candidate becomes a live paper signal only if it:

1. improves proper out-of-sample scores over the operational prior or fixed mechanical baseline;
2. remains positive after conservative costs;
3. survives exclusion of its best city and best month;
4. has enough independent city-days for a useful confidence interval;
5. passes a timestamp audit on a random raw-record sample;
6. is frozen before the paper-trading period starts.

One striking day creates a case study and a testable hypothesis—not a coefficient change.
