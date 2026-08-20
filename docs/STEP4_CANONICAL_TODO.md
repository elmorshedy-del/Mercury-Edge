# Mercury Edge — Step 4 Canonical TODO

Status: **LOCKED IMPLEMENTATION PLAN — work from this checklist in order**

Branch: `paper-rigour-v2`

PR: #5 (`WIP: hard-state evidence and paper-rigour refactor`)

Safety rule: **Do not merge or deploy Step 4 until every required item and regression test below passes.**

Verified progress: **4A PASS; 4B PASS; 4C live current/T/six-hour core PASS with the 24-hour channel deliberately fail-closed/deferred; 4D PASS on run 298; 4E pure elimination PASS on run 312; 4F canonical stale dead-NO paper execution PASS on run 338; 4G-A MADIS OMO source/trust contract PASS on run 352; corrected 4G-B direct OMO five-minute-state research decoding PASS on run 399 (165 Python tests + Node + Docker + Postgres). Next implementation substep: 4G-C empirical MADIS/live-feed validation; information-visibility/market-reaction work remains a raw-first derived layer, not a trading-policy complication.**

## Purpose

Step 4 turns the existing hard-state proof work into a world-class, replaceable, auditable information architecture without changing the core economic thesis.

The active trading thesis remains intentionally narrow:

> A settlement-compatible fact proves one or more Kalshi outcomes impossible while executable NO contracts on those outcomes still trade below guaranteed settlement value after fees.

Mercury trades hard facts, not forecasts. Raw source data is immutable; interpretations are versioned and replayable.

---

# Step 4A — Canonical domain contracts

- [x] Introduce explicit domain objects/types separating:
  - raw source message / raw source record;
  - normalized observation;
  - settlement-compatible evidence;
  - derived hard climate state;
  - bucket elimination result;
  - market/execution state;
  - post-trade settlement truth / validation.
- [x] Strategy/execution code must never parse METAR/MADIS/DSM/CLI strings directly.
- [x] Define an evidence interface that can represent at minimum:
  - current ASOS temperature evidence from the ordinary METAR main temperature field;
  - precise current ASOS temperature evidence from `T` groups;
  - six-hour maximum evidence from `1sTTT` groups carried inside the applicable routine METAR;
  - future MADIS OMO one-minute-cadence direct five-minute climate-state evidence without requiring strategy rewrites;
  - conservative 24-hour/daily max evidence only if climate-date/timing semantics are unambiguous.
- [x] Explicitly separate **trade-time evidence** from **validation/settlement truth**.
  - DSM must not trigger normal intraday trades.
  - CLI/Kalshi settlement must not trigger normal intraday trades.
  - DSM/CLI belong to audit, validation, and settlement grading.
- [x] Add explicit evidence provenance fields: source, station, raw identifier/group, source record id/hash, observation time, source-publication/first-fetchable time when available, Mercury receipt time, interpretation time, climate date, evidence model version, parser version, calendar version, integrity status.
- [x] Add explicit fail-closed integrity states instead of silently coercing ambiguous input.

### Step 4A acceptance tests

- [x] Existing `TemperatureEvidence` / `HardStateProof` behavior remains correct.
- [x] Strategy modules can consume canonical hard-state objects without knowledge of raw weather syntax.
- [x] Domain serialization is deterministic and round-trippable.
- [x] Unknown/ambiguous evidence types cannot accidentally become benchmark-eligible.

---

# Step 4B — Immutable raw journal + replaceable derivations

- [x] Create/standardize an append-only raw-source journal for every source currently used by the hard-state pipeline.
- [x] Preserve raw payload exactly as received (text/binary-safe representation as appropriate) plus a stable payload hash.
- [x] Preserve all available clocks separately:
  - source observation time;
  - source generation/publication time when supplied;
  - first external fetchability time when measurable;
  - Mercury network receipt time;
  - Mercury parse/interpretation time.
- [x] Never overwrite a raw source record because a parser or interpretation changes.
- [x] Derived evidence must reference the immutable raw record(s) that produced it.
- [x] Re-running a newer parser/evidence model over old raw data must create/reproduce a new derived interpretation without mutating the old raw record.
- [x] Add model/version identifiers sufficient to trace parser/evidence/calendar state used by signals; execution-version linkage remains part of the later order/explainability audit layer.

### Step 4B acceptance tests

- [x] Identical raw input hashes identically.
- [x] Reprocessing the same raw record under the same software versions is deterministic/idempotent.
- [x] Reprocessing under a changed evidence version can coexist with the prior derivation.
- [x] No code path can update historical raw payload content; database triggers reject UPDATE/DELETE.

