# General Research Ideas

A shared notebook for future Mercury Edge research questions, anomalies, and cases worth revisiting. Any AI or engineer working on the repository can add new ideas here when something appears potentially valuable but is outside the current implementation priority.

Items in this file are **research bookmarks, not established strategy rules**. Before using any item operationally, independently re-confirm the cited data, source semantics, timestamps, market rules, and any causal interpretation.

---

## NYC — 2026-08-20: winner-prediction / temperature-cap case

### Why preserve this date

This case is **not required for the current hard-elimination strategy**. It is worth revisiting later for a separate winner-prediction strategy.

The unresolved question is why the market already placed very little probability on NYC reaching 86 F or above before the 11:51 EDT report established that 84 F had been reached.

The later research question is not merely whether clouds/rain/wind were present. It is:

> What specific, machine-readable information available before 11:51 caused the market to assign such a small upper-tail probability, and does the same relationship repeat across many dates and stations?

Do not mix this research with current Phase 1 hard-edge P&L.

---

### Confirmed weather sequence

Date-specific KNYC observations were re-fetched from IEM on 2026-08-21.

Key rows in America/New_York time:

- **09:51** — 81 F, `CLR`
- **10:51** — 82 F, `FEW120`, calm
- **11:51** — 84 F, wind `11003KT`, `FEW045 BKN055 BKN110`, T-group `T02890189`
- **12:51** — 79 F, `OVC100`
- **13:51** — 79 F, `-RA`, `FEW043 BKN100 OVC110`, and `10289` (6-hour max +28.9 C = 84 F)
- later afternoon observations remained below 84 F

IEM source used:

`https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py?station=NYC&data=tmpf&data=dwpf&data=drct&data=sknt&data=gust&data=vsby&data=skyc1&data=skyc2&data=skyc3&data=wxcodes&data=metar&year1=2026&month1=8&day1=20&year2=2026&month2=8&day2=21&tz=America%2FNew_York&format=onlycomma&latlon=no&elev=no&missing=M&trace=T&direct=no&report_type=3&report_type=4`

Kalshi metadata for the date states that settlement was based on New York City (`CLINYC`) according to **The Weather Company**, not directly on raw NWS METAR. The finalized `T85` upper-tail market (86 F or above) shows an expiration value of 84.00 F and resolved NO.

Useful metadata endpoints:

`https://external-api.kalshi.com/trade-api/v2/events/KXHIGHNY-26AUG20?with_nested_markets=true`

`https://external-api.kalshi.com/trade-api/v2/markets/KXHIGHNY-26AUG20-B84.5`

`https://external-api.kalshi.com/trade-api/v2/markets/KXHIGHNY-26AUG20-T85`

---

### Confirmed pre-sweep Kalshi state

One-minute public candlesticks immediately before the decisive trade-tape event show approximately:

#### 84-85 F — `KXHIGHNY-26AUG20-B84.5`

Candle ending **15:52Z / 11:52 EDT**:

- YES bid: 56c
- YES ask: 60c

#### 82-83 F — `KXHIGHNY-26AUG20-B82.5`

Candle ending **15:52Z / 11:52 EDT**:

- YES bid: 30c
- YES ask: 35c

#### 86 F or above — `KXHIGHNY-26AUG20-T85`

Candle ending **15:52Z / 11:52 EDT**:

- YES bid: 6c
- YES ask: 9c

This is the main interesting fact for later winner-prediction research: before the 84 F observation had been fully incorporated into prices, the market already treated a further rise to 86+ as a small tail probability.

Candlestick endpoints used:

`https://external-api.kalshi.com/trade-api/v2/series/KXHIGHNY/markets/KXHIGHNY-26AUG20-B84.5/candlesticks?start_ts=1787240940&end_ts=1787241300&period_interval=1`

`https://external-api.kalshi.com/trade-api/v2/series/KXHIGHNY/markets/KXHIGHNY-26AUG20-B82.5/candlesticks?start_ts=1787241000&end_ts=1787241240&period_interval=1`

`https://external-api.kalshi.com/trade-api/v2/series/KXHIGHNY/markets/KXHIGHNY-26AUG20-T85/candlesticks?start_ts=1787241000&end_ts=1787241420&period_interval=1`

Important limitation: one-minute candles prove minute-level BBO/history, not exact resting L2 size at an arbitrary millisecond. They should not be used to claim exact executable capacity.

---

### Confirmed trade-tape sweep timing

Public Kalshi trades show a large cross-bucket repricing cluster at approximately **15:53:02.84Z = 11:53:02.84 EDT**.

Examples from the fetched tape:

- `B82.5` has a mass sequence at **15:53:02.837988Z**, rapidly trading down through many YES prices and reaching YES 1c / NO 99c.
- `B84.5` shows the corresponding large repricing cluster beginning around **15:53:02.836698Z** and then trades into the 80s/90s YES.
- `T85` has a large cluster at **15:53:02.835629Z** across multiple prices.

Using only the nominal 11:51:00 observation stamp, the public trade-tape sweep cluster is about **122.84 seconds later**.

Trade endpoints used:

`https://external-api.kalshi.com/trade-api/v2/markets/trades?ticker=KXHIGHNY-26AUG20-B82.5&min_ts=1787241120&max_ts=1787241360&limit=1000`

