# Mercury Edge — Backtest Discovery Report

**Audit date:** 2026-08-18  
**Source run:** production `backtest_runs.id=3`, model `latency-v0.1.0`  
**Nominal range:** 2026-06-26 through 2026-08-13 (`as_of_end=2026-08-14T00:00Z`)  
**Purpose:** discovery/ranking for live capture. **Not** evidence that a station or strategy is ready for money.

## 1. Run-level facts

The stored run reported:

- 882 market events
- 1,526 mechanical signals
- 397 `executable_proxy` signals
- 1,487 wins / 1,526 resolved = 97.44% outcome win rate
- gross P&L per one-contract signal stream: $0.16
- estimated fees: $0.02
- net P&L: $0.14
- explicit engine warning: minute-candle quotes are proxies, not proof of fills or available size

### Critical interpretation

The apparent 97.44% win rate must **not** be treated as a hard-state success rate. A genuinely proven dead bucket should not lose because of subsequent weather. The 39 historical losses are evidence that the old transformation (`round(runningHigh)` from sources then marked settlement-compatible) was not actually settlement-hard. The paper-trader branch now fails closed on this point.

## 2. The old P&L was concentrated in only two signals

Of all 397 signals marked `executable_proxy`, only **two** had an entry NO price below $1.00:

| Station | Date | Contract | Old entry NO | Reaction lag | Old net P&L / contract |
|---|---:|---|---:|---:|---:|
| KMSP | 2026-08-04 | 75°F or below | 89¢ | 1,465 s | +10¢ |
| KMDW | 2026-08-01 | 70–71°F | 95¢ | 333 s | +4¢ |

The other **395 executable proxies entered at NO = $1.00**, i.e. zero gross edge in the first eligible minute-candle snapshot.

This is one of the most important findings from the previously unstudied run: the historical `+$0.14` net result was not a broad repeatable return stream; it was almost entirely two events.

### What this does and does not mean

It does **not** prove that the other stations had no sub-minute stale opportunity. The old backtest used minute-candle book states, so a 10–40 second stale window could disappear inside the candle before the first proxy quote was recorded. This is the main reason live sequenced L2 capture is necessary.

## 3. Phoenix: real research priority, but not proven historical profit

KPHX is unusual enough to prioritize for live capture:

- 102 total signals
- 16 old executable proxies
- 24 signals with a measurable reaction lag
- median measurable lag: 50.5 s
- p90 measurable lag: ~439.7 s
- maximum: 2,390 s (~39.8 min)
- 5 / 24 measurable lags were >=120 s
- old outcome win rate: 100 / 102 = 98.04%
- **all 16 old executable entries were already NO = $1.00**

Phoenix long-lag cases:

| Date | Local trigger | Contract | Reaction lag | Old entry |
|---|---|---|---:|---|
| 2026-08-01 | 16:54:09 MST | 112–113°F | 2,390 s | none within 90 s |
| 2026-08-01 | 14:54:12 MST | 111°F or below | 708 s | none within 90 s |
| 2026-08-03 | 14:54:09 MST | 111–112°F | 530 s | none within 90 s |
| 2026-08-12 | 13:54:10 MST | 97°F or below | 229 s | none within 90 s |
| 2026-08-13 | 13:54:14 MST | 96°F or below | 226 s | none within 90 s |

**Key hypothesis:** a missing minute-candle quote is not equivalent to an empty order book. A quiet market can have resting stale L2 liquidity while producing no new candle state useful to the old backtest. Phoenix is therefore a high-value station for testing the **no-candle blind spot** with live order-book snapshots/deltas.

## 4. Heavy-tail station effect

Reaction latency is strongly heavy-tailed. Means are often misleading; some stations have modest medians but repeated multi-minute tails.

Selected candidates from the old run:

| Station | Measured lags | Median | p90 | Max | >=120 s |
|---|---:|---:|---:|---:|---:|
| KDCA | 26 | 59.5 s | 817.5 s | 1,428 s | 9 |
| KATL | 34 | 85.5 s | 457.6 s | 1,769 s | 12 |
| KMSY | 28 | 57.5 s | 513.7 s | 4,833 s | 9 |
| KPHX | 24 | 50.5 s | 439.7 s | 2,390 s | 5 |
| KBOS | 32 | 44.5 s | 163 s | 234 s | 8 |
| KMSP | 26 | 28 s | 161 s | 1,465 s | 5 |
| KPHL | 39 | 46 s | 149.2 s | 522 s | 5 |

KSAT is an especially extreme-tail example: max 6,654 s (~111 min) but only two lags >=120 s and p90 ~112 s. This is why station selection should not use mean lag alone.

### Research implication

Rank stations on at least four dimensions separately:

1. frequency of hard/near-hard weather state changes,
2. probability that stale executable L2 survives after the signal,
3. duration of the stale tail conditional on survival,
4. executable depth/price after fees.

A single average-lag score hides the structure we actually care about.

## 5. Report-cycle clustering

When restricted to old `executable_proxy=true` signals (which require an actual source receipt plus a quote within 90 s), trigger minutes cluster strikingly around recurring report minutes:

- KPHX: all 16 at `:54`
- KNYC: all 11 at `:54`
- KSAT: 13 / 14 at `:54`
- KATL: 15 / 17 at `:56`
- KAUS: 25 / 26 at `:56`
- KDFW: 20 / 23 at `:56`
- KOKC: 30 / 30 at `:56`
- KLAX: 26 / 29 at `:56`
- KMIA: 28 / 39 at `:56`
- KMDW: 23 / 30 at `:56`
- KMSP: 20 / 22 at `:56`
- KPHL: 25 / 29 at `:58`
- KBOS: 17 / 20 at `:58`
- KLAS: 15 / 16 at `:00`