---

# Step 4C — Live ASOS evidence channels

## Current METAR evidence

- [x] Preserve the canonical Fahrenheit lattice logic already validated in Step 1.
- [x] Main whole-C current-temperature field remains lossy and may establish only the minimum canonical Fahrenheit bound supported by its full inverse lattice.
- [x] Precise `T` group remains a separate stronger current-temperature evidence item.
- [x] Never use naïve continuous `C -> F -> round` as settlement proof.
- [x] Off-lattice `T` values fail closed.
- [x] Conflicting main/T evidence fails closed according to explicit integrity rules.

## Six-hour maximum evidence — established core evidence

- [x] Treat the six-hour maximum as a distinct evidence item even though it is contained in a normal routine METAR.
- [x] Preserve both facts when one report contains a lower current temperature and a higher six-hour maximum.
- [x] Continue accepting a six-hour maximum as hard evidence only when its full six-hour interval belongs to the same target LST climate day.
- [x] A hidden maximum may raise the daily hard-state lower bound even when current temperature has already fallen.
- [x] Repeated six-hour/current evidence that does not raise the bound must corroborate state without retriggering a new hard-state transition.

## 24-hour/daily max group

- [ ] Support only after exact observation timing/climate-date association is unambiguous. **Deferred intentionally:** the parser recognizes the group, but the benchmark adapter admits none until the midnight-LST association is proven mechanically.
- [x] Fail closed on ambiguous midnight-LST association.
- [x] Do not let this work delay or complicate the active current/T/six-hour core; the 24-hour group is isolated and non-trading.

### Step 4C acceptance tests

- [x] `31 C` alone does not prove 88 F.
- [x] `32 C` establishes the correct lower bound from the canonical ASOS lattice.
- [x] `T0311` proves canonical 88 F.
- [x] `T0306` proves canonical 87 F.
- [x] off-lattice `T0310` fails closed.
- [x] a valid six-hour maximum can establish a hidden daily max above the current observation.
- [x] a six-hour interval crossing the climate-day boundary cannot establish the target day's hard state.
- [x] lower/repeated evidence cannot decrease the hard state or create a duplicate transition.

---

# Step 4D — Canonical monotonic hard-state accumulator

- [x] Create one source-agnostic component responsible for the event's proven daily-high lower bound.
- [x] It consumes only benchmark-eligible settlement-compatible evidence for the exact station, climate date, and calendar version.
- [x] State is monotonic within the target climate day: e.g. `86 -> 87 -> 88`; ordinary later observations cannot reduce it.
- [x] Preserve a complete append-only evidence/application history for every bound, including corroborating evidence that did not create a new transition.
- [x] Record the **first Mercury-knowable transition time** for each newly proven bound using interpretation-complete time when present, otherwise Mercury receipt time; observation time cannot authorize early knowledge.
- [x] Record later public/corroborating disclosures separately rather than rewriting the first-known time.
- [x] Explicitly support multiple information clocks:
  - when the physical observation occurred;
  - source publication / first-fetchability when available;
  - when Mercury received and interpreted the hard fact;
  - when a later disclosure/corroboration arrived.
- [ ] A later QC/settlement disagreement must be surfaced as an audit event. **Deferred to 4H:** the 4D journals are already immutable, so later truth cannot silently rewrite historical knowledge state.
- [x] Evidence learned in one network response is applied atomically: current/T/six-hour facts with the same Mercury-known timestamp can create at most one transition, at the strongest proven bound. This prevents invented intra-response trading windows.

### Step 4D acceptance tests

- [x] same evidence stream + same versions => identical transition sequence independent of input order.
- [x] later lower current temperature cannot lower a prior hidden max.
- [x] later six-hour max equal to an earlier precise/MADIS-derived bound is corroboration, not a new transition.
- [x] station/climate-date/calendar mismatch cannot contaminate another event/state.
- [x] causal ordering uses Mercury receipt/interpretation time, never an earlier physical observation timestamp.
- [x] same-receipt lower current + higher hidden max produces one atomic transition at the higher bound.
- [x] canonical DBN integration follows accumulator transition identity even if a legacy proof-trigger field is deliberately stale/wrong.
- [x] hard-state application and transition rows are append-only and database-immutable.

Verification: GitHub Actions **run 298** — **92 Python tests passed**, collector compile PASS, Docker build PASS, Node checks PASS, full Postgres migrations PASS, immutable evidence-journal regression PASS, immutable hard-state-timeline regression PASS.

