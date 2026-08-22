# Review: hard-edge latency engineering

**Review date:** 2026-08-21  
**Scope:** Mercury Edge hourly / 6-hour hard-elimination path.

This is a review snapshot, not an implementation specification. The observations below were checked against the current `main` branch on 2026-08-21. Before changing anything, the current code, provider behavior, assumptions, and measurements should be independently re-read and re-confirmed. Some points below are architectural judgments rather than facts; there is room to reject, refine, or replace them if measurement supports a better design.

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

The historical IEM high-frequency path is also deliberately conservative. `lib/sources/iem.ts` marks archived MADIS/HF points as `receiptQuality: "discovery_only"` because the historical IEM page does not preserve the original receipt timestamp, and marks them `settlementCompatible: false`. That makes them useful for research but not valid as proof of historical executable latency P&L.

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

A fixed 10-second poll therefore creates a controllable detection delay after AWC has the report. If publication happens at a random phase relative to the polling loop, the polling component alone adds roughly 0-10 seconds, about 5 seconds on average, plus HTTP/API effects. On a stale-price opportunity that may last only tens of seconds or roughly two minutes, voluntarily losing several seconds is material.

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

That may be a deliberately conservative paper assumption, but it should not be confused with measured Mercury-side latency. If the project is trying to learn whether a real bot can capture a 20-120 second stale window, the actual software path should be measured separately from a conservative simulation parameter.

A reasonable research target discussed was sub-second Mercury-side handling (for example, a few hundred milliseconds), while retaining a 1-second conservative paper scenario until the real path is benchmarked. **This is a target/hypothesis, not an observed current latency.** The correct number should come from instrumentation.

---

## 3. Better polling model: scheduled hot windows, not continuous hammering

The routine hourly/6-hour use case does not need maximum-frequency polling for all 60 minutes of every hour.

A cleaner architecture is a station-specific release scheduler:

- outside the expected release window: low-frequency fallback polling or another lightweight mechanism
- shortly before the empirically expected release: enter a hot-poll window
- keep hot polling until the new report is actually seen
- immediately return that station to idle/fallback mode after capture

The exact window should be learned from real data rather than hard-coded from memory. For each station, record the distribution of:

`observed_at -> AWC receiptTime -> Mercury first_seen_at`

Then choose a hot window that covers the real publication distribution with margin.

This is preferable to assuming that a `:51` observation is guaranteed to appear at `:51:00`. The observation stamp and publication/availability time are different events.

A low-frequency fallback outside the hot window still has value because:

- routine issuance can drift
- special observations / SPECI can occur outside the expected schedule
- source behavior may change

The hot-poll design should also respect AWC usage/rate limits. This review did **not** establish the highest safe polling frequency or whether AWC offers a better push/streaming mechanism for this exact feed. Provider constraints should be rechecked before aggressive polling is implemented.

One current architectural detail to reconsider: `weather_collector.py` requests all configured stations in one `ids=` query. If release windows differ by station, it may be more efficient to hot-poll only stations currently inside their release windows rather than the entire station universe every time.

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

One engineering caution: `weather_collector.py` currently stores `max_temperature_f` from AWC's derived `maxT` JSON field. The raw METAR is also stored. For a fail-closed hard-elimination engine, validate that AWC `maxT` semantics are exactly the desired `1snTxTxTx` group in all relevant cases, and consider parsing/cross-checking the raw group directly rather than silently trusting one normalized field.

---

## 7. Timestamp set that should remain visible end-to-end

For every hard-state event, the replay/audit system should ideally preserve:

- `observed_at` — timestamp encoded/applied to the weather observation
- `source_received_at` — source/provider receipt timestamp when present
- `mercury_request_started_at`
- `mercury_first_seen_at` — first response that actually contains the new report
- `decision_at`
- `order_sent_at` or simulated order-sent
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
- canonical full calendar date; avoid `DDHHMMZ` month contamination
- exact settlement-series/event mapping
- verified settlement-source bridge
- raw precision / rounding semantics
- bucket strike interpretation
- no hard trade from a merely predictive neighboring airport observation
- no hard trade from an archived high-frequency source with unknown receipt time
- fail closed when source/date/rounding/settlement compatibility is ambiguous

The 2026-08-21 investigation is a useful reminder: METAR text itself only contains day/hour/minute (`DDHHMMZ`), so stale web pages can accidentally look like the current month if surrounding date provenance is ignored. The release explorer should therefore store/display the full canonical source date, not reconstruct context from the METAR token alone.

---

## 9. Release Explorer / Event Replay: observability that fits this engineering

A future UI discussed alongside this work would make these latency questions inspectable without manually reconstructing a day every time.

For any selected **city + date**, it would show:

- all relevant weather releases for that station/date
- release type: hourly METAR, 6-hour-max-bearing METAR, SPECI, later rolling-5/MADIS, final climate
- raw report and parsed hard-state fields
- observed/source-receipt/Mercury-receipt timestamps
- contemporaneous Kalshi bucket BBO/trades/L2
- first reaction, sweep start, and effective reprice
- latency slider such as 250 ms / 500 ms / 1 s / custom
- simulated executable price and actual historical depth at order-arrival time
- VWAP/capacity and max theoretical net edge after fees
- evidence-quality badge: full L2 replay vs minute reconstruction vs weather-only

This would be more useful than a single "lag" number. It should distinguish, for example:

1. first meaningful orderbook reaction
2. first aggressive sweep
3. effectively repriced, e.g. impossible YES down near 1-5c

Maximum trade potential should come from the actual L2 book available at the simulated arrival time, not `edge x unlimited contracts`.

The same replay engine could later serve live-paper audit and real-trade postmortems.
