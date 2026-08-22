# Information Visibility / MADIS Semantics Verification

Date: 2026-08-20

Branch: `paper-rigour-v2`

PR: #5

Safety status: **research/paper hardening only — no merge, production deploy, or real-money enablement performed.**

## Objective

Preserve and reconstruct four causal histories without inventing trader psychology:

1. what Mercury knew and when;
2. what the configured ordinary-public weather path exposed and when;
3. what Kalshi actually quoted/traded and when;
4. what later validation/settlement truth established.

The ordinary-public state is a crowd-information proxy, not a claim that a specific trader consumed a specific report.

## Information-visibility foundation — VERIFIED

Primary implementation:

- `paper_collector/information_visibility.py`
- `paper_collector/test_information_visibility.py`
- `docs/INFORMATION_VISIBILITY_MODEL.md`
- `docs/INFORMATION_VISIBILITY_J1_AUDIT.md`

The model now preserves:

- ordinary-public vs specialized-public vs validation-only vs unknown visibility;
- first-fetchable / source-publication / Mercury-first-public-fetch / Mercury-receipt availability bases;
- no use of physical observation time as a proxy for public knowledge;
- source, product, station, climate date, raw source IDs/hash where available;
- parser/evidence/calendar/visibility policy versions;
- separate ordinary-public main-current and precise-T-group facts;
- latest six-hour maximum disclosure;
- monotonic ordinary-public daily-high lower bound;
- deterministic public-disclosure IDs and public-information states;
- direct computation of the earliest ordinary-public catch-up time for a Mercury-known bound.

Verification: GitHub Actions **run 391 (`32395097442`)**:

- **154 Python tests passed**;
- Python compile PASS;
- collector Docker build PASS;
- Node checks PASS;
- full Postgres migrations PASS;
- immutable weather/evidence journal tests PASS;
- immutable hard-state timeline tests PASS;
- immutable Kalshi raw market journal test PASS.

## Raw market capture hardening — VERIFIED

The existing Kalshi collector already records raw WebSocket order-book snapshot/delta and trade messages with sequence/connection identity, exchange timestamps when supplied, Mercury wall/monotonic receipt clocks, raw text and hashes.

One gap found during the raw-coverage audit was corrected:

- `sql/017_market_data_journal_immutability.sql` adds database-level UPDATE/DELETE rejection to `market_data_journal`;
- `sql/tests/017_market_data_journal_immutability_test.sql` permanently tests that invariant.

This means later market-state/reaction features can be replaced and recomputed without mutating the exchange-message evidence they came from.

## MADIS OMO semantic correction — VERIFIED

The initial working assumption that MADIS OMO air-temperature `T` was a raw one-minute sensor average requiring Mercury to build a second rolling-five-minute average was corrected before benchmark promotion.

The authoritative ASOS semantics are now encoded in the architecture:

- ASOS produces a running five-minute temperature each minute;
- that five-minute state is rounded/stored as whole °F;
- the whole-°F state is converted to the OMO/METAR Celsius representation;
- the OMO temperature is therefore already the current ASOS five-minute climate state on a one-minute reporting cadence;
- MADIS stores the OMO `T` variable in Kelvin.

Mercury therefore **does not re-average five MADIS OMO records**.

Correct research decoding path:

`canonical whole °F -> documented ASOS 0.1°C OMO lattice -> Kelvin -> configured MADIS storage representation`

and inversely:

`MADIS OMO T(K) -> versioned inverse lattice -> unique canonical whole °F -> RESEARCH_ONLY direct OMO five-minute evidence`

Important regression: canonical **88°F -> 31.1°C -> 304.25 K** before any configured MADIS storage quantization. A direct physical 88°F -> Kelvin calculation is not the OMO source encoding model.

Primary implementation:

- `paper_collector/madis_omo.py`
- `paper_collector/madis_temperature_mapping.py`
- `paper_collector/test_madis_temperature_mapping.py`
- `docs/STEP4G_B_PLAN.md`

Direct OMO research evidence:

- requires a unique inverse mapping under an explicit/versioned MADIS storage policy;
- requires acceptable source/QC status and verified `TSS=0`;
- preserves actual Mercury receipt/interpretation causality;
- performs no interpolation for missing OMO minutes;
- performs no second rolling average;
- makes conflicting accepted values for the same physical observation minute fail closed;
- is idempotent for exact duplicate inputs;
- remains `RESEARCH_ONLY` and is rejected by the benchmark hard-state accumulator.

Verification: GitHub Actions **run 399 (`32395889553`)**:

- **165 Python tests passed**;
- Python compile PASS;
- collector Docker build PASS;
- Node checks PASS;
- all Postgres migrations PASS;
- immutable hard-information journal PASS;
- immutable hard-state timeline PASS;
- immutable Kalshi raw market journal PASS.

## Remaining gaps before claiming full information lifecycle

These are intentionally still open:

1. **Live MADIS transport / empirical storage policy** — the actual Kelvin quantization/rounding representation of the chosen live LDM/MADIS path must be measured from real captures. No benchmark trust promotion is allowed before this validation.
2. **MADIS empirical validation** — compare direct OMO states/maxima against precise T-groups, valid six-hour maxima and completed-day truth over a substantial sample, while measuring missing/conflicting/QC/reconnect/latency behavior.
3. **Settlement validation collectors** — DSM, CLI and final Kalshi settlement still need full raw-first lifecycle handling for complete Step 4H/J replay.
4. **Derived market-state/reaction layer** — raw Kalshi coverage exists, but the reusable synchronized derived layer that joins public disclosures / Mercury state / exact book+trade state / settlement truth remains to be built. It must be a projection over raw journals, not a replacement for them.
5. **Public first-fetchability precision** — current AWC first successful poll is a conservative upper bound where a true first-fetchable/publication timestamp is unavailable. This uncertainty must remain explicit in reaction-latency analysis.

## Result

The architecture now supports the user's intended research question without baking timing folklore into the live trader:

> What did Mercury know, what did the ordinary public information path show, what was Kalshi pricing/trading, when did the ordinary public information catch up, and what ultimately settled?

Optimal execution timing, staged capital deployment, report-cycle effects and time-of-day crowd behavior should be tested later from those synchronized causal histories rather than hard-coded now.
