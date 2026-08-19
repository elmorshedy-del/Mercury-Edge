# Mercury Edge — Step 4 Canonical TODO

Status: **LOCKED IMPLEMENTATION PLAN — work from this checklist in order**

Branch: `paper-rigour-v2`

PR: #5 (`WIP: hard-state evidence and paper-rigour refactor`)

Safety rule: **Do not merge or deploy Step 4 until every required item and regression test below passes.**

## Purpose

Step 4 turns the existing hard-state proof work into a world-class, replaceable, auditable information architecture without changing the core economic thesis.

The active trading thesis remains intentionally narrow:

> A settlement-compatible fact proves one or more Kalshi outcomes impossible while executable NO contracts on those outcomes still trade below guaranteed settlement value after fees.

Mercury trades hard facts, not forecasts. Raw source data is immutable; interpretations are versioned and replayable.

---

# Step 4A — Canonical domain contracts

- [ ] Introduce explicit domain objects/types separating:
  - raw source message / raw source record;
  - normalized observation;
  - settlement-compatible evidence;
  - derived hard climate state;
  - bucket elimination result;
  - market/execution state;
  - post-trade settlement truth / validation.
- [ ] Strategy/execution code must never parse METAR/MADIS/DSM/CLI strings directly.
- [ ] Define an evidence interface that can represent at minimum:
  - current ASOS temperature evidence from the ordinary METAR main temperature field;
  - precise current ASOS temperature evidence from `T` groups;
  - six-hour maximum evidence from `1sTTT` groups carried inside the applicable routine METAR;
  - future MADIS OMO 1-minute evidence/reconstruction without requiring strategy rewrites;
  - conservative 24-hour/daily max evidence only if climate-date/timing semantics are unambiguous.
- [ ] Explicitly separate **trade-time evidence** from **validation/settlement truth**.
  - DSM must not trigger normal intraday trades.
  - CLI/Kalshi settlement must not trigger normal intraday trades.
  - DSM/CLI belong to audit, validation, and settlement grading.
- [ ] Add explicit evidence provenance fields: source, station, raw identifier/group, source record id/hash, observation time, source-publication/first-fetchable time when available, Mercury receipt time, interpretation time, climate date, evidence model version, parser version, calendar version, integrity status.
- [ ] Add explicit fail-closed integrity states instead of silently coercing ambiguous input.

### Step 4A acceptance tests

- [ ] Existing `TemperatureEvidence` / `HardStateProof` behavior remains correct.
- [ ] Strategy modules can consume canonical hard-state objects without knowledge of raw weather syntax.
- [ ] Domain serialization is deterministic and round-trippable.
- [ ] Unknown/ambiguous evidence types cannot accidentally become benchmark-eligible.

---

# Step 4B — Immutable raw journal + replaceable derivations

- [ ] Create/standardize an append-only raw-source journal for every source used by the hard-state pipeline.
- [ ] Preserve raw payload exactly as received (text/binary-safe representation as appropriate) plus a stable payload hash.
- [ ] Preserve all available clocks separately:
  - source observation time;
  - source generation/publication time when supplied;
  - first external fetchability time when measurable;
  - Mercury network receipt time;
  - Mercury parse/interpretation time.
- [ ] Never overwrite a raw source record because a parser or interpretation changes.
- [ ] Derived evidence must reference the immutable raw record(s) that produced it.
- [ ] Re-running a newer parser/evidence model over old raw data must create/reproduce a new derived interpretation without mutating the old raw record.
- [ ] Add model/version identifiers sufficient to answer: "which trades/signals were produced by parser/evidence/calendar/execution version X?"

### Step 4B acceptance tests

- [ ] Identical raw input hashes identically.
- [ ] Reprocessing the same raw record under the same software versions is deterministic/idempotent.
- [ ] Reprocessing under a changed evidence version can coexist with the prior derivation.
- [ ] No code path can update historical raw payload content.

---

# Step 4C — Live ASOS evidence channels

## Current METAR evidence

- [ ] Preserve the canonical Fahrenheit lattice logic already validated in Step 1.
- [ ] Main whole-C current-temperature field remains lossy and may establish only the minimum canonical Fahrenheit bound supported by its full inverse lattice.
- [ ] Precise `T` group remains a separate stronger current-temperature evidence item.
- [ ] Never use naïve continuous `C -> F -> round` as settlement proof.
- [ ] Off-lattice `T` values fail closed.
- [ ] Conflicting main/T evidence fails closed according to explicit integrity rules.

