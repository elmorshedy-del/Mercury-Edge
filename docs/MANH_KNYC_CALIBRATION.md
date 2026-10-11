# MANH → KNYC calibration: seeing Central Park between hourlies

Kalshi and Polymarket NYC high-temperature markets settle on the NWS Daily Climate Report
(CLI) for Central Park (KNYC). KNYC publishes only hourly METARs at :51, so for 59 minutes of
every hour the market is "dark". The NYS Mesonet Manhattan site (MANH) reports every 5 minutes.
This study asks how much of KNYC's hidden state can be recovered from MANH, nearby Mesonet
sites and the airports west and south of the park (Newark, Caldwell, Wall Street), and turns the
answer into a live dashboard panel (Weather Lab → NYC → Live).

All performance numbers are **out-of-sample**: models are trained on 2021–2024 and scored on
every decision point from 1 Jan 2025 to 8 Oct 2026, which they never saw. Each Mesonet model is
compared with an otherwise identical model *without* Mesonet data, which already knows the time
of day, the season, KNYC's own recent hourly trend and the NBM forecast. The lift reported is
therefore what the Mesonet data adds beyond what a careful trader already has.

Reproduce everything with `research/manh_knyc/` (see its README).

---

## 1. What KNYC actually reports (verified against 1-minute ASOS data)

| Fact | Evidence |
|---|---|
| The :51 METAR temperature is the **5-minute average of the 1-minute temperatures from :47 to :51**, rounded to a whole °F. The T-group (tenths °C) is just that whole-°F value converted. | Reproduced exactly for 99.9% of 36,000+ METARs |
| The CLI daily high is the **highest of those 5-minute averages, computed every minute** of the climate day. | Matches on 99.2% of 742 days with complete 1-minute data |
| The climate day runs **midnight to midnight local *standard* time** (1:00 AM–12:59 AM EDT in summer). | NWS convention; used throughout |
| The CLI high **beats the highest hourly METAR on 58% of days** (+1 °F on 49%, +2 °F or more on 9%). It is higher more often in spring and summer (62–71% of days) than in winter (43–46%). | 2,094 days, 2021–2026 |

The last row is the core opportunity: on most days the true high happens *between* hourlies and
only becomes public through the 6-hour max groups (11:51, 17:51, 23:51 UTC METARs), the DSM, or
the CLI itself.

## 2. Why MANH alone does not "just track" Central Park

