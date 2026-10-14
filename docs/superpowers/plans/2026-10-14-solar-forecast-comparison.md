# Solar forecast comparison implementation plan

Goal: Compare observed satellite sunlight with a real pre-day weather forecast in all five cities.

Architecture: Use NOAA HRRR 00 UTC of the target local calendar date (the previous local evening). Extract instantaneous surface DSWRF from its hourly GRIB2 fields via ecCodes, preserve original publication/run/capture timestamps in Postgres, and interpolate only between adjacent hourly forecasts. Keep live satellite polling independent of forecast loading. The HRRR benchmark is separate from the TWC temperature product.

Constraints: Same physical quantity, W/m² at a horizontal surface; no conversion from cloud percentage or UV. No revised forecasts, extrapolation, averaging/instantaneous mixing, or model switching. Reject objects first published during the local target day. Gaps remain gaps. The comparison band is descriptive, not calibrated forecast uncertainty or a temperature/trading signal.

- [x] Test and implement run eligibility, exact-time matching, gaps, radiation quality and signed deviations in `lib/weather/solar-forecast-model.ts` / `tests/solar-forecast.test.ts`.
- [x] Extract bounded HTTP byte ranges, validate GRIB run/valid-time/units/instantaneous metadata, persist immutable points in `lib/weather/solar-forecast-source.ts` and SQL migration; expose nonblocking `app/api/weather-dashboard/solar/forecast/route.ts`. Test malformed/average metadata and partial source failures. Install stable ecCodes in Docker runtime.
- [x] Add a source-labeled forecast line, time-axis deviation replay, color/explanation readout, publication provenance and missing-data states to existing gauge/timeline. Preserve 5s observed updates and pinned replay behavior.
- [ ] Run real NOAA extraction, full `npm run check`, independent code review, deploy with a main ref lease, and verify public API/UI in all five cities.

Review focus: late-issued objects; partial hourly coverage; run changes at local midnight; stale observed data and low sun; gaps/invalid pixels; forecast-vs-temperature-provider ambiguity; native decoder availability and bounded downloads.

Validation: initial check 61/61, additional RED→GREEN cases cover read fallback, pending persistence, ecCodes version alias and rejected whole-file response cancellation. Exact Debian2.28 decoder tested against a real NOAA field for all five station neighborhoods. Review found read/write outage recovery issues; both addressed.
