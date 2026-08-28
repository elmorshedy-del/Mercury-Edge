# TWC adaptive trajectory protocol

## Objective

Mercury's trajectory should answer two separate questions:

1. What did The Weather Company (TWC) forecast for the station, hour by hour?
2. Given what has actually happened so far, what is the best defensible distribution for the remaining temperature path and daily maximum?

The second question must not be answered by mechanically shifting the original curve. A temperature miss can have different causes with different persistence: cloud/radiation, moisture, wind/advection, precipitation/convection, or a station/report anomaly.

## Baseline and archive

- Primary forecast baseline: TWC Hourly Forecast (`/v3/wx/forecast/hourly/2day`) at the exact station coordinates.
- Persist the first TWC curve captured for each local day as the displayed `original forecast`.
- Persist a complete TWC forecast snapshot at 15-minute buckets throughout the day. This creates an exact revision archive and also preserves the previous day's forecast for the following day.
- Verification observations: raw METAR/SPECI T-group temperature and dew point, wind vector, cloud amount parsed from METAR, precipitation/weather codes, six-hour maxima, and high-frequency station observations where available.
- Do not treat NWS forecasts as a surrogate for TWC forecast history. NWS can remain an independent comparison/research feature, but it is not the trajectory baseline for a TWC-settled market.

TWC documents the hourly forecast as coming from its own Forecast system and exposes temperature, dew point, cloud cover, wind, precipitation, and related fields. TWC also documents that Forecast On Demand blends observations, high-resolution analyses/models, radar, soundings and other inputs.

References:
- https://developer.weather.com/docs/openapi/hourly-forecast-3-0
- https://developer.weather.com/docs/faqs

## Scientific model

### Stage 1 — sequential bias state

Use a station- and clock-hour-aware state-space correction (Kalman / adaptive MOS) rather than an arbitrary residual shift. The state is the current TWC temperature bias. Each new routine observation updates the bias estimate and its uncertainty. Lead-time persistence is estimated from historical residual autocorrelation and allowed to vary by station, season, local hour, and regime.

This follows established surface-temperature post-processing literature: adaptive Kalman filters have repeatedly reduced 2-m temperature bias, and lead-time/diurnal Kalman corrections outperform static corrections in many settings.

References:
- Homleid (1995), *Diurnal Corrections of Short-Term Surface Temperature Forecasts Using the Kalman Filter*, Weather and Forecasting. https://doi.org/10.1175/1520-0434(1995)010%3C0689:DCOSTS%3E2.0.CO;2
- Libonati, Trigo & DaCamara (2008), *Correction of 2 m-temperature forecasts using Kalman Filtering technique*, Atmospheric Research. https://doi.org/10.1016/j.atmosres.2007.08.006
- Local temperature forecasts based on statistical post-processing of NWP data (2021). https://doi.org/10.1002/met.2006

### Stage 2 — mechanism-conditioned residual model

For every observation time `t`, calculate errors relative to the TWC forecast valid at `t`:

- `e_T`: temperature error
- `d(e_T)/dt`: heating/cooling-rate error over the latest 1–3 h
- `e_Td`: dew-point error
- `e_cloud`: observed minus forecast total-cloud fraction
- `e_u`, `e_v`: wind-vector errors (never subtract direction angles directly)
- precipitation mismatch: observed rain/thunder vs forecast probability/type
- solar forcing: TWC UV / day-night field plus solar elevation; later add measured/analysis global horizontal irradiance when available
- time since observed daily high and remaining solar runway
- six-hour maximum constraint
- upwind thermal-gradient/advection features, gated by a coherent wind vector

Predict the future TWC residual separately at +1, +2, +3, +4 and +6 h. Use a regularized dynamic regression/GAM or ridge model first; only move to tree boosting after a walk-forward comparison shows a real gain. The sequential Kalman state is an input and a fallback, not a competing unvalidated hand rule.

### Mechanism diagnosis

The mechanism label is diagnostic evidence, not a causal claim by itself.

**Radiative/cloud miss**
- daylight / meaningful UV
- cloud-cover error and temperature heating-rate error have physically consistent signs
- unexpected extra cloud -> less short-wave energy -> less remaining daytime heating
- unexpected clearing -> more solar heating runway

Cloud/radiation errors are a documented major source of 2-m temperature error. Studies have directly linked cloud-cover errors to surface-temperature errors and surface short-wave radiation errors.

