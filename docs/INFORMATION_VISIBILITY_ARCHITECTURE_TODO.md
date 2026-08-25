# Mercury Edge — Information Visibility Architecture TODO

Status: **LOCKED RAW-FIRST ARCHITECTURE — design before implementation**

Branch: `paper-rigour-v2`

PR: #5

## Purpose

Mercury must be able to reconstruct four synchronized histories for any station/event/day:

1. **Mercury knowledge** — what Mercury could legitimately know, and when it knew it.
2. **Public/crowd-visible information** — what authoritative information had actually become public, and when.
3. **Market belief / trading response** — the exact Kalshi quotes, depth, trades, and repricing path.
4. **Settlement truth** — what ultimately counted for settlement and any later corrections/revisions.

The primary engineering objective is not to guess why the crowd moved. It is to preserve enough causal raw data that later replay/statistical analysis can test what information was visible, what remained hidden, when that changed, and how the market reacted.

---

# Non-negotiable raw-first rule

**Raw facts are permanent; every interpretation is disposable and rebuildable.**

- [ ] Every externally received source message must have an immutable raw representation before semantic parsing.
- [ ] Raw payloads must be preserved byte-for-byte where transport permits; text WebSocket messages must preserve the exact received UTF-8 payload plus its byte hash.
- [ ] Preserve original ordering/sequence information supplied by the source.
- [ ] Preserve all available clocks separately; never collapse them into one timestamp.
- [ ] Raw records must be append-only and protected against UPDATE/DELETE.
- [ ] Derived/normalized/public-state/hard-state/crowd-reaction tables must be treated as replaceable projections.
- [ ] Every derived row must link back to the exact raw record(s) that produced it plus parser/model/calendar versions.
- [ ] A changed parser/model must create a new derivation; it must never rewrite historical raw data.
- [ ] Replays must support excluding a day, source, or record without deleting source data.
- [ ] Synthetic/injected research data must live in an overlay/replay layer and must never be written as if it were historically received raw data.
- [ ] It must be possible to delete all disposable derivations and deterministically rebuild them from raw journals and selected model versions.

Current architecture already provides much of this substrate:

- weather HTTP responses are captured before parsing in the immutable raw-source path;
- Kalshi WebSocket order-book/trade messages are journaled with raw text, sequence, receipt clocks, hash, and connection chain;
- canonical weather evidence and hard states are versioned derivations rather than replacements for raw source records.

This document extends that same principle to **public visibility and market-reaction reconstruction**.

---

# Canonical four-timeline architecture

```text
RAW EXTERNAL SOURCES
    |
    +--> Weather / disclosure raw journal
    |      AWC METAR, future MADIS/LDM, DSM, CLI, later public sources
    |
    +--> Kalshi raw market journal
           WS snapshots, deltas, trades, sequence, exchange/receipt clocks

              IMMUTABLE SOURCE LAYER
                       |
             versioned derivations
                       |
       +---------------+----------------+
       |               |                |
       v               v                v
Mercury Knowledge   Public Visibility   Market State
       |               |                |
       +---------------+----------------+
                       |
              synchronized event timeline
                       |
                       v
                Settlement Truth
                       |
                       v
          replay / attribution / backtest
```

No trading strategy is allowed to become a dependency of this data layer.

---

# A — Immutable source coverage audit

## A1. Weather / information sources

For each source used now or later:

- [ ] Preserve exact raw payload.
- [ ] Preserve source identity and stream/product identity.
- [ ] Preserve station when known, but do not require parsing to store raw data.
- [ ] Preserve source observation/product-valid time when supplied.
- [ ] Preserve source publication/generation/receipt time when supplied.
- [ ] Preserve first externally fetchable time only when actually measurable; never infer it from observation time.
- [ ] Preserve Mercury request-start/network-receipt/interpretation clocks separately.
- [ ] Preserve HTTP/LDM/transport metadata sufficient to audit latency.
- [ ] Preserve corrections/revisions as additional raw records; never overwrite earlier versions.

Required source families:

