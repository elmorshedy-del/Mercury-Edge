# Review: hard-edge latency engineering + NYC Aug 20 research bookmark

**Review date:** 2026-08-21  
**Scope:** Mercury Edge hourly / 6-hour hard-elimination path, plus a preserved future-research case for New York on 2026-08-20.

This is a review snapshot, not an implementation specification. The observations below were checked against the current `main` branch and public source data on 2026-08-21, but the next engineer should independently re-read the current code and re-confirm source behavior before changing anything. Some suggestions are architectural judgments rather than facts; there is room to reject, refine, or replace them if measurement shows a better design.

---

## 1. What is already correct in Mercury

The core timestamp philosophy is mostly right.

Current `paper_collector/weather_collector.py` records three distinct clocks for AWC METAR data:

- `observed_at` from AWC `obsTime`
- `source_received_at` from AWC `receiptTime`
- `first_seen_at` from Mercury's own HTTP response-completion timestamp

It also records request start, response completion, and request RTT in the stored payload. This is the right general provenance model: observation time is not the same thing as the time Mercury could have known the information.

The current runner (`paper_collector/runner.py`) launches:

- the direct Kalshi WS collector (`collector.py`)
- AWC weather collector (`weather_collector.py`)
- IEM/MADIS OMO collector (`omo_collector.py`)
- rules collector
- `unified_engine.py`
- auditor/status processes

The Kalshi side is already architected much closer to a low-latency path than the AWC side. `paper_collector/collector.py` keeps an authenticated Kalshi WebSocket hot, journals orderbook/trade messages with wall-clock and monotonic nanosecond receive timestamps, and treats REST orderbook requests as audit cross-checks rather than the trading data path. That separation is worth preserving.

The hard-state paper logic also does not intentionally key a signal off the METAR's nominal observation timestamp. `paper_engine.py` stores the weather `first_seen_at` / receive epoch as the signal trigger, and `unified_engine.py` processes weather only after its configured simulated process delay. This is fundamentally the correct direction for a realizability test.

A separate good guardrail: live AWC rows are currently inserted with `compatibility_status='unverified'`, and the DBN hard-state path requires proven settlement compatibility before treating a signal as approved. That should not be weakened casually, especially after the Aug. 14 settlement-source change to The Weather Company.

The historical IEM high-frequency path is also deliberately conservative. `lib/sources/iem.ts` marks archived MADIS/HF points as `receiptQuality: "discovery_only"` because the historical IEM page does not preserve the original receipt timestamp, and marks them `settlementCompatible: false`. That means they are useful for research but not valid as proof of historical executable latency P&L.

Relevant current files reviewed:

- `paper_collector/weather_collector.py`
- `paper_collector/runner.py`
- `paper_collector/collector.py`
- `paper_collector/unified_engine.py`
- `paper_collector/paper_engine.py`
- `lib/sources/iem.ts`

---

## 2. Where the current hourly / 6-hour engineering is unnecessarily slow

### Fixed all-day AWC polling

`paper_collector/weather_collector.py` currently has:

```python
POLL_SECONDS = max(5.0, float(os.getenv("AWC_POLL_SECONDS", "10")))
```

and performs one request for the configured station list each cycle throughout the day.

For the hourly / 6-hour hard-edge strategy, this is not ideal. Routine METAR observation times are strongly scheduled by station (KNYC is commonly stamped around `:51`, for example), while the exact time at which the new report becomes available through AWC is not guaranteed to be exactly `:51:00`.

A fixed 10-second poll therefore creates a controllable detection delay after AWC has the report. If publication happens at a random phase relative to the polling loop, the added delay is roughly 0-10 seconds, about 5 seconds on average before HTTP RTT. On a stale-price opportunity that may last only tens of seconds or roughly two minutes, voluntarily losing several seconds is material.

This is the main latency-engineering issue discussed here. It is not a flaw in the hard-state logic itself.

### Paper process delay is deliberately large

The live runner currently uses `unified_engine.py`, which sets:

```python
DEFAULT_PROCESS_DELAY_MS = max(1000, int(os.getenv("PAPER_ENGINE_PROCESS_DELAY_MS", "6000")))
```

and later enforces:

```python
delay_ms = max(1000, int(global_cfg.get("paper_engine_process_delay_ms", DEFAULT_PROCESS_DELAY_MS)))
```

So the current default simulated processing delay is 6 seconds and the code floor is 1 second.

That may be a deliberately conservative paper assumption, but it should not be confused with measured Mercury-side latency. If the project is trying to learn whether a real bot can capture a 20-120 second stale window, it is useful to measure the actual software path separately from a conservative simulation parameter.