---

# Step 4E — Pure bucket elimination engine

- [x] Elimination receives only:
  - exact Kalshi event/market strike metadata;
  - canonical hard state.
- [x] Elimination does not care which weather source produced the hard state.
- [x] Derive every mathematically impossible bucket from the proven lower bound using exact strike semantics.
- [x] Preserve a machine-readable proof for each eliminated bucket: bound, strike rule, evidence transition id/context, climate date, hard-state id and event rules hash.
- [x] Fail closed if strike metadata is incomplete, ambiguous, mismatched to station/date, or cannot be represented exactly.
- [x] Ensure no forecast probability or "likely winner" logic enters this component.

### Step 4E acceptance tests

- [x] hard state `>= 88` eliminates every and only bucket whose maximum possible winning temperature is `< 88`.
- [x] boundary values are tested explicitly; equality to the cap remains alive and an unbounded upper tail cannot be killed by a lower-bound-only state.
- [x] old Aug-18/Aug-19 wrong-date contamination is a permanent regression test.
- [x] exact event date + station + LST climate date must agree before elimination.

Verification: GitHub Actions **run 312** — **103 Python tests passed**, Python compile PASS, collector Docker PASS, Node PASS, full Postgres migrations PASS. `paper_collector/bucket_elimination.py` is authoritative for hardened DBN bucket death.

---

# Step 4F — Active trading path: stale dead-NO only

- [x] Keep the active benchmark trade deliberately simple: canonical eliminated bucket -> executable NO -> fee-adjusted guaranteed economics -> simulated paper order/fill/position.
- [x] For each eliminated bucket, inspect the executable NO side reconstructed from causal Kalshi L2.
- [x] Calculate actual executable economics from ask/depth, fill quantity, fees and cash-rounding behavior; guaranteed payout/profit/ROI are stored on the order audit.
- [x] Rank dead-NO opportunities mechanically by exact guaranteed ROI/profit/capacity; do not introduce weather forecasting.
- [x] Default benchmark is hold to settlement; Step 4F adds no early-exit/forecast logic.
- [x] No survivor-YES basket or winner forecast is required for Step 4.
- [x] Multiple dead NOs are routes on the same hard-state fact, not separate weather strategies.
- [x] Shadow/research activity remains isolated and cannot consume benchmark sleeve capital.
- [x] Existing per-mode `max_no_price` values remain **portfolio guards only**. They are not treated as proof of edge; a fill must independently have strictly positive guaranteed net return after exact fees.

### Step 4F acceptance tests

- [x] a non-dead, missing-proof or mismatched-elimination candidate is rejected before market lookup and can never produce a benchmark dead-NO order.
- [x] an eliminated bucket with no positive guaranteed net return after exact fees/cash rounding does not trade.
- [x] execution uses current executable order-book economics reconstructed from causal live L2; absence of L2 blocks the benchmark trade and never falls back to midpoint/candle proxies.
- [x] shadow-only configuration cannot spend benchmark cash.
- [x] successful paper orders link exact elimination id + hard-state transition and persist guaranteed payout/profit/ROI, actual fills, fees, L2 snapshot identity and execution-model versions.

Verification: GitHub Actions **run 338** — **117 Python tests passed**, Python compile PASS, collector Docker PASS, Node PASS, full Postgres migrations PASS, immutable journal/timeline tests PASS. Hardened DBN now routes benchmark execution through `paper_collector/dead_no_executor.py`; pure exact execution math lives in `paper_collector/dead_no_execution.py`.

---

# Step 4G — MADIS/LDM-ready extension point (no premature trust)

MADIS OMO is a planned earlier information channel. The dataset is one-minute cadence, but authoritative ASOS documentation establishes that OMO air temperature is already the ASOS running five-minute climate temperature reported each minute. Mercury therefore decodes the OMO state directly and **does not re-average five OMO records**.