`https://external-api.kalshi.com/trade-api/v2/markets/trades?ticker=KXHIGHNY-26AUG20-B84.5&min_ts=1787241120&max_ts=1787241300&limit=500`

`https://external-api.kalshi.com/trade-api/v2/markets/trades?ticker=KXHIGHNY-26AUG20-T85&min_ts=1787241060&max_ts=1787241360&limit=500`

This is **trade-tape sweep timing**, not proven first-L2-reaction timing. Captured L2 could reveal cancellations/book mutations slightly earlier than the first aggressive fills.

---

### Comparison clue: NYC 2026-08-21

The next day produced a strikingly similar public trade-tape timing.

At KNYC on 2026-08-21:

- the 13:51 EDT METAR contained `10250`, proving a 6-hour maximum of 77 F;
- the <=76 F Kalshi market (`KXHIGHNY-26AUG21-T77`) had its mass sweep cluster at **17:53:02.256661Z / 13:53:02.256661 EDT**.

That is about **122.26 seconds after the nominal 13:51:00 observation stamp**.

The two nominal observation-to-sweep intervals are therefore within roughly 0.6 seconds of one another:

- Aug 20: ~122.84 s
- Aug 21: ~122.26 s

This deserves later study because it looks more consistent with a deterministic source-ingestion/polling cycle than with independent humans gradually interpreting weather. Possible explanations include TWC update timing, another common upstream feed, or sophisticated bots acting on the same schedule. **No causal source has been proven.**

Aug 21 source data used:

`https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py?station=NYC&data=tmpf&data=metar&year1=2026&month1=8&day1=21&year2=2026&month2=8&day2=22&tz=America%2FNew_York&format=onlycomma&latlon=no&elev=no&missing=M&trace=T&direct=no&report_type=3&report_type=4`

`https://external-api.kalshi.com/trade-api/v2/markets/trades?ticker=KXHIGHNY-26AUG21-T77&min_ts=1787334660&max_ts=1787334900&limit=1000`

A later study should measure across many events:

`METAR observed_at -> AWC receiptTime -> Mercury first_seen -> TWC update (if observable) -> first Kalshi L2 mutation -> first sweep trade`

and test whether reactions repeatedly cluster near a fixed offset such as ~122 seconds.

---

### Meteorological context already located for Aug 20

Preserve this context, but do not over-attribute causality to it.

NWS OKX Area Forecast Discussions from that morning clearly showed that a significant afternoon regime change was expected well before noon:

- **03:25 EDT AFD (07:25Z):** Flood Watch issued for NYC/NE NJ/Southern Westchester/western Long Island; cold front/frontal wave approaching; high PWAT (2+ inches); deep saturation in model soundings; heavy-rain setup; watch from 2 PM.
- **10:42 EDT AFD (14:42Z):** Flood Watch still in effect; confidence in heavy rainfall increased; cold front/frontal wave; deep saturation; showers/potential thunderstorms expected that afternoon/evening.
- **12:49 EDT AFD (16:49Z):** confidence in flash flooding increased further; WPC upgraded portions of NYC metro/NE NJ to moderate excessive-rainfall risk; heavy showers expected later afternoon/evening.

Stable IEM NWS-text archive query for the 10:42 and 12:49 products:

`https://mesonet.agron.iastate.edu/json/nwstext_search.py?awipsid=AFDOKX&sts=2026-08-20T13:00Z&ets=2026-08-20T18:00Z`

Full-day archive query, including the earlier products such as 03:25 EDT:

`https://mesonet.agron.iastate.edu/json/nwstext_search.py?awipsid=AFDOKX&sts=2026-08-20T00:00Z&ets=2026-08-21T00:00Z`

These meteorological facts make a temperature cap plausible, but they do **not** prove which variables or model products caused sophisticated traders to price 86+ at only ~6-9% before the 11:51 report. Avoid reducing the later study to a vague "cloud/wind regime" narrative.

Candidate machine-readable inputs to test later include:

- forecast/model probability distributions
- short-term temperature tendency
- solar radiation / cloud timing
- radar and precipitation arrival
- dew point
- wind shifts
- TWC nowcasts
- NWS guidance
- other high-frequency observation streams

They should be tested statistically rather than assumed causal.

---

### Why the Aug 20 hard-state repricing itself is simpler

Once 84 F was observed, the 82-83 F bucket became impossible. The market did not need to know that 84 F would be the exact final high; it only needed to remove probability from states below the newly established maximum.

Immediately beforehand, approximately:

- 82-83: ~30-35%
- 84-85: ~56-60%
- 86+: ~6-9%

After the lower bucket became impossible, much of the removed probability naturally concentrated in 84-85 because the pre-existing upper-tail probability was already small. The post-sweep 84-85 pricing in the high-80s/low-90s is directionally consistent with simple conditional renormalization.

That is useful evidence for the hard-elimination strategy, but **why the 86+ tail was already so small** belongs to a later winner-prediction strategy.

---

### Open questions to return to

**Primary winner-prediction question**

Why was `P(max > 85 F)` already only about 6-9% before the 11:51 84 F observation was fully incorporated, and which machine-readable signals explain that confidence robustly across history?

**Market-microstructure question**

Why do Aug 20 and Aug 21 both show public trade-tape sweep clusters almost exactly ~122 seconds after the nominal `:51` KNYC observation stamp?

The source URLs and key timestamps are preserved above so this case can be reconfirmed later without reconstructing it from scratch.