A reasonable research target discussed was sub-second Mercury-side handling (for example, a few hundred milliseconds), while retaining a 1-second conservative paper scenario until the real path is benchmarked. **This is a target/hypothesis, not an observed current latency.** The correct number should come from instrumentation rather than assumption.

---

## 3. Better polling model: scheduled hot windows, not continuous hammering

The routine hourly/6-hour use case does not need maximum-frequency polling for all 60 minutes of every hour.

The cleaner architecture is a station-specific release scheduler:

- outside the expected release window: low-frequency fallback polling (or another lightweight mechanism)
- shortly before the empirically expected release: enter a hot-poll window
- keep hot polling until the new report is actually seen
- immediately return that station to idle/fallback mode after the new report is captured

The exact window should be learned from real data rather than hard-coded from memory. For each station, record the distribution of:

`observed_at -> AWC receiptTime -> Mercury first_seen_at`

Then choose a hot window that covers the real publication distribution with margin.

This is preferable to assuming that a `:51` observation is guaranteed to appear at `:51:00`. The observation stamp and publication/availability time are different events.

A low-frequency fallback outside the hot window still has value because:

- routine issuance can drift
- special observations / SPECI can occur outside the expected schedule
- source behavior may change

The hot-poll design should also respect AWC usage/rate limits. The current review did **not** establish the highest safe polling frequency or whether AWC offers a better push/streaming mechanism for this exact feed. Those provider constraints should be checked before an aggressive implementation.

One current architectural detail to reconsider: `weather_collector.py` requests all configured stations in one `ids=` query. If release windows differ by station, it may be more efficient to hot-poll only the stations that are currently inside their release windows rather than hammering the full station universe every time.

---

## 4. What latency is controllable vs uncontrollable

Useful decomposition:

```text
ASOS measurement/report generation
    -> upstream/provider ingestion
    -> AWC data availability
    -> Mercury detects response
    -> Mercury parses hard state
    -> Mercury sends decision/order
    -> Kalshi receives it
```

The first upstream segment is mostly outside Mercury's control.

The following parts are controllable to varying degrees:

- source/feed choice
- polling vs streaming
- polling cadence
- whether connections are warm
- HTTP/network location
- parser/decision path
- DB involvement in the critical path
- Kalshi connection state
- signing/order construction

AWC `receiptTime` should be treated carefully: it is evidence of AWC/source receipt, but it should not automatically be relabeled as an exact public `publish_at` timestamp unless AWC documentation confirms that semantic. Mercury's own `first_seen_at` remains the strongest timestamp for when *this system* could actually know the report.

---

## 5. The fast path should be short; persistence can be asynchronous

For an eventual real execution path, the critical chain ideally looks like:

```text
new weather payload arrives
-> timestamp immediately
-> identify station/date/report
-> parse hard-state fields
-> evaluate bucket elimination
-> inspect already-hot Kalshi L2 state
-> construct/sign/send order
```

Database writes, dashboards, long REST calls, model calls, and retrospective analytics should not need to block this path.

The present weather architecture is still effectively:

```text
AWC HTTP response
-> parse
-> INSERT/COMMIT live_weather_journal
-> unified_engine polls DB
-> strategy processing
```

For paper research this is survivable because `first_seen_at` is captured before the DB work and historical execution can be modeled from that timestamp. But if the goal becomes "how fast could the live process actually react?", the database being the handoff between collector and strategy deserves review. A direct in-memory/event-queue handoff with asynchronous persistence may be cleaner, while still writing the exact same audit record.

This is an architectural suggestion, not a requirement. It should be benchmarked before changing a working system; if DB handoff proves comfortably fast relative to the edge, simplicity may be worth more than micro-optimization.

---

## 6. Hourly and 6-hour max are the same report path

There is no need to build a second network ingestion system for 6-hour maxima. The 6-hour maximum group is embedded in the relevant METAR report.

Example from KNYC on 2026-08-21 at 13:51 EDT:

```text
KNYC 211751Z AUTO VRB04KT 10SM BKN035 BKN060 24/18 A3009
RMK AO2 SLP180 T02440178 10250 20183 58005 $
```

`10250` is the 6-hour maximum group and represents +25.0 C, i.e. 77 F. That proved an intrahour 77 F had occurred even though the routine current-temperature value in the same report was 76 F.

So making routine METAR ingestion faster helps both:

- current/hourly hard threshold crossings
- 6-hour max hard confirmations