- [ ] NOAA/AWC METAR raw responses — existing path must remain intact.
- [ ] MADIS/LDM raw records — future 4G transport must write immutable raw records before normalization/reconstruction.
- [ ] DSM raw products — validation only.
- [ ] CLI raw products — validation/settlement lifecycle only.
- [ ] Kalshi settlement/final-result raw source used for grading.
- [ ] Any later public weather page/feed added to model crowd visibility must use the same raw-first contract.

## A2. Kalshi market source

- [ ] Preserve exact WebSocket messages for order-book snapshots, deltas, and trades.
- [ ] Preserve source sequence and connection identity.
- [ ] Preserve exchange timestamp when supplied.
- [ ] Preserve Mercury receipt wall-clock and monotonic time.
- [ ] Preserve raw payload hash and chain/continuity evidence.
- [ ] Treat sequence gaps/reconnects as explicit intervals of incomplete market knowledge.
- [ ] Preserve REST cross-checks as separate audit observations, never as a substitute for the causal WS stream.
- [ ] Preserve market/event/strike-rule snapshots needed to interpret prices and settlement states historically.

### A acceptance tests

- [ ] Raw weather payload can be retrieved without using any derived table.
- [ ] Raw Kalshi message can be retrieved without using reconstructed book tables.
- [ ] UPDATE/DELETE against immutable raw journals is rejected.
- [ ] Same raw payload received at a later time remains a distinct causal capture.
- [ ] Parser/model changes leave source hashes and raw payloads untouched.

---

# B — Public Disclosure object

Create a source-neutral, versioned `PublicDisclosure` derivation representing **what became publicly observable**, not what Mercury privately inferred.

Minimum fields:

- disclosure id;
- source/product;
- source raw record id/hash;
- station;
- climate date when applicable;
- observation/product-valid time;
- source publication/generation time when supplied;
- first-publicly-fetchable time when actually measurable;
- Mercury first-seen time;
- Mercury interpretation time;
- disclosure type (`CURRENT_MAIN`, `PRECISE_T`, `SIX_HOUR_MAX`, `DSM`, `CLI_PRELIMINARY`, `CLI_FINAL`, etc.);
- canonical content exposed by that disclosure;
- parser/model/calendar version;
- integrity/fail-closed status.

Rules:

- [ ] A `PublicDisclosure` may state only information actually exposed by that public record.
- [ ] It must never import hidden MADIS-derived state merely because Mercury already knows it.
- [ ] Main-temperature, precise `T`, six-hour max, DSM, and CLI remain distinct public facts.
- [ ] Multiple facts in one public report share the same causal public arrival but remain separately interpretable facts.
- [ ] Later corrections append new disclosure derivations rather than rewriting old ones.

---

# C — Public Information State

Create a versioned, replayable `PublicInformationState` for each station/climate-date as of a selected causal time.

Purpose:

> What settlement-relevant information could a trader observing the configured public information set have seen by this moment?

It is **not** a model of trader intelligence and not a forecast.

Minimum state:

- latest public current-temperature disclosure;
- latest public precise-current disclosure if present;
- latest valid public six-hour maximum disclosure if present;
- highest daily lower bound actually disclosed publicly so far;
- disclosure ids supporting each field;
- state-known/public-seen time;
- configured visibility-source policy/version.

Rules:

- [ ] State changes only when a new configured public disclosure arrives.
- [ ] It is reconstructed from raw public-source records and versioned disclosure parsing.
- [ ] It cannot use future records.
- [ ] It cannot use private/research MADIS reconstruction unless that same fact was separately publicly disclosed.
- [ ] Source-set assumptions must be explicit and versioned. Example: `public-state-v1 = NOAA/AWC public METAR stream only`.
- [ ] If public availability timing cannot be established precisely, preserve a range/uncertainty or fail closed instead of inventing an exact timestamp.

### C acceptance tests

- [ ] Hidden Mercury max before public disclosure does not appear in public state.
- [ ] The same max appears in public state only when a qualifying public disclosure arrives.
- [ ] Public state replay is deterministic from the same raw data + versions.
- [ ] A correction produces a later state transition without erasing the earlier public state.

---

# D — Mercury Knowledge State

Keep the existing canonical hard-state architecture as the authoritative answer to:

> What settlement-compatible facts could Mercury legitimately know at this time?

