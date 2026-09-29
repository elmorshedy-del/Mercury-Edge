# Step 4E verification — pure bucket elimination

Status: **PASS**

Branch: `paper-rigour-v2`

PR: #5

Implementation commits through: `cd872bdb69e948bcfdc2e4345750821efe6ed94a`

GitHub Actions: **Paper Trader CI run 312 — PASS**

## What is now authoritative

`paper_collector/bucket_elimination.py` is the only canonical deterministic bucket-death calculation for the hardened DBN path.

The module consumes only:

- a source-neutral `HardClimateState`;
- the exact Kalshi event/market strike snapshot;
- the immutable event rules hash and normalized station identity.

It contains no METAR, MADIS, Celsius, weather-parser, forecast, or likely-winner logic.

A bucket is eliminated only when the canonical hard lower bound is **strictly greater** than that market's finite cap. Equality is not death. An upper-tail market with no finite cap cannot be eliminated from a lower-bound-only climate state.

The whole event fails closed when event date, station, rules hash, market ticker, or strike metadata is missing/malformed/ambiguous.

Each elimination carries a stable machine-readable proof including the hard-state id, transition evidence id, climate date, exact strike rule, event rules hash and elimination model version.

The hardened DBN path now routes event snapshots through this module instead of locally calculating `high > cap`.

## Permanent regressions verified

- hard state `>= 88` eliminates every and only market capped below 88;
- cap exactly 88 remains alive;
- decimal cap below an integer hard bound is eliminated correctly;
- Aug-18 hard state cannot eliminate an Aug-19 event;
- station mismatch fails closed;
- unparseable event date fails closed;
- missing event rules hash fails closed;
- malformed strike metadata fails the entire event closed;
- upper-tail no-cap market remains alive;
- identical hard state gives the same dead set regardless of whether the source mechanism was current/T/hidden-max evidence;
- elimination output is deterministic.

## Verification

GitHub Actions run **312**:

- **103 Python tests passed, 0 failed**;
- Python compile PASS;
- collector Docker build PASS;
- Node checks PASS;
- full Postgres migrations PASS;
- immutable raw/evidence journal regression PASS;
- immutable hard-state timeline regression PASS.

No merge and no Railway deployment were performed.

## Next

Proceed strictly to **Step 4F — active stale dead-NO execution**. The active benchmark remains a real-time paper bot: eliminated bucket -> executable live L2 NO -> exact fee-adjusted guaranteed economics -> simulated order/fill/cash/position. Counterfactual opportunity curves remain a separate research/audit layer and cannot overwrite benchmark decisions or P&L.