One engineering caution: `weather_collector.py` currently stores `max_temperature_f` from AWC's derived `maxT` JSON field. The raw METAR is also stored. For a fail-closed hard-elimination engine, it is worth validating that AWC `maxT` semantics are exactly the desired `1snTxTxTx` group in all relevant cases, and likely cross-checking/parsing the raw group directly instead of silently trusting one normalized field.

---

## 7. Timestamp set that should remain visible end-to-end

For every hard-state event, the replay/audit system should ideally preserve:

- `observed_at` — timestamp encoded/applied to the weather observation
- `source_received_at` — source/provider receipt timestamp when present
- `mercury_request_started_at`
- `mercury_first_seen_at` — first response that actually contains the new report
- `decision_at`
- `order_sent_at` (or simulated order-sent)
- `kalshi_ack_at` / exchange arrival if available in real execution
- Kalshi L2 receive timestamps around the same window
- first meaningful book mutation after the signal
- first aggressive sweep/trade cluster
- effectively repriced time

The most useful latency measures are then separable:

- upstream: `observed_at -> source_received_at`
- source/detection: `source_received_at -> mercury_first_seen_at`
- internal: `mercury_first_seen_at -> order_sent_at`
- market stale window: `mercury_first_seen_at -> sweep/reprice`

For historical cases where original receipt timestamps do not exist, do not substitute a discovery time and call it executable latency. Label observation-based latency as theoretical/estimated.

---

## 8. Hard-edge guardrails remain more important than speed

The current project is intentionally playing a hard-elimination game first, not a "predict the winning bucket" game.

Speed should not weaken these invariants:

- exact station identity
- canonical full calendar date (avoid DDHHMMZ month contamination)
- exact settlement-series/event mapping
- verified settlement-source bridge
- raw precision / rounding semantics
- bucket strike interpretation
- no hard trade from a merely predictive neighboring airport observation
- no hard trade from an archived high-frequency source with unknown receipt time
- fail closed when source/date/rounding/settlement compatibility is ambiguous

The 2026-08-21 investigation is a useful reminder: METAR text itself only contains day/hour/minute (`DDHHMMZ`), so stale web pages can accidentally look like the current month if the surrounding date provenance is ignored. The release explorer should therefore store/display the full canonical source date, not reconstruct context from the METAR token alone.

---

## 9. Release Explorer / Event Replay: observability that fits this engineering

A future UI discussed alongside this work would make these latency questions inspectable without manually reconstructing a day every time.

For any selected **city + date**, it would show:

- all relevant weather releases for that station/date
- release type (hourly METAR, 6-hour max-bearing METAR, SPECI, later rolling-5/MADIS, final climate)
- raw report and parsed hard-state fields
- observed/source-receipt/Mercury-receipt timestamps
- contemporaneous Kalshi bucket BBO/trades/L2
- first reaction, sweep start, and effective reprice
- latency slider (250 ms / 500 ms / 1 s / custom)
- simulated executable price and actual historical depth at order-arrival time
- VWAP/capacity and max theoretical net edge after fees
- evidence quality badge: full L2 replay vs minute reconstruction vs weather-only

This would be more useful than a single "lag" number. It should distinguish, for example:

1. first meaningful orderbook reaction
2. first aggressive sweep
3. effectively repriced (e.g. impossible YES down near 1-5c)

Maximum trade potential should come from the actual L2 book available at the simulated arrival time, not `edge x unlimited contracts`.

The same replay engine could later serve live paper audit and real-trade postmortems.

---

# Future research bookmark: NYC — 2026-08-20

## Why preserve this date

This is **not needed for the current hard-elimination strategy**. Preserve it as a later "winner prediction / temperature cap" research case.

The unresolved question is why the market already placed very little probability on NYC reaching 86 F or above before the 11:51 EDT report established that 84 F had been reached. This may eventually help a separate strategy that predicts which surviving bucket will win.

Do not mix this research with Phase 1 hard-edge P&L.

---

## Confirmed weather sequence

Date-specific KNYC observations were re-fetched from IEM on 2026-08-21.

Key rows (America/New_York):

- **09:51** — 81 F, CLR
- **10:51** — 82 F, `FEW120`, calm
- **11:51** — 84 F, wind `11003KT`, `FEW045 BKN055 BKN110`, raw T-group `T02890189`
- **12:51** — 79 F, `OVC100`
- **13:51** — 79 F, `-RA`, `FEW043 BKN100 OVC110`, and `10289` (6-hour max +28.9 C = 84 F)
- later afternoon temperatures remained below 84 F

Source used:

`https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py?station=NYC&data=tmpf&data=dwpf&data=drct&data=sknt&data=gust&data=vsby&data=skyc1&data=skyc2&data=skyc3&data=wxcodes&data=metar&year1=2026&month1=8&day1=20&year2=2026&month2=8&day2=21&tz=America%2FNew_York&format=onlycomma&latlon=no&elev=no&missing=M&trace=T&direct=no&report_type=3&report_type=4`