References:
- Van Weverberg et al. (2015), *Using regime analysis to identify the contribution of clouds to surface temperature errors in weather and climate models*. https://doi.org/10.1002/qj.2603
- Ma et al. (2018), *On the Role of Surface Energy Budget Errors to the Warm Surface Air Temperature Error over the Central United States*. https://doi.org/10.1002/2017JD027194
- Patel et al. (2021), *The Diurnal Cycle of Winter Season Temperature Errors in the Operational Global Forecast System*. https://doi.org/10.1029/2021GL095101

**Moisture / latent-heat miss**
- meaningful dew-point error and/or unexpected precipitation
- evaluate together with heating-rate error and cloud/radiation, not dew point alone
- a moister-than-forecast boundary layer can change the partition between sensible and latent heat; it is supporting evidence, not a fixed degree-for-degree temperature adjustment

**Advection / breeze / frontal miss**
- wind-vector error, direction transition, or speed error
- coherent upwind temperature/dew-point gradient
- abrupt changes should shorten persistence of the previous local temperature bias; a stable coherent wind regime can transport the upwind anomaly into later hours

Near-surface forecast errors are strongly regime- and flow-dependent, particularly when boundary-layer forcing changes.

Reference:
- Pu et al. (2013), *Examination of Errors in Near-Surface Temperature and Wind from WRF Numerical Simulations in Regions of Complex Terrain*. https://doi.org/10.1175/WAF-D-12-00109.1

**Convection / precipitation transition**
- rain/thunder begins earlier or later than forecast
- temperature/dew-point/cloud changes occur together
- treat this as a regime break; do not extrapolate the pre-storm slope through it

**Observation anomaly**
- isolated temperature jump with no physically consistent dew-point, wind, cloud, radiation, or neighboring-station support
- quarantine for the adaptive model until confirmed by a subsequent observation/max product

## Historical training data

There are two different historical problems and they must not be mixed:

1. **Exact historical TWC forecast revisions.** Public TWC documentation provides current forecasts and historical *conditions*, but not a simple public archive of every past point forecast/revision. Mercury therefore starts its own 15-minute TWC snapshot archive now. Do not invent historical TWC trajectories from later observations.
2. **Physical prior / mechanism training.** Backfill years of archived HRRR/RAP/NBM forecasts plus METAR observations for the same stations. This gives a large, timestamp-clean hindcast set for learning how cloud, dew point, wind and heating-rate errors map into future temperature residuals. Those coefficients are a prior only; station-specific TWC coefficients are updated as Mercury's exact TWC archive grows.

TWC History on Demand is useful for historical analyzed conditions (including temperature, dew point, wind and global horizontal irradiance), not as a substitute for archived point forecasts:
- https://developer.weather.com/docs/history-on-demand-package

## Validation protocol

No random train/test split.

- expanding-window or rolling-origin validation by date
- preserve issuance time; no feature may use data that arrived after the simulated decision time
- separate seasons and local clock hours
- evaluate raw TWC, simple latest-residual persistence, Kalman-only, and mechanism-conditioned model
- hourly metrics: MAE, RMSE, bias by lead time
- daily-max metrics: absolute max error, peak-time error, probability calibration for +1°F / +2°F and Kalshi buckets
- probabilistic metrics: Brier score and CRPS where a distribution is produced
- feature ablation: remove cloud, dew point, wind/advection, precipitation one family at a time
- deploy a mechanism coefficient only when it improves out-of-sample walk-forward performance and keeps the physically expected sign in the relevant regime

## Daily maximum posterior

The final output should be a distribution, not only a single adaptive line:

- simulate correlated future hourly residual paths from the calibrated lead-time error covariance
- add them to the TWC hourly curve
- impose hard observed constraints (current max and official six-hour maxima)
- compute `P(max already occurred)`, `P(+1°F)`, `P(+2°F)`, peak-time distribution, expected max, and Kalshi bucket probabilities

This prevents a visually plausible mean curve from being mistaken for a high-confidence maximum forecast.

## Deployment rule

The current implementation may use the sequential Kalman bias correction immediately because it is an established adaptive post-processing method. Mechanism diagnostics may be displayed immediately. **Mechanism-specific numerical coefficients stay at zero until the walk-forward historical validation above estimates and validates them.** This is deliberate: it prevents post-hoc stories such as “dew point caused it” from becoming untested trading logic.