## Six-hour maximum evidence — established core evidence

- [ ] Treat the six-hour maximum as a distinct evidence item even though it is contained in a normal routine METAR.
- [ ] Preserve both facts when one report contains a lower current temperature and a higher six-hour maximum.
- [ ] Continue accepting a six-hour maximum as hard evidence only when its full six-hour interval belongs to the same target LST climate day.
- [ ] A hidden maximum may raise the daily hard-state lower bound even when current temperature has already fallen.
- [ ] Repeated six-hour/current evidence that does not raise the bound must corroborate state without retriggering a new hard-state transition.

## 24-hour/daily max group

- [ ] Support only after exact observation timing/climate-date association is unambiguous.
- [ ] Fail closed on ambiguous midnight-LST association.
- [ ] Do not let this work delay or complicate the active current/T/six-hour core if it proves operationally messy; isolate behind its own adapter/evidence type.

### Step 4C acceptance tests

- [ ] `31 C` alone does not prove 88 F.
- [ ] `32 C` establishes the correct lower bound from the canonical ASOS lattice.
- [ ] `T0311` proves canonical 88 F.
- [ ] `T0306` proves canonical 87 F.
- [ ] off-lattice `T0310` fails closed.
- [ ] a valid six-hour maximum can establish a hidden daily max above the current observation.
- [ ] a six-hour interval crossing the climate-day boundary cannot establish the target day's hard state.
- [ ] lower/repeated evidence cannot decrease the hard state or create a duplicate transition.

---

# Step 4D — Canonical monotonic hard-state accumulator

- [ ] Create one source-agnostic component responsible for the event's proven daily-high lower bound.
- [ ] It consumes only benchmark-eligible settlement-compatible evidence.
- [ ] State must be monotonic within the target climate day: e.g. `86 -> 87 -> 88`; ordinary later observations cannot reduce it.
- [ ] Preserve a complete append-only evidence history for every bound, including corroborating evidence that did not create a new transition.
- [ ] Record the **first Mercury-knowable transition time** for each newly proven bound.
- [ ] Record later public/corroborating disclosures separately rather than rewriting the first-known time.
- [ ] Explicitly support multiple information clocks:
  - when the physical observation occurred;
  - when Mercury could first know the hard fact;
  - when a commonly watched public disclosure later revealed/corroborated it.
- [ ] A later QC/settlement disagreement must be an audit event; it must not silently rewrite historical knowledge state.

### Step 4D acceptance tests

- [ ] same evidence stream + same versions => identical transition sequence.
- [ ] later lower current temperature cannot lower a prior hidden max.
- [ ] later six-hour max equal to an earlier precise/MADIS-derived bound is corroboration, not a new transition.
- [ ] event/climate-date mismatch can never contaminate another event.

---

# Step 4E — Pure bucket elimination engine

- [ ] Elimination receives only:
  - exact Kalshi event/market strike metadata;
  - canonical hard state.
- [ ] Elimination must not care which weather source produced the hard state.
- [ ] Derive every mathematically impossible bucket from the proven lower bound using exact strike semantics.
- [ ] Preserve a machine-readable proof for each eliminated bucket: bound, strike rule, evidence transition id, climate date.
- [ ] Fail closed if strike metadata is incomplete, ambiguous, mismatched to station/date, or cannot be represented exactly.
- [ ] Ensure no forecast probability or "likely winner" logic enters this component.

### Step 4E acceptance tests

- [ ] hard state `>= 88` eliminates every and only bucket whose maximum possible winning temperature is `< 88`.
- [ ] boundary values are tested explicitly.
- [ ] old Aug-18/Aug-19 wrong-date contamination is a permanent regression test.
- [ ] exact event date + station + LST climate date must agree before elimination.

---

# Step 4F — Active trading path: stale dead-NO only

- [ ] Keep the active benchmark trade deliberately simple.
- [ ] For each eliminated bucket, inspect the executable NO side.
- [ ] Calculate actual executable economics from ask/depth, fees, and fill quantity.
- [ ] Rank dead-NO opportunities mechanically by guaranteed economics and capacity; do not introduce weather forecasting.
- [ ] Default benchmark is hold to settlement.
- [ ] No survivor-YES basket or winner forecast is required for Step 4.
- [ ] Multiple dead NOs are routes on the same hard-state fact, not separate weather strategies.
- [ ] Shadow/research activity must never consume benchmark sleeve capital.