- [x] **4G-A:** Define a `MADIS_OMO_1MIN` source adapter contract without making it benchmark-eligible. The enum retains the dataset/cadence name; the adapter preserves official MADIS `T` in Kelvin, `TSS`, immutable raw provenance and separate clocks. Raw OMO wire evidence is `RESEARCH_ONLY` and cannot raise benchmark hard state.
- [ ] Preserve raw MADIS/LDM records and receipt ordering exactly when live access becomes available. **Storage contract is already compatible through the generic immutable raw journal; actual LDM transport remains pending access/feed details.**
- [x] **4G-B:** Correctly model OMO temperature as an ASOS running-five-minute state on a one-minute cadence, then decode `T(K)` through a versioned inverse lattice: canonical whole °F -> documented ASOS 0.1°C OMO encoding -> Kelvin -> configured MADIS storage representation. Unique direct states can become `RESEARCH_ONLY` evidence; no second rolling average is permitted.
- [x] MADIS source/decoding boundaries are architecturally isolated from bucket elimination and dead-NO execution; 4G-A/B required no changes to either component.
- [x] Direct research decoding explicitly handles missing observations without interpolation, exact duplicates idempotently, late/out-of-order arrival causally, conflicting same-minute values fail-closed, QC status, and verified `TSS=0`. **Live reconnect/sequence-gap distributions remain part of 4G-C transport validation.**
- [x] The source contract records observation time -> source/MADIS release time if available -> first-fetchability if available -> LDM/Mercury receipt -> Mercury interpretation latency. **Actual live latency distributions remain pending feed access.**
- [ ] **4G-C:** Before promotion to benchmark evidence, empirically establish the actual MADIS Kelvin storage/rounding representation and validate direct OMO states/maxima over a substantial sample against precise T-groups, valid six-hour maxima, completed DSM, CLI, and settlement outcomes.
- [x] Promotion from research/validation to hard-state eligible is structurally a separately versioned trust-policy change. Raw OMO and direct B2 evidence remain non-benchmark until explicit empirical promotion.
- [ ] Historical archive availability must never be treated as contemporaneous live availability in replay.

### Step 4G acceptance tests/scaffolding

- [x] source adapter was added without changes to elimination/execution code.
- [x] a synthetic OMO state is decoded deterministically through the documented whole-°F -> 0.1°C -> Kelvin source lattice plus explicit MADIS storage policy.
- [x] missing OMO minutes cannot create/interpolate a state; conflicting same-minute states fail closed; late/out-of-order observations remain knowable only at actual Mercury receipt/interpretation time.
- [x] direct OMO five-minute evidence remains `RESEARCH_ONLY` and cannot alter benchmark hard state.
- [ ] replay respects actual receipt time and live-capture availability, never future archive knowledge. **4G-C/J.**

4G-A verification: GitHub Actions **run 352** — **131 Python tests passed**, Python compile PASS, collector Docker PASS, Node PASS, full Postgres migrations PASS, immutable journal/timeline tests PASS. See `docs/STEP4G_A_VERIFICATION.md`.

Corrected 4G-B verification: GitHub Actions **run 399 (`32395889553`)** — **165 Python tests passed**, Python compile PASS, collector Docker PASS, Node PASS, full Postgres migrations PASS, immutable weather/evidence journal PASS, immutable hard-state timeline PASS, immutable Kalshi market journal PASS. See `docs/STEP4G_B_PLAN.md` and `docs/INFORMATION_VISIBILITY_VERIFICATION.md`.

No MADIS evidence is benchmark eligible yet.

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

- [x] Wrong event day: Aug-19 Kalshi market cannot use Aug-18 weather.
- [x] DST/civil-midnight: settlement climate day uses local standard time year-round.
- [x] Main `31 C` cannot be naïvely rounded into proof of 88 F.
- [x] `T0311` -> canonical 88 F.
- [x] `T0306` -> canonical 87 F.
- [x] off-lattice `T0310` fails closed.
- [x] valid six-hour hidden max can raise hard state above current temp.
- [x] six-hour max crossing target climate-day boundary is rejected.
- [x] unproven decoded/research weather cannot contaminate deterministic hard state.
- [x] repeated evidence for the same lower bound cannot retrigger a new hard-state transition.
- [x] later current temperature below a known max cannot reduce the bound.
- [x] research/shadow strategies cannot spend benchmark cash.
- [x] incomplete/ambiguous Kalshi strike metadata cannot create an elimination.
- [x] MADIS OMO missing-minute case cannot silently fabricate/interpolate a climate state; directly received OMO states stand alone.
- [x] MADIS late/out-of-order arrival uses Mercury receipt/interpretation causality and is never backdated to the physical observation timestamp.
- [ ] historical MADIS/archive replay must prove that future archive availability cannot leak into an earlier causal state. **4G-C/J.**

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

- MADIS OMO one-minute-cadence ASOS -> empirically validated direct OMO current-five-minute-state decoding -> earlier hidden-max hard state.

This is not treated as speculative in concept; it is withheld from benchmark trading until the exact live feed/storage representation, causality, latency and agreement with authoritative comparison sources are validated.

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