This looks like an upstream observation/report cadence effect, not random arrival timing.

### Operational implication

For live research, subscriptions should already be hot before each station's routine report window. We should measure:

`weather source receipt -> Mercury decision -> contemporaneous L2 state -> first book mutation -> first trade -> full repricing`

Do **not** wait to discover/subscribe only after a weather signal arrives.

A future FAA/OMO feed may introduce additional intra-hour signal times, so routine-report windows are a priority—not the only windows to monitor.

## 6. False-hard / settlement-transform risk is station-specific

The old model produced 39 losses in trades it conceptually treated like mechanical eliminations. Highest observed old loss rates included:

- KDCA: 7 / 107 = 6.54%
- KHOU: 4 / 67 = 5.97%
- KDFW: 4 / 85 = 4.71%
- KAUS: 4 / 93 = 4.30%
- KLAX: 4 / 131 = 3.05%
- KPHL: 3 / 114 = 2.63%
- KPHX: 2 / 102 = 1.96%

Examples also cluster in particular bucket ranges at some stations (e.g. KLAX 79–80°F and several 95–98°F bands at hot-weather stations). This may reflect rounding/precision, source semantics, or the old use of current temperature as a settlement-compatible running maximum.

**Conclusion:** settlement compatibility must be validated per source + station + event-rule version + transformation. A generic `official source = hard` flag is unacceptable.

## 7. No-candle blind spot

The old engine defines an entry as the first stored quote within 90 seconds after the signal. If no quote row exists, `entry=null` and `executable_proxy=false`.

That leaves two very different states indistinguishable historically:

1. no tradable liquidity existed, or
2. stale resting orders existed but the minute-candle dataset did not record an intervening state/update.

The long KPHX cases are the strongest reason to measure this live. Live L2 snapshots/deltas can directly answer the question.

**Candidate research pattern name:** `NCB` — **No-Candle Blind Spot**.  
This is a data/execution pattern, not yet a trading strategy.

## 8. Report-Cycle Staleness hypothesis

The minute clustering plus heavy tails suggests a testable pattern:

**RCS — Report-Cycle Staleness:** routine station reports arrive on predictable minute offsets; some temperature contracts may retain stale resting liquidity for seconds or minutes afterward.

RCS should be tested as a **signal-timing regime**, not traded blindly. The valid trade still needs a DBN/DSN/etc. strategy and a proven weather-state transformation.

Metrics to capture per report cycle:

- whether a relevant weather state changed,
- L2 state immediately before and after receipt,
- first stale executable price and depth,
- stale survival at 10/25/50/100/250/500/1000/2000/5000 ms,
- first book delta attributable to repricing,
- first public trade,
- full repricing time,
- whether no market update occurred despite resting depth.

## 9. Regional clustering is a portfolio-risk issue

Signals occur across many stations on the same heat-event days. Even if individual contracts are geographically separate, they may share:

- the same synoptic heat regime,
- the same upstream report architecture,
- the same Kalshi participant/market-maker reaction process,
- the same settlement-source implementation.

The allocator should therefore maintain both **event-level** and **regional/source-level** exposure clusters instead of treating every station as independent capital diversification.

## 10. Live-capture station plan

### Broad passive capture — all 18 historically tested stations

Capture Kalshi L2/trades, Kalshi rules, and AWC for:

`KNYC KPHL KLAX KPHX KMDW KMSP KDCA KATL KMSY KBOS KMIA KOKC KDFW KDEN KAUS KHOU KLAS KSAT`

Passive capture is intentionally broad because rare tails can dominate research value.

### High-frequency IEM/MADIS priority — 10 stations

`KNYC KPHL KLAX KPHX KMDW KMSP KDCA KATL KMSY KBOS`

Reasons:

- KNYC/KPHL/KLAX: manually reconstructed edge cases.
- KMSP/KMDW: the only two old executable entries below NO=$1.00.
- KPHX: unusually heavy no-candle/reaction tail.
- KDCA/KATL/KMSY/KBOS: strongest repeated heavy-tail candidates.

KMIA is retained as an optional control because it had many executable proxies but no old sub-$1 NO entry.

## 11. What the old backtest cannot answer

It cannot establish:

- actual L2 depth at the signal instant,
- whether a static order book existed during a no-candle interval,
- queue position,
- sub-minute price changes hidden inside a candle,
- exact order-arrival/fill behavior at 10–500 ms,
- settlement compatibility of the old signal source,
- whether archived discovery-only OMO data was publicly available at its observation timestamp.

These are now explicit live-paper audit targets rather than assumptions.

## 12. New ranking principle

Do not rank stations by historical P&L or mean lag alone.

For live paper trading, compute a **Station Opportunity Profile** from actual captured L2:

- `valid_signals`
- `hard_approved_signals`
- `stale_l2_events`
- `stale_event_rate`
- `median/p90 stale survival ms`
- `best executable NO cost`
- `depth at 25/50/100/250/500 ms`
- `fee-adjusted edge`
- `false-hard / auditor block rate`
- `source receipt latency`
- `market reaction latency`
- `capacity-adjusted realized paper P&L`

Only after those metrics exist should the allocator use station-specific capital weights.