### Step 4F acceptance tests

- [ ] a non-dead bucket can never produce a benchmark dead-NO order.
- [ ] an eliminated bucket with no positive guaranteed net return after fees does not trade.
- [ ] execution uses current executable order-book economics, not midpoint or candle proxies, when live L2 is available.
- [ ] shadow-only configuration cannot spend benchmark cash.

---

# Step 4G — MADIS/LDM-ready extension point (no premature trust)

MADIS OMO 1-minute ASOS is a planned earlier information channel, potentially exposing a hidden hard maximum well before the six-hour max disclosure.

- [ ] Define a `MADIS_OMO_1MIN` source adapter contract now without making it benchmark-eligible yet.
- [ ] Preserve raw MADIS/LDM records and receipt ordering exactly when access becomes available.
- [ ] Design the reconstruction interface for raw 1-minute temperatures -> exact/versioned ASOS rolling five-minute climate-state reconstruction.
- [ ] Reconstruction logic must be isolated from source transport and from trading strategy code.
- [ ] Explicitly handle and test missing minutes, duplicate records, late/out-of-order arrival, corrections/QC flags, reconnects, and clock skew.
- [ ] Record observation time -> source/MADIS release time if available -> LDM receipt -> Mercury interpretation latency.
- [ ] Before promotion to benchmark evidence, validate reconstructed maxima over a substantial sample against precise T-groups, valid six-hour maxima, completed DSM, CLI, and settlement outcomes.
- [ ] Promotion from research/validation to hard-state eligible must be a versioned/configured trust-policy change, not an architecture rewrite.
- [ ] Historical archive availability must never be treated as contemporaneous live availability in replay.

### Step 4G acceptance tests/scaffolding

- [ ] source adapter can be added without changes to elimination/execution code.
- [ ] synthetic minute stream can be reconstructed deterministically.
- [ ] missing/out-of-order records fail closed or produce an explicit non-tradable reconstruction state.
- [ ] replay respects actual receipt time, never future archive knowledge.

---

# Step 4H — DSM / CLI / settlement auditor (validation only)

- [ ] Model DSM and CLI as validation/ground-truth inputs, not ordinary trade-time evidence.
- [ ] Parse/store complete target climate date and reported maximum with raw provenance.
- [ ] Distinguish preliminary/same-day CLI products from completed/final settlement truth; do not label every CLI issuance "final".
- [ ] Compare final/settlement truth to each hard-state transition used for trading.
- [ ] Surface any case where a supposedly impossible bucket later settles YES as a critical invariant failure requiring investigation.
- [ ] Preserve corrections/revisions instead of overwriting earlier product versions.

### Step 4H acceptance tests

- [ ] DSM/CLI cannot trigger a normal benchmark intraday signal.
- [ ] same-day preliminary CLI is not automatically considered final.
- [ ] final settlement grading can trace back to every raw evidence item that justified the trade.

---

# Step 4I — World-class debugging and explainability

- [ ] Every hard-state transition must expose a structured "why" trace containing:
  - event/market;
  - station;
  - climate date;
  - new proven lower bound;
  - newly dead bucket(s);
  - source type;
  - raw evidence group/value;
  - canonical interpretation;
  - observation/publication/receipt/interpretation timestamps;
  - software/model versions;
  - referenced immutable raw record hash/id.
- [ ] Every benchmark order/trade must link to the exact elimination and evidence transition that authorized it.
- [ ] Make it possible to inspect the original raw payload from a signal/trade audit record.
- [ ] Add structured error/fail-closed reason codes so ambiguous records can be counted and analyzed rather than disappearing.

---

# Step 4J — Deterministic replay as a first-class capability

- [ ] Replay an event from immutable weather + market journals in receipt-time order.
- [ ] Reproduce exactly:
  1. what raw information Mercury had;
  2. when it received it;
  3. what hard-state transitions occurred;
  4. which buckets became impossible;
  5. what executable Kalshi market existed at that time;
  6. what order/trade decision the configured engine made;
  7. eventual settlement outcome.