- [ ] Preserve all existing immutable evidence/provenance/version rules.
- [ ] MADIS reconstruction remains research-only until explicitly promoted by versioned trust policy.
- [ ] Mercury-known time remains receipt/interpretation-causal, never backdated to physical observation time.
- [ ] Maintain a direct comparison helper between `MercuryKnowledgeState` and `PublicInformationState`.

Derived comparison fields may include:

- Mercury proven daily-high lower bound;
- public disclosed daily-high lower bound;
- information-gap size in °F when representable;
- first time the gap opened;
- first time public information caught up;
- gap duration;
- exact raw/derived records establishing both sides.

This comparison is analysis metadata, not a trading rule.

---

# E — Market Belief State

Market belief must be represented by **observable market behavior**, not a story about trader psychology.

Reconstruct from raw Kalshi journal:

- [ ] exact L2 state at any causal time where continuity is valid;
- [ ] best YES/NO bid/ask;
- [ ] spread;
- [ ] depth by price level;
- [ ] executable size/average price for configurable notionals;
- [ ] trades, side/aggressor information when observable, price, quantity, exchange time and Mercury receipt time;
- [ ] event-wide probability/price distribution across all buckets;
- [ ] quote additions/cancellations/consumption where derivable from book deltas;
- [ ] sequence-gap / unavailable intervals explicitly marked invalid.

Important rule:

- [ ] `MarketBeliefState` records what the market priced. It must not claim *why* without a separate attribution analysis.

---

# F — Synchronized Information Event Timeline

Build a disposable/rebuildable timeline by joining the four histories in causal time.

Each event/day should be able to display:

```text
TIME            MERCURY KNOWS        PUBLIC SEES          KALSHI
13:41:08        high >= 77           current 75           75-76 YES 64
14:00:00        high >= 77           current 75           75-76 YES 71
15:53:01        high >= 77           six-hour max 77      75-76 reprices
15:53:03        high >= 77           six-hour max 77      75-76 YES 3
...
FINAL           --                   --                   settles NO
```

- [ ] Timeline rows/events reference source raw ids; they do not copy facts without provenance.
- [ ] Time resolution remains as fine as the raw clocks allow.
- [ ] Derived display bins (1s, 10s, 1m, 5m) are views only and can be changed later.
- [ ] No permanent `15 min before`, `3 PM`, `5 PM`, or other magic research window is baked into source storage.

---

# G — Market reaction / crowd-attribution research layer

This is deliberately downstream from raw capture and public-state reconstruction.

For every information event, later analysis should be able to calculate arbitrary windows such as `t-60s .. t+60s` without having stored only those windows originally.

Candidate derived metrics:

- price change by bucket;
- best-bid/best-ask change;
- spread change;
- depth removed/added;
- executable-price change at selected capital sizes;
- traded volume and count;
- time to first quote response;
- time to first trade response;
- time to 50%/90% of eventual repricing;
- event-wide price redistribution across buckets;
- pre-report drift;
- post-report continuation/reversal;
- information-gap duration versus mispricing duration.

Attribution states should be conservative, e.g.:

- `TEMPORALLY_ASSOCIATED`;
- `COMPETING_PUBLIC_EVENT_PRESENT`;
- `PRICE_MOVED_BEFORE_DISCLOSURE`;
- `NO_OBVIOUS_REACTION`;
- `MARKET_DATA_INCOMPLETE`;
- `CAUSALITY_UNRESOLVED`.

- [ ] Never convert temporal coincidence into certain causal language automatically.
- [ ] Preserve competing information events so later statistical models can control for them.
- [ ] Preserve days where nothing happens; do not build a dataset only from visually interesting moves.

---

# H — Settlement Truth and catch-up analysis

Settlement/validation remains separate from trade-time/public-time state.

- [ ] Preserve DSM/CLI/final settlement raw records and revisions.
- [ ] Build versioned `SettlementTruth` derivations.
- [ ] For each event measure:
  - when Mercury first knew the final-relevant hard bound;
  - when public information first exposed the same bound;
  - when Kalshi first substantially repriced toward the eventual truth;
  - whether the market moved before the formal public disclosure;
  - eventual settlement result.
