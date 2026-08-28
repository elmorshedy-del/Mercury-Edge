# TWC market-anchor event study

## Hypothesis

Mercury treats The Weather Company (TWC) forecast as the **candidate market anchor**, not because it is assumed to be the best meteorological forecast, but because Kalshi temperature contracts settle to TWC. The hypothesis is behavioral and must be tested:

> Traders begin from the TWC forecast distribution and revise it as observations and other model evidence diverge from TWC.

NBM, HRRR, RAP, NWS and other model products are therefore explanatory/diagnostic inputs. They must never silently replace the stored TWC anchor in the market model.

## Immutable anchor

For every station/date, preserve:

- latest TWC snapshot captured before local midnight
- TWC `calendarDayTemperatureMax`
- full TWC hourly temperature trajectory
- TWC hourly dew point
- cloud cover
- wind speed/direction
- precipitation probability/type
- UV/daylight fields when available
- capture timestamp and source identity

Once the target day begins, this record is immutable. Later TWC revisions are stored as separate snapshots and never overwrite the pre-day anchor.

## Three independent objects

Mercury must keep these separate:

1. **Anchor** — what TWC expected before the day.
2. **Atmospheric information shock** — how observations and independent models differ from that anchor.
3. **Market response** — how Kalshi order books/prices move after either a TWC revision or an atmospheric divergence.

No variable from object 2 or 3 is allowed to rewrite object 1.

## Anchor hypothesis test

Archive Kalshi best bid/ask, midpoint, depth and trade prices at high frequency together with every TWC/NBM/HRRR forecast capture.

### A. TWC revision event study

For every material TWC forecast revision at time `t`:

- delta in TWC calendar-day high
- delta in TWC hourly peak
- delta in individual future hourly values
- delta in dew point/cloud/wind/precip forecast
- Kalshi implied-distribution movement at `t + 1m`, `+5m`, `+15m`, `+30m`, `+60m`

Estimate how many cents / implied degrees the market moves per degree of TWC revision.

### B. Disagreement identification

Identify clean windows where one source changes while others do not.

**TWC-only move**
- TWC high or trajectory changes materially
- NBM/HRRR/NWS remain approximately unchanged
- measure subsequent Kalshi repricing

**Non-TWC-only move**
- NBM/HRRR/NWS changes materially
- TWC remains approximately unchanged
- measure subsequent Kalshi repricing

If Kalshi follows TWC-only changes more strongly/quickly, that is evidence for the anchor hypothesis. If not, Mercury must reduce the behavioral weight assigned to TWC.

### C. Cross-sectional disagreement days

At the start of each day record:

- TWC expected high
- NBM expected high
- HRRR expected high
- NWS forecast high
- Kalshi distribution center / expected value

Regress/compare Kalshi's initial center against the competing forecast sources. Do this by city and season because trader behavior may differ across markets.

Do not infer anchoring from one or two visually convincing examples.

## Atmospheric divergence model

Once the immutable TWC anchor is established, every live observation produces a vector of errors relative to TWC:

- `e_temp = observed temp - TWC temp`
- `e_heating = observed temperature tendency - TWC tendency`
- `e_dew = observed dew point - TWC dew point`
- cloud-cover difference
- wind-vector difference `(u,v)`
- precipitation/convection timing mismatch
- solar/short-wave discrepancy when available
- upwind temperature/dew-point gradient conditional on wind direction
- remaining daylight / solar elevation
- current observed maximum and six-hour maximum constraints

The statistical target is:

`E[T_future - TWC_future | information available now]`

and ultimately the distribution of the final daily maximum.

## Role of NBM and HRRR

NBM/HRRR are **not market anchors** in this design. They answer questions such as:

- Is TWC's cloud forecast likely wrong?
- Is a sea-breeze/front arriving earlier than TWC expected?
- Is the incoming air mass warmer/cooler/drier/moister than TWC forecast?
- Is observed weak heating temporary because a cloud break is imminent?
- Is a temperature residual likely to persist or reset?

HRRR is especially useful for rapid regime transitions because it is hourly updated, ~3-km resolution, convection allowing and radar assimilating. NOAA's open HRRR archive extends back to 2014, making it suitable for physical-prior training.

NBM provides a calibrated multi-model benchmark and can help distinguish a TWC-specific miss from broad model consensus.

## Adaptive future

Do not mechanically add the latest temperature residual to every remaining TWC hour.

Use two layers:

1. **Sequential bias state** — Kalman/adaptive MOS correction estimated from the sequence of observed TWC residuals.
2. **Mechanism-conditioned correction** — historically trained effects for radiation/cloud, moisture, wind/advection and precipitation regime errors.

Mechanism coefficients remain zero until they improve out-of-sample walk-forward validation.

Examples:

- TWC too warm + unexpected persistent cloud + weak observed heating + HRRR keeps cloud -> negative residual likely persists/grows.
- TWC too cool + unexpected clearing + strong heating + several daylight hours remain -> positive residual may grow.
- TWC too warm but HRRR shows imminent clearing -> do not extrapolate the current cool residual unchanged.
- TWC trajectory miss coincides with sharp wind-vector and dew-point transition -> treat as air-mass/advection regime change; old residual persistence should drop.
- Rain/thunder begins unexpectedly -> regime break; do not extrapolate pre-convective slope.

## Validation

Use chronological rolling-origin / expanding-window validation only.

Compare:

1. raw TWC
2. latest-residual persistence
3. Kalman-only
4. Kalman + NBM/HRRR state variables
5. full mechanism-conditioned model

Metrics:

- hourly MAE/RMSE by lead time
- final-high absolute error
- peak-time error
- Brier scores for `P(+1°F)`, `P(+2°F)`, `P(max already occurred)`
- CRPS for full maximum distribution
- Kalshi bucket log loss / Brier score
- simulated market edge after realistic spread/fees/latency

Run feature-family ablations. A variable family is retained only when it improves future, not in-sample, performance.

## Required outputs in Mercury

For each station show:

- **TWC pre-day anchor high**
- original TWC hourly path
- actual hourly METAR path
- current TWC residual
- dominant divergence diagnosis with evidence
- Kalman adaptive path
- mechanism-conditioned path once validated
- `P(max already occurred)`
- `P(+1°F)` / `P(+2°F)`
- expected final maximum and interval
- Kalshi expected maximum / bucket probabilities
- edge: model probability minus market probability
- whether TWC itself has revised since the immutable pre-day anchor

## Rule

The core distinction is permanent:

**TWC = candidate market prior.**

**Observations + HRRR/NBM = evidence used to update that prior.**

**Kalshi = market's own posterior, which Mercury compares against its independently computed posterior.**