- [ ] Replay must be version-selectable for parser/calendar/evidence/elimination/execution logic.
- [ ] Changing one component version must permit A/B replay without altering source data.
- [ ] Prevent future-information leakage explicitly.

---

# Permanent regression suite

These are non-negotiable fixtures. Add each as an automated named regression if it is not already covered adequately.

- [ ] Wrong event day: Aug-19 Kalshi market cannot use Aug-18 weather.
- [ ] DST/civil-midnight: settlement climate day uses local standard time year-round.
- [ ] Main `31 C` cannot be naïvely rounded into proof of 88 F.
- [ ] `T0311` -> canonical 88 F.
- [ ] `T0306` -> canonical 87 F.
- [ ] off-lattice `T0310` fails closed.
- [ ] valid six-hour hidden max can raise hard state above current temp.
- [ ] six-hour max crossing target climate-day boundary is rejected.
- [ ] unproven decoded/research weather cannot contaminate deterministic hard state.
- [ ] repeated evidence for the same lower bound cannot retrigger a new hard-state transition.
- [ ] later current temperature below a known max cannot reduce the bound.
- [ ] research/shadow strategies cannot spend benchmark cash.
- [ ] incomplete/ambiguous Kalshi strike metadata cannot create an elimination.
- [ ] future MADIS missing-minute case cannot silently fabricate a settlement-compatible rolling-five-minute state.
- [ ] future MADIS out-of-order arrival must use receipt-time causal ordering in replay.

---

# Strategy / research boundary after Step 4

## Active benchmark trading now

- **Hard Information Arbitrage — dead-NO elimination only.**
- One core fact: settlement-compatible evidence establishes a hard lower bound that kills outcomes.
- Multiple dead-NO trades may be generated by the same hard-state transition if each remains independently guaranteed-profitable after fees.

## Established deterministic information mechanisms already in the core

- precise current ASOS `T`-group boundary crossing;
- settlement-compatible current ASOS evidence from the main field when the inverse lattice truly proves the bound;
- **six-hour hidden maximum disclosure** from valid `1sTTT` groups;
- correct LST climate-day/event matching.

## Planned earlier deterministic information channel

- MADIS OMO 1-minute ASOS -> validated rolling-five-minute reconstruction -> earlier hidden-max hard state.

This is not treated as speculative in concept; it is withheld from benchmark trading only until the exact feed/reconstruction/latency behavior is validated.

## Deterministic extensions preserved for later, not required for Step 4

- exhaustive surviving-YES constructions, including asymmetric sizing where every remaining settlement state still produces positive net P&L;
- multi-stage/sequential deterministic trades when one hidden hard state creates more than one stale market dislocation as public information catches up;
- precision/rounding asymmetry when precise settlement-compatible evidence already makes a bucket impossible.

These extensions must not complicate the initial dead-NO implementation.

## Statistical/forecast layer — explicitly later

- late-day likely-winner selection;
- probability of additional heating/rise;
- terminal-bucket forecasting;
- weather-regime prediction.

These may later sit above the deterministic infrastructure but must not contaminate the current hard-state benchmark.

## Research observations to preserve, not trade merely because they were observed

- LAX multi-stage price-shape/V behavior beyond proven deterministic eliminations;
- generic winner-transfer behavior;
- any rounding/precision behavior lacking hard settlement-compatible proof;
- other market-shape hypotheses discovered during replay.

Each hypothesis should eventually have status such as `UNTESTED`, `SUPPORTED`, `REJECTED`, or `PROMOTED`, with the original observation, mechanism, required data, backtest, failure cases, and decision preserved.

---

# Engineering rules for every Step 4 commit

- [ ] Work strictly in checklist order unless a failing test forces a documented dependency change.
- [ ] One coherent architectural change per commit where practical.
- [ ] Add/modify tests in the same commit or immediately following test commit.
- [ ] Run targeted tests first.
- [ ] Run the complete Python/Node/Docker CI suite after each completed substep.
- [ ] Record exact test count/result and commit SHA in `docs/HARD_STATE_REFACTOR.md`.
- [ ] Update this TODO by checking completed items only after tests pass.
- [ ] Any newly discovered architectural assumption or external-source ambiguity must be written into this file before implementing around it.
- [ ] Do not merge PR #5 or deploy Railway during Step 4 without explicit approval after the whole Step 4 checklist passes.