- [ ] Any supposedly dead bucket settling YES remains a critical invariant failure, not merely a losing trade.

---

# I — Replay and research ergonomics

The raw-first design must make future experimentation cheap.

- [ ] Replay any chosen event/day entirely from immutable journals.
- [ ] Filter out a day/source/record without deleting anything.
- [ ] Replay only a selected station/market/time interval.
- [ ] Rebuild with different parser/evidence/public-state versions.
- [ ] Rebuild alternative display resolutions without touching source data.
- [ ] Apply synthetic research overlays to test hypotheses without contaminating historical raw journals.
- [ ] Compare two model/version outputs on identical raw data.
- [ ] Export a synchronized event table suitable for statistical analysis/backtesting.

Key invariant:

> **Changing what we want to analyze later must not require having predicted that analysis today.**

---

# J — Implementation order

Do not jump directly to a crowd-prediction model.

## J1 — Raw coverage audit

- [ ] Inventory every current weather and Kalshi raw source path.
- [ ] Verify exact payload preservation and immutability.
- [ ] Identify any derived-only facts that cannot currently be reconstructed from raw input.
- [ ] Fix raw gaps before adding new derived models.

## J2 — PublicDisclosure domain contract

- [ ] Add source-neutral versioned disclosure object and deterministic serialization.
- [ ] Add adapters from existing raw public METAR records.
- [ ] Keep DSM/CLI validation-only classifications explicit.

## J3 — PublicInformationState accumulator

- [ ] Add source-neutral causal public-state reconstruction.
- [ ] Add future-information-leakage tests.
- [ ] Add Mercury-vs-public gap derivation.

## J4 — MarketState reconstruction audit

- [ ] Verify existing WS snapshots/deltas/trades can reconstruct causal L2 for arbitrary timestamps.
- [ ] Explicitly surface invalid/gap intervals.
- [ ] Ensure all market-state outputs trace to raw WS records.

## J5 — Synchronized timeline

- [ ] Join Mercury state, public state, market state and settlement truth by causal time.
- [ ] Build versioned/disposable API/query output; do not create a second source of truth.

## J6 — Crowd-reaction feature extraction

- [ ] Implement generic event-window metrics parameterized at query/backtest time.
- [ ] Do not bake in noon/3 PM/5 PM hypotheses as storage rules.
- [ ] Preserve null/no-reaction controls.

## J7 — Backtest integration

- [ ] Feed synchronized histories into the existing Mercury backtest engine.
- [ ] Let strategy research choose arbitrary timing/tranching rules later.
- [ ] Keep hindsight-optimal results explicitly separate from causal paper/live strategies.

---

# Permanent acceptance/regression requirements

- [ ] Delete disposable derivations in a test database and regenerate identical outputs from raw journals.
- [ ] Raw payload/hash remains identical across parser/model upgrades.
- [ ] Excluding one day from replay does not mutate that day's raw data.
- [ ] Synthetic injection is visibly marked as replay overlay and cannot masquerade as historical receipt.
- [ ] Public state cannot see hidden MADIS information before public disclosure.
- [ ] Mercury state cannot use an observation before Mercury received/interpreted the necessary source record.
- [ ] Kalshi state cannot use messages received after the queried causal timestamp.
- [ ] Sequence-gap intervals cannot silently produce executable historical L2.
- [ ] Every displayed information/price transition can be traced back to immutable raw record ids/hashes.
- [ ] Settlement corrections/revisions do not overwrite earlier records.
- [ ] A full event timeline can be reconstructed with a different display/binning scheme without recollecting data.

---

# Scope discipline

This architecture intentionally does **not** decide now:

- whether immediate or delayed hidden-max entry is optimal;
- whether 15 minutes before a six-hour report is optimal;
- whether noon, 3 PM, 5 PM or another period is systematically best;
- which public report the crowd follows most;
- whether price changes are caused by reports versus generic time-of-day beliefs.

It ensures we preserve the raw causal evidence needed to answer all of those questions later with replay/statistical analysis instead of guesses.

The immediate goal remains simple:

> **Know exactly what Mercury knew, what was public, what Kalshi priced/traded, and what finally settled — while preserving the raw source layer so every interpretation can be rebuilt.**
