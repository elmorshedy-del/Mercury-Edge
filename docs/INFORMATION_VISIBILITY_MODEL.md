# Mercury Edge — Information Visibility Model

Status: **data/interpretation foundation; no trading-policy change**

## Goal

For every weather-market event, preserve enough causal data to reconstruct four synchronized histories:

1. **Mercury information** — what Mercury could derive and when it completed that interpretation.
2. **Ordinary-public information** — what settlement-relevant information was available through the supported ordinary public product path and when Mercury first observed it publicly available.
3. **Kalshi market belief/action** — the actual executable order book, quote changes, trades, depth and timing.
4. **Settlement truth** — the later validation/final result that determines whether the information interpretation was correct.

The system must not pretend to know what an individual trader actually saw. `ordinary_public` is a **crowd-information proxy**: information available through the ordinary public product path. Market behavior is measured separately from the raw Kalshi journal.

## Why this matters

A hidden-information edge is not just:

`Mercury knew X -> X settled true.`

The research question is:

`Mercury knew X -> ordinary public information still exposed only Y -> Kalshi continued pricing as if Y mattered -> ordinary public information later caught up to X -> Kalshi repriced -> settlement confirmed X.`

That timeline allows later backtests to answer questions such as:

- Which public reports actually coincide with price changes?
- How long does Kalshi remain stale after a report becomes public?
- Does the market typically react to ordinary current temperature, precise T-groups, six-hour maxima, or simply elapsed time/remaining-heating expectations?
- When Mercury has a hidden maximum, does the dead-NO become cheaper before the ordinary public disclosure catches up?
- How much executable depth exists at discovery, during the hidden-information window, and immediately before/after public disclosure?
- At what times of day do price changes occur without any new authoritative public weather information?

These are downstream statistical questions. The current architecture should record facts and clocks, not bake an explanation into the live trader.

## Existing raw capture foundation

### Weather

`paper_collector/weather_collector.py` already preserves:

- the exact AWC HTTP entity bytes before parsing;
- raw METAR text;
- observation time;
- AWC `receiptTime` in the parsed weather row;
- Mercury request start and response-completion times;
- Mercury monotonic receipt timing;
- immutable raw-source identity/hash.

### Kalshi

`paper_collector/collector.py` already preserves:

- raw WebSocket messages;
- order-book snapshots and deltas;
- trades;
- exchange timestamps when supplied;
- Mercury receipt time down to nanoseconds;
- sequence continuity;
- payload hashes/hash chaining;
- periodic REST order-book audit cross-checks.

Those raw journals remain authoritative. Derived visibility models are replaceable/versioned interpretations and must never rewrite raw data.

## Visibility classes

`paper_collector/information_visibility.py` defines the current v1 classification:

- `ordinary_public` — supported ordinary NOAA/AWC ASOS/METAR evidence: main current temperature, precise T-group current temperature, six-hour maximum, and eventually an admitted 24-hour maximum product.
- `specialized_public` — MADIS/OMO raw or reconstructed evidence. This may be publicly obtainable infrastructure but is deliberately distinguished from the ordinary report path that most market participants are likely to follow.
- `validation_only` — DSM, CLI and Kalshi settlement truth. These are for audit/grading and cannot be treated as ordinary intraday crowd information.
- `unknown` — source/provenance that cannot be classified safely.

This classification says **where the information was available**, not whether the crowd actually consumed it.

## Causal availability clocks

Physical observation time never authorizes public knowledge.

For each disclosure, v1 uses the earliest defensible availability clock in this order:

1. explicit `first_fetchable_at`, if measured;
2. explicit true `source_published_at`, if supplied;
3. for ordinary-public products, Mercury's first successful public fetch/receipt as a conservative upper bound;
4. for specialized feeds, Mercury receipt time.

If better first-fetchability measurement becomes available later, the visibility model can be versioned and replayed without changing the raw journals.

## Public information state

The model distinguishes two different concepts that must not be collapsed:

- **public daily-high lower bound** — monotonic; once the public has evidence the day reached at least 77 F, a later 74 F current observation cannot erase it;
- **latest public current observation** — non-monotonic; it can rise or fall and is selected by observation time, not blindly by arrival order.

A delayed older report can therefore raise the known historical daily maximum without replacing a newer current observation.

## Hidden-information window

For a Mercury-discovered bound `X`, the key timestamps are:

- `mercury_known_at(X)` — when Mercury completed or received the settlement-compatible interpretation;
- `ordinary_public_catch_up_at(X)` — earliest defensible time ordinary-public evidence itself proves at least `X`.

The interval between them is the **information-lead window**. Kalshi order-book/trade data during that interval are what later analysis should use to test execution timing and capital-allocation ideas.

Example:

```text
13:12:03  Mercury specialized reconstruction proves daily high >= 77 F
13:12:03  ordinary public state still proves only >= 75 F
13:12-15:53 Kalshi quotes/trades are recorded continuously
15:53:06  ordinary public six-hour maximum proves >= 77 F
15:53:xx  subsequent Kalshi repricing is measured, not assumed
later      settlement/CLI/DSM used to grade the interpretation
```

## Design rule

Do **not** build a crowd-psychology model into the hard-state or execution engine.

The live system should continue to do the simple deterministic job:

`raw evidence -> canonical interpretation -> hard state -> dead bucket -> executable trade`

The information-visibility layer exists so replay/backtest can later explain price changes and test optimal execution using the synchronized raw facts.