Kalshi's event/market metadata for that date states settlement was based on New York City (CLINYC) according to **The Weather Company**, not raw NWS METAR directly. The finalized upper-tail market (`T85`, 86 F or above) shows an expiration value of 84.00 F and resolved NO.

Useful metadata endpoints:

`https://external-api.kalshi.com/trade-api/v2/events/KXHIGHNY-26AUG20?with_nested_markets=true`

`https://external-api.kalshi.com/trade-api/v2/markets/KXHIGHNY-26AUG20-B84.5`

`https://external-api.kalshi.com/trade-api/v2/markets/KXHIGHNY-26AUG20-T85`

---

## Confirmed pre-sweep Kalshi state

One-minute public candlesticks immediately before the decisive trade-tape event show approximately:

### 84-85 F (`KXHIGHNY-26AUG20-B84.5`)

Candle ending **15:52Z / 11:52 EDT**:

- YES bid: 56c
- YES ask: 60c

### 82-83 F (`KXHIGHNY-26AUG20-B82.5`)

Candle ending **15:52Z / 11:52 EDT**:

- YES bid: 30c
- YES ask: 35c

### 86 F or above (`KXHIGHNY-26AUG20-T85`)

Candle ending **15:52Z / 11:52 EDT**:

- YES bid: 6c
- YES ask: 9c

This is the core interesting fact for later winner-prediction research: before the 84 F observation had been fully incorporated into prices, the market already treated a further rise to 86+ as a small tail probability.

Candlestick endpoints used:

`https://external-api.kalshi.com/trade-api/v2/series/KXHIGHNY/markets/KXHIGHNY-26AUG20-B84.5/candlesticks?start_ts=1787240940&end_ts=1787241300&period_interval=1`

`https://external-api.kalshi.com/trade-api/v2/series/KXHIGHNY/markets/KXHIGHNY-26AUG20-B82.5/candlesticks?start_ts=1787241000&end_ts=1787241240&period_interval=1`

`https://external-api.kalshi.com/trade-api/v2/series/KXHIGHNY/markets/KXHIGHNY-26AUG20-T85/candlesticks?start_ts=1787241000&end_ts=1787241420&period_interval=1`

Important limitation: one-minute candles prove minute-level BBO/history, not exact resting L2 size at an arbitrary millisecond. Do not use these candles to claim exact executable capacity.

---

## Confirmed trade-tape sweep timing

Public Kalshi trades show a large cross-bucket repricing cluster at approximately **15:53:02.84Z = 11:53:02.84 EDT**.

Examples from the fetched tape:

- `B82.5` has a mass sequence at **15:53:02.837988Z**, rapidly trading down through many YES prices and reaching YES 1c / NO 99c.
- `B84.5` shows the corresponding large repricing cluster beginning around **15:53:02.836698Z** and then trades into the 80s/90s YES.
- `T85` has a large cluster at **15:53:02.835629Z** across multiple prices.

So, using the nominal 11:51:00 observation stamp only, the public trade-tape sweep cluster is about **122.84 seconds later**.

Trade endpoints used:

`https://external-api.kalshi.com/trade-api/v2/markets/trades?ticker=KXHIGHNY-26AUG20-B82.5&min_ts=1787241120&max_ts=1787241360&limit=1000`

`https://external-api.kalshi.com/trade-api/v2/markets/trades?ticker=KXHIGHNY-26AUG20-B84.5&min_ts=1787241120&max_ts=1787241300&limit=500`

`https://external-api.kalshi.com/trade-api/v2/markets/trades?ticker=KXHIGHNY-26AUG20-T85&min_ts=1787241060&max_ts=1787241360&limit=500`

Again: this is **trade-tape sweep timing**, not proven first L2 reaction timing. A future replay with captured L2 could reveal order cancellations/book mutations slightly earlier than the first aggressive fills.

---

## A useful comparison clue with NYC 2026-08-21

The next day produced a strikingly similar public trade-tape timing.

At KNYC on 2026-08-21:

- 13:51 EDT METAR contained `10250`, proving a 6-hour maximum of 77 F.
- the <=76 F Kalshi market (`KXHIGHNY-26AUG21-T77`) had its mass sweep cluster at **17:53:02.256661Z / 13:53:02.256661 EDT**.

That is about **122.26 seconds after the nominal 13:51:00 observation stamp**.

The two nominal observation-to-sweep intervals are therefore within roughly 0.6 seconds of one another:

- Aug 20: ~122.84 s
- Aug 21: ~122.26 s

This is worth studying later because it looks more like a deterministic source-ingestion/polling cycle than independent humans slowly interpreting weather. Possible explanations include TWC update timing, another shared upstream feed, or sophisticated bots polling/acting on a common schedule. **No causal source has been proven yet.**

Aug 21 source data used:

`https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py?station=NYC&data=tmpf&data=metar&year1=2026&month1=8&day1=21&year2=2026&month2=8&day2=22&tz=America%2FNew_York&format=onlycomma&latlon=no&elev=no&missing=M&trace=T&direct=no&report_type=3&report_type=4`

`https://external-api.kalshi.com/trade-api/v2/markets/trades?ticker=KXHIGHNY-26AUG21-T77&min_ts=1787334660&max_ts=1787334900&limit=1000`

A future study should measure across many events:

`METAR observed_at -> AWC receiptTime -> Mercury first_seen -> TWC update (if observable) -> first Kalshi L2 mutation -> first sweep trade`

and test whether the reaction repeatedly clusters near a fixed offset such as ~122 seconds.

---

## Meteorological context already located for Aug 20 (preserve, but do not over-attribute causality)

NWS OKX Area Forecast Discussions from that morning clearly showed that a significant afternoon regime change was expected well before noon:

- **03:25 EDT AFD (07:25Z):** Flood Watch issued for NYC/NE NJ/Southern Westchester/western Long Island; cold front/frontal wave approaching; high PWAT (2+ inches); deep saturation in model soundings; heavy-rain setup; watch from 2 PM.
- **10:42 EDT AFD (14:42Z):** Flood Watch still in effect; confidence in heavy rainfall increased; cold front/frontal wave; deep saturation; showers/potential thunderstorms expected that afternoon/evening.
- **12:49 EDT AFD (16:49Z):** confidence in flash flooding increased further; WPC upgraded portions of NYC metro/NE NJ to moderate excessive-rainfall risk; heavy showers expected later afternoon/evening.

Stable IEM NWS-text archive query for the 10:42 and 12:49 products:

`https://mesonet.agron.iastate.edu/json/nwstext_search.py?awipsid=AFDOKX&sts=2026-08-20T13:00Z&ets=2026-08-20T18:00Z`

Full-day archive query (contains the earlier products including 03:25 EDT):

`https://mesonet.agron.iastate.edu/json/nwstext_search.py?awipsid=AFDOKX&sts=2026-08-20T00:00Z&ets=2026-08-21T00:00Z`

These meteorological facts make a temperature cap plausible, but they do **not** yet prove which variables or model products caused sophisticated traders to price 86+ at only ~6-9% before the 11:51 report. Avoid reducing the later study to a vague "cloud/wind regime" narrative.

The more rigorous later question is:

> What specific, machine-readable inputs available before 11:51 caused the market's upper-tail probability to be so low, and does that relationship repeat across many days/stations?

Candidate inputs to test later could include forecast/model distributions, solar/cloud/radar timing, short-term temperature tendency, dew point, wind shift, precipitation arrival, TWC nowcasts, NWS guidance, etc. They should be tested statistically rather than assumed to be causal.

---

## Why the Aug 20 hard-state repricing itself is simpler than the winner-prediction question

Once 84 F was observed, the 82-83 F bucket became impossible. The market did not need to know that 84 F would be the exact final high; it only needed to remove probability from states below the newly established maximum.

Immediately beforehand, approximately:

- 82-83: ~30-35%
- 84-85: ~56-60%
- 86+: ~6-9%

After the lower bucket became impossible, a large share of the removed probability naturally concentrated in 84-85 because the pre-existing upper-tail probability was already small. The observed post-sweep 84-85 pricing in the high-80s/low-90s is directionally consistent with simple conditional renormalization.

That is useful evidence for the hard-elimination strategy, but the reason the 86+ tail was already small belongs to a later, separate winner-prediction strategy.

---

## Suggested label for this preserved case

**NYC 2026-08-20 — Future Research / Winner-Prediction Case**

Primary unresolved question:

**Why was `P(max > 85 F)` already only about 6-9% before the 11:51 84 F observation was fully incorporated, and which machine-readable signals explain that confidence robustly across history?**

Secondary market-microstructure question:

**Why do Aug 20 and Aug 21 both show public trade-tape sweep clusters almost exactly ~122 seconds after the nominal `:51` KNYC observation stamp?**

Do not spend current Phase 1 effort solving this unless it directly helps hard-elimination execution. The source URLs and key timestamps above are preserved so the case can be reconstructed later without starting from zero.
