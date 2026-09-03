# Data dictionary

## Clock fields

| Field | Meaning | May be substituted? |
|---|---|---|
| `observed_at` | Physical report/measurement valid time | No |
| `received_at` | Time exposed by the primary source | No |
| `ingested_at` | First Mercury Edge database write | No |
| `issued_at` | Official product issuance | No |
| `captured_at` | Quote or candle timestamp | No |
| `high_observed_at` | High occurrence time encoded in DSM | No |

`receipt_quality` is `actual`, `bounded`, or `discovery_only`. Only `actual` passes the default executable-latency gate.

## Core tables

- `stations`: exact settlement station, city, timezone, and source mappings.
- `weather_observations`: normalized reports plus raw source payload.
- `product_releases`: official DSM/CLI releases and encoded daily highs.
- `market_events`: city-date market and settlement source.
- `market_contracts`: mutually exclusive temperature bands.
- `market_quotes`: one-minute bid/ask/last OHLC history; timestamps are candle ends.
- `market_trades`: exact-time public prints, taker outcome side, price, and quantity. These are observed participants' fills, not Mercury fills.
- `ingestion_runs`: provider-level successes and failures.
- `backtest_runs`: immutable configuration and aggregate result.
- `backtest_signals`: one row per predeclared trigger and its gates.
- `source_health`: last successful probe and consecutive failure count.

Numeric temperature values preserve the source precision after exact unit conversion. UI rounding never overwrites stored values.

## Backtest evidence tiers

| Tier | What it supports | What it does not support |
|---|---|---|
| `weather_only` | Candidate elimination census | Market price, latency, or profit |
| `minute_candle_proxy` | Price path and interval-censored reaction | Depth, queue position, or fill |
| `trade_tape_observed` | Another taker's exact-time price and quantity | Mercury's counterfactual priority or fill |
| `l2_simulated` | Latency/depth-aware simulated fill | A real-money execution |

`violent` is a descriptive episode label, not an entry feature. `candidateProxy` requires an actual receipt timestamp, a proven settlement transformation, and a nonterminal post-trigger candle. `executable_proxy` stays false in the candle/tape backtest and may only be set by the sequenced L2 replay path.