MANH is the closest Mesonet site (≈1.3 km SSE of the KNYC sensor) but it is a **city rooftop**
(≈95 m elevation incl. building, WMO siting class 5, "completely surrounded by artificial heat
sources"). KNYC is a ground-level sensor in a large park. The two respond differently:

* **Bias changes through the day.** MANH runs ≈1.7–1.9 °F warmer than KNYC overnight (the park
  cools, the roof does not), drops to ≈0.4 °F *cooler* around 12–2 PM, and the difference has a
  ±2 °F day-to-day spread. By season it is ≈+2.3 °F in winter but ≈0 in summer. A fixed offset
  cannot work.
* **Short-term wiggles barely correlate.** Correlation between MANH's and KNYC's temperature
  changes: 0.08 over 5 min, 0.26 over 15 min, 0.45 over 30 min, 0.67 over 60 min, 0.82 over
  2 hours. Over one hour KNYC moves only ≈64% as much as MANH. This is the "they don't trend
  together intra-hour" effect seen in practice.
* **Season matters.** The hourly correlation is ≈0.8 from May to September but only 0.41–0.53 in
  December–February, when the roof decouples from the park.

### A blend of six stations is a much better proxy than MANH

Fitting the KNYC 60-minute change on six nearby Mesonet sites (weights fit on 2021–2024 only):

| Station | Type | Weight |
|---|---|---|
| MHCHEL Chelsea | NYC Micronet | 28% |
| BRON Bronx | NYS Mesonet | 26% |
| MHMHIL Murray Hill | NYC Micronet | 20% |
| MANH Manhattan | NYS Mesonet (rooftop) | 14% (≈38% in summer, ≈6% in winter) |
| QUEE Queens | NYS Mesonet | 9% |
| BKLN Brooklyn | NYS Mesonet | 6% |

The blend's change correlates with KNYC's change at 0.50 over 15 minutes (MANH: 0.26) and 0.85
over 60 minutes (MANH: 0.67). The blend explains ≈71% of hourly KNYC variance versus ≈44% for
MANH alone. MANH stays in the model, both inside the blend and through its own features (it is
best in summer and carries solar radiation, wind and humidity), but it is not the best single
proxy.

### What about LaGuardia (KLGA)?

LGA is ≈8 km ENE of the park, on Flushing Bay. Our weather usually moves west to east, so in
most hours LGA is *downstream*: in westerly and southerly flow its temperature changes line up
with or follow KNYC's (peak correlation at 0 to +15 min). In easterly flow off the water the
relationship flattens (LGA's past changes correlate about as well as its concurrent ones), which
is the "something coming from the east" case, but the coupling is weak (≈0.2) because LGA sits in
its own marine air.

Practically, LGA's intra-hour feed is published at whole-°C resolution (1.8 °F steps; our
simulation matched 143/143 archived readings) and arrives **≈20–30 minutes late** on the NWS API
(≈40 minutes on IEM). Added to the models under those real conditions, LGA changed next-METAR
log loss from 1.1805 to 1.185 (no gain, even with zero delay and full 1-minute resolution) and
changed the daily-high models by noise-level amounts (hidden-max AUC 0.827 → 0.831). LGA is
therefore **not** in the live model. `research/manh_knyc/lga_check.py` reruns the test.

### Newark, Caldwell and Wall Street (west and south) — in the model

Caldwell (KCDW, ≈30 km WNW) and Newark (KEWR, ≈15 km WSW) sit *upstream* in our usual westerly
flow; Wall Street (KJRB, downtown heliport) is ≈10 km south. Using their 1-minute histories, in
west/northwest wind **Caldwell's temperature changes lead Central Park's by about 30 minutes**
(correlation of KNYC's next-15-minute change with Caldwell's change ending 15 minutes earlier:
0.35, versus 0.24 for the simultaneous change). Newark leads by roughly 15 minutes (0.31 peak).
In southerly or easterly flow neither leads.

How they are used live (`research/manh_knyc/airports.py`, mirrored in TypeScript):

| Feed | Resolution | When it can be used |
|---|---|---|
| 5-minute ASOS reports (api.weather.gov; identical to the IEM MADIS archive reading-for-reading) | whole °C (1.8 °F steps) + wind | 25 minutes after the stamp (observed delay 12–30 min) |
| Routine/special METARs (EWR :51, CDW :53, JRB :56) | whole °F via the T-group | 3 minutes after the observation |

Features: each airport's level relative to the last KNYC METAR (or the published max), its 30-
and 60-minute changes, wind components, its latest METAR level and hourly change, and (for the
daily models) its highest reading of the climate day so far. Readings outside −40…120 °F (the
`M61/M61` missing-data sentinel) are dropped, and METARs older than 90 minutes (180 for the
previous one) are ignored — Caldwell and Wall Street both have multi-day outages in the archive.

Out-of-sample effect of adding the three airports on top of the six Mesonet sites (95% intervals
from resampling whole days, so correlated hours within a day do not overstate certainty):

| Model | Without airports | With airports | Log-loss change (95% CI) |
|---|---|---|---|
| Next METAR | 50.7% exact, log loss 1.180 | **51.4%**, **1.168** | −0.013 (−0.016 … −0.009) |
| Hidden max | AUC 0.827, log loss 0.784 | **AUC 0.848**, **0.769** | −0.015 (−0.028 … −0.001) |
| Final high | 47.8% exact, log loss 1.413 | **48.6%**, **1.395** | −0.018 (−0.032 … −0.005) |

All three improvements are statistically clear, if modest. Caldwell carries most of the
next-METAR gain (its 30-minute lead in westerly flow survives the feed delay), Newark adds some,
and Wall Street adds nothing to the next METAR but contributes to the daily models through its
precise :56 METAR and its daily maximum. With zero feed delay the next-METAR gain would roughly
double (log loss 1.156 vs 1.168 live; `research/manh_knyc/results/airports_check.txt`), so a faster source for the 5-minute airport data
would be worth having. `research/manh_knyc/airports_check.py` reruns these comparisons.

### The NYS Mesonet "forecast high"

The forecast shown on nysmesonet.org is not a Mesonet product. The site calls the **NWS point
forecast** (api.weather.gov) for the station's grid cell. It is dynamic: it changes whenever NWS
OKX updates its grid, several times a day. The nowcast uses the National Blend of Models (NBM)
TXN forecast and spread instead, because its full history is archived and could be tested.

---

## 3. Method: anchor on the last METAR, explain the change

The hidden KNYC temperature at time *t* between hourlies is modelled as

```
KNYC(t) ≈ last METAR + β · (blend change since the METAR's averaging window)
```

The anchor removes the slowly varying rooftop/park bias, and what remains to be learned is how
much of the Mesonet *change* shows up in the park. That depends on time of day, season, sun,
wind and recent trend. Three gradient-boosted classifiers (LightGBM, exported to TypeScript)
turn this into calibrated probabilities:

| Model | Question | Output |
|---|---|---|
| Next METAR | What whole-°F value will the next :51 METAR show? | P(value) for last −5 … +5 °F |
| Hidden max | Is KNYC's true running high *already* above the published max? | P(+0, +1, +2, +3 or more °F) |
| Final high | Where will the CLI high finish? | P(published max + 0 … +20 °F) |

Inputs at each 5-minute decision time use only data that existed then: KNYC reports 3 minutes
after their :51 stamp, Mesonet values at their stamp (they arrive ≈1m50s later), airport
5-minute reports 25 minutes after their stamp and airport METARs 3 minutes after theirs, and NBM
runs 75 minutes after their run time. Path features reconstruct the implied KNYC curve between every
pair of hourlies (straight line between METARs plus the blend's deviation from its own straight
line), so a spike that came and went between two METARs leaves a trace.

Probabilities are calibrated: early stopping and a softmax temperature are fitted on 2024
(held out from the 2021–2023 fit), and the daily models use large minimum leaf sizes because
their 288 decision points per day are highly correlated. All results below use that 2024
temperature and are strictly out-of-sample. The *deployed* models then re-fit the calibration on
2025–26 (the most recent held-out period): the temperature for all three, plus a logistic
(Platt) correction of the hidden-max model's headline P(already higher), which scored with the
2024 temperature came out overconfident in 2025–26 (stated 45% → observed 37%). After the
correction it tracks the 2025–26 outcomes (35% → 32%, 45% → 46%, 75% → 72%); that check is
in-sample for the two calibration parameters only.
Calibration tables are in `research/manh_knyc/results/metrics.json`.

---

## 4. Results (2025-01-01 → 2026-10-08, out-of-sample)

### 4a. Next hourly METAR (14,765 test hours)

| Information used | Exact value is the top pick | Within ±1 °F | Log loss |
|---|---|---|---|
| No Mesonet (time, season, KNYC trend) | 38.0% | 82.1% | 1.515 |
| + MANH only | 46.4% | 88.9% | 1.290 |
| + MANH and 5-station blend | 50.7% | 91.6% | 1.180 |
| **+ Newark, Caldwell, Wall Street (live model)** | **51.4%** | **91.9%** | **1.168** |

By how early the call is made (live model vs no Mesonet):

| Mesonet reading used | Minutes before the METAR | Exact | Within ±1 °F |
|---|---|---|---|
| :50 | 1 | 54.6% (vs 37.7%) | 93.9% (vs 82.1%) |
| :40 | 11 | 54.4% | 94.0% |
| :25 | 26 | 51.9% | 92.5% |
| :10 | 41 | 50.0% | 90.9% |
| :55 (just after the last METAR) | 56 | 46.5% | 88.4% |

Calibration is close to perfect: when the model says the next METAR will *differ* from the last
one with probability 45%, it differs 43% of the time; at 85% it differs 88% of the time.

### 4b. Has KNYC already exceeded the published high?

At a random time of day, the true running high is above the published max 46% of the time.

| | Log loss | AUC (detect "already ≥ +1 °F") |
|---|---|---|
| No Mesonet | 0.877 | 0.761 |
| **With Mesonet + airports** | **0.767** | **0.848** |

The lift is largest in the afternoon, the window that matters before the 1:51 PM EDT 6-hour max
release and the evening CLI: AUC 0.72 → 0.83 at 2 PM, 0.70 → 0.81 at 3 PM, 0.62 → 0.79 at
5 PM, 0.58 → 0.79 at 6 PM. Scored with the 2024 calibration it is overconfident in the middle
(stated 35% → observed 27%, 45% → 37%, 55% → 49%); the deployed model adds the Platt correction
described in section 3.

### 4c. Final CLI high

Share of decision points where the most likely value is exactly the CLI high:

| Local time | No Mesonet | With Mesonet + airports | Within ±1 °F (live model) |
|---|---|---|---|
| 10 AM | 19.2% | 21.3% | 53% |
| 12 PM | 22.2% | 26.1% | 64% |
| 2 PM | 48.5% | 53.2% | 84% |
| 3 PM | 54.9% | 60.6% | 91% |
| 4 PM | 58.3% | 64.4% | 96% |
| 5 PM | 61.0% | 67.1% | 97% |
| 6 PM | 59.8% | 66.4% | 97% |
| 7 PM | 69.5% | 74.7% | 97% |

Before about 1 PM the final high is mostly a forecasting problem (NBM already carries most of
what is knowable) and the observations add little. From 2 PM on, the question becomes "what
already happened between hourlies", and the Mesonet + airports add 5–7 points of exact-value
accuracy. Per-value probabilities are well calibrated (stated 45% → observed 44%, 65% → 69%),
slightly high only around 30–40% (34% → 29%).

---

## 5. Live tool

**Weather Lab → NYC → Live → "What KNYC is doing between hourlies"**
(`/api/weather-dashboard/manh-nowcast`, refreshes every 60 s).

* **Blend-implied KNYC now:** the last METAR plus the six-station blend's change since then.
* **Next hourly METAR:** probability of each whole-°F value, plus P(lower / same / higher).
* **Daily high (CLI):** the published max so far (METARs, SPECIs and 6-hour max groups), the
  probability that KNYC is *already* higher, and the final-high distribution.
* **Airports row:** the latest Newark, Caldwell and Wall Street 5-minute reading (with its age)
  and METAR.
* **Model vs market:** today's KXHIGHNY brackets with model probability, Kalshi quote midpoint
  and the difference.
* **Chart:** the implied KNYC path through the day, MANH raw, every KNYC report and the published
  max.

Data latency in practice: MANH 5-minute values arrive ≈1m50s after their stamp, so the :50
reading lands at ≈:51:50, usually *before* the :51 METAR reaches AWC (≈:53–:54). KNYC METARs
and airport METARs come from aviationweather.gov, airport 5-minute reports from api.weather.gov,
NBM from IEM, Kalshi from the public API. The TypeScript feature
engine is tested to reproduce the Python training features exactly on a replayed day
(`tests/manh-nowcast.test.ts`).

## 6. How to read it, and caveats

* **Strongest use cases:** (1) the last 10–15 minutes before an hourly, when the :45/:50 Mesonet
  readings pin down the coming METAR (≈55% exact, ≈94% within ±1 °F); (2) mid/late afternoon,
  when the question is whether the high already happened between hourlies and what the 6-hour
  group or CLI will reveal.
* **Weakest use case:** the morning final-high forecast. Before noon the outcome depends on
  heating that hasn't happened yet; the model only knows NBM plus observations, while the market
  aggregates many models. A large morning gap between model and market is more likely a model
  limitation than an edge.
* **Exact integers are hard.** KNYC is rounded to whole °F and the rooftop/park mismatch adds
  ≈1 °F of noise. Probabilities spread over two or three values are the honest answer, not a
  defect.
* **Market comparisons use quote midpoints,** not executable fills. Fees and spreads apply.
* **Retrain periodically** (every few months, and after any sensor or siting change at KNYC or
  the Mesonet sites). If a Mesonet site goes offline, the blend re-weights over the remaining
  stations automatically; if MANH itself is down, the panel stops predicting rather than guess.

## 7. Ideas not yet tested

* KNYC-specific intra-hour data sources (none is public today; NWS API shows hourly only).
* Model vs market backtest on archived Kalshi candles (`MarketReactionPanel` infrastructure),
  especially around the 17:51 UTC 6-hour max release.
* Additional NYC Micronet sites or HRRR short-range forecasts for the morning final-high problem.
* A faster source for the airports' 5-minute reports. The dashboard already has a Synoptic token
  (`SYNOPTIC_TOKEN`, used by the HF-ASOS panel); if Synoptic delivers them sooner than
  api.weather.gov's 12–30 minutes, lowering `LATENCY_MIN` in `airports.py` and retraining should
  recover much of the zero-delay gain.
* Other stations upstream in westerly flow (e.g. Teterboro, Morristown) on the same test.
