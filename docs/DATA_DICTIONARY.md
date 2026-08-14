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
- `market_quotes`: one-minute quote/candle history.
- `ingestion_runs`: provider-level successes and failures.
- `backtest_runs`: immutable configuration and aggregate result.
- `backtest_signals`: one row per predeclared trigger and its gates.
- `source_health`: last successful probe and consecutive failure count.

Numeric temperature values preserve the source precision after exact unit conversion. UI rounding never overwrites stored values.
