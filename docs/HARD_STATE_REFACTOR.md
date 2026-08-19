# Mercury hard-state refactor log

Branch: `paper-rigour-v2`
Draft PR: `#5` — do not merge/deploy until the final exact-build replay passes.

Goal: rebuild the deterministic weather edge around settlement-compatible evidence that eliminates outcomes, with every behavioral change isolated, tested, and documented before the next step.

## Ground rules

1. No live/benchmark behavior changes until the prerequisite evidence/calendar layer has passed tests.
2. Raw METAR/SPECI groups are the evidentiary record for ASOS hard-state logic; decoded Celsius values are metadata, not a primitive Fahrenheit measurement.
3. Celsius may be informative only through the forward ASOS encoding lattice. Never infer a canonical Fahrenheit state by continuous C→F conversion and rounding.
4. Every hard-state trigger must retain source, raw group, observation time, first-seen time, climate date, provenance, and compatibility/QC grade.
5. Predictive hypotheses and confirmed-impossibility signals are separate cohorts and never share benchmark performance statistics.
6. Real-money execution remains disabled.

## Authoritative semantics used

- NWS ASOS information: rolling 5-minute temperature averages are stored in whole degrees Fahrenheit and used for climate highs; transmitted Celsius is derived from that state.
- NWS climate reporting: the ASOS daily summary covers 00:00–23:59 **local standard time (LST)**, including during daylight-saving months.
- NWS observation FAQ: daily highs can occur between hourly METARs; 6-hour max groups and DSM/CLI may reveal values absent from hourly snapshots; erroneous ASOS data can be corrected before/during CLI QC.

References:
- https://www.weather.gov/psr/HiResASOS
- https://www.weather.gov/lot/weather_observations_faq
- https://www.weather.gov/asos/InformationReporting.html
- https://www.weather.gov/lox/asostemperature

## Step plan

1. **ASOS evidence lattice/parser** — pure raw-METAR proof objects; no trading integration.
2. **LST climate-day calendar** — replace civil-midnight semantics and correctly map max-group windows.
3. **Hard-state proof integration** — DBN/DSN/SBK/HSR consume proof objects, never generic C→F decoded values.
4. **First-class hidden-max events / canonical information architecture** — immutable raw evidence, source-neutral evidence/state, current/T/six-hour channels, monotonic state; 24-hour/DSM/CLI admitted only with explicit lifecycle semantics.
5. **Predictive vs confirmed separation** — hidden-max prediction stays research-only; confirmed impossible buckets form the deterministic core.
6. **Focused reaction/capacity research** — replace random Cartesian parameter grids with reaction-time and executable-capacity curves.
7. **Hard-state allocator** — HSR chooses among equivalent certain-payoff constructions using fee-adjusted executable dollar return.
8. **QC/settlement grades and recycling** — H1/H2/H3/final provenance, revision tracking, finalized settlement and capital recycling.
9. **Full regression + exact-build replay** — Aug 16/17/18 plus invariant suite; only then merge/deploy.

---

## Step 1 — ASOS evidence lattice/parser

Status: **PASS — local tests + GitHub CI; not yet wired into trading behavior.**

Files:
- `paper_collector/asos_evidence.py`
- `paper_collector/test_asos_evidence.py`

### Design

The module models the documented forward pipeline:

`canonical whole °F -> nearest 0.1 °C -> whole °C main METAR field`

It builds a finite inverse lattice and returns `TemperatureEvidence` proof objects containing:

- evidence kind
- exact raw METAR group
- encoded Celsius value
- all compatible canonical whole-°F states
- proven lower/upper Fahrenheit bounds
- integrity status (`canonical`, `ambiguous_lattice`, `off_lattice`)
- hard-state eligibility

Bucket elimination is intentionally expressed as:

`evidence.proven_min_f > market_upper_bound_f`

not as a Celsius round-trip.

### Required regression cases

- 85°F -> 29.4°C -> main 29°C
- 86°F -> 30.0°C -> main 30°C
- 87°F -> 30.6°C -> main 31°C
- 88°F -> 31.1°C -> main 31°C
- 89°F -> 31.7°C -> main 32°C
- 90°F -> 32.2°C -> main 32°C
- raw T-group 31.1°C -> exact 88°F
- raw T-group 30.6°C -> exact 87°F
- raw T-group 31.0°C -> off-lattice, fail closed
- main 31°C -> {87°F, 88°F}, cannot eliminate an 86–87 bucket
- main 32°C -> {89°F, 90°F}, does eliminate an 86–87 bucket
- 6-hour max `10311` -> exact 88°F
- KLAX 24-hour group `402500194` -> max exact 77°F
- negative-temperature encoding remains correct

### Verification

Local command:

```bash
python -m unittest -v test_asos_evidence.py
```

Result: **11 passed, 0 failed.**

GitHub Actions `Paper Trader CI` run 180:
- collector compile: PASS
- existing strategy tests + ASOS evidence tests: PASS
- collector Docker build: PASS
- Node checks: PASS

### Explicit non-changes

Step 1 did **not** alter `weather_collector.py`, `market_calendar.py`, DBN/HSR execution, portfolio state, or Railway. The evidence primitive was proved first; integration is deferred to Step 3.

---

## Step 2 — Local-standard-time climate calendar

Status: **PASS — local tests + GitHub CI.**

Files:
- `paper_collector/market_calendar.py`
- `paper_collector/test_market_calendar.py`

### Design

Settlement-day logic now distinguishes ordinary civil local time from the fixed **local standard time** clock used for NWS/ASOS climate days.

New primitives:
- `standard_utc_offset(timezone_name, year)`
- `local_standard_time(value, timezone_name)`
- `climate_date(value, timezone_name)`
- `climate_day_bounds(day, timezone_name)`
- `six_hour_window_within_climate_day(observed_at, timezone_name)`

`event_matches_observation()` now compares a Kalshi event date with the observation's **climate date**, not its civil date.

`confirmed_same_day_high()` now queries 00:00–24:00 LST and only accepts an AWC six-hour maximum when the full six-hour lookback is inside that same climate day. Step 2 deliberately leaves evidence-source filtering unchanged; that is Step 3.

### Required regression cases

- New York Aug 19 00:30 EDT = Aug 18 climate date.
- New York Aug 19 01:00 EDT = Aug 19 climate date.
- Los Angeles Aug 17 00:30 PDT = Aug 16 climate date.
- Phoenix has no DST displacement; its climate boundary remains civil midnight.
- Standard offsets: ET -5, CT -6, MT/AZ -7, PT -8.
- August New York climate day = 05:00Z to 05:00Z.
- August Los Angeles climate day = 08:00Z to 08:00Z.
- Winter and summer use the same standard-time UTC boundary.
- KLAX 15:53 LST six-hour max window is valid inside the climate day.
- KLAX 03:53 LST six-hour max window is rejected because it crosses climate midnight.
- `confirmed_same_day_high()` uses LST query bounds and excludes a cross-midnight `maxT`.

### Verification

Local command:

```bash
python -m unittest -v test_market_calendar.py
```

Result: **10 passed, 0 failed.**

GitHub Actions `Paper Trader CI` run 186:
- collector compile: PASS
- existing strategy tests + Step 1 + Step 2 tests: PASS
- collector Docker build: PASS
- Node checks: PASS

### Behavioral scope

Step 2 changes only date/window semantics in the hardened branch. It does **not** yet make decoded weather values settlement-grade proof. The legacy `temperature_f`/`max_temperature_f` aggregation remains in place until Step 3 replaces it with the tested evidence objects.

---

## Step 3A — Causal raw-ASOS daily proof aggregator

Status: **PASS — local tests + GitHub CI; still isolated from execution.**

Files:
- `paper_collector/hard_state_proof.py`
- `paper_collector/test_hard_state_proof.py`

### Design

`HardStateProof` is the deterministic daily-high lower-bound object consumed by the next integration step. It queries only causally available `NOAA_AWC` rows with raw METAR/SPECI text inside the same LST climate day. It ignores the collector's decoded `temperature_f` and `max_temperature_f` fields.

Accepted proof inputs in Step 3A:
- main METAR temperature field, interpreted only as its inverse whole-F lattice set;
- precise T group;
- six-hour maximum group, only when the complete six-hour interval is inside the same LST climate day.

Deferred to Step 4:
- 24-hour max group;
- DSM;
- final CLI.

Fail-closed row checks:
- any off-lattice temperature evidence rejects the whole raw report for hard-state use;
- a precise T group inconsistent with the main temperature field rejects the row;
- a six-hour maximum below the precise current temperature in the same report rejects the row.

The daily proof is the maximum of every accepted record's `proven_min_f`. The trigger is the **first causal report that establishes a new bound**; later repeats of the same bound do not generate a second transition.

Grades introduced:
- `H1_CURRENT` — current main/T evidence;
- `H2_SIX_HOUR_MAX` — valid same-climate-day six-hour maximum.

### Required regression cases

- `T0311` => daily lower bound 88°F, kills upper bound 87°F.
- main `31°C` => lower bound 87°F, does not kill upper 87°F.
- main `32°C` => lower bound 89°F.
- `T0310` off-lattice => no proof from that row.
- contradictory main/T fields => no proof from that row.
- KLAX current temperature below the hidden six-hour max => six-hour group raises the daily lower bound.
- cross-midnight six-hour max => ignored; current evidence can still contribute.
- repeated same bound => no new transition.
- later higher bound => new transition.
- causal query uses the LST climate-day bounds.

### Verification

Local command:

```bash
python -m unittest -v test_hard_state_proof.py
```

Result: **10 passed, 0 failed.**

GitHub Actions `Paper Trader CI` run 194:
- collector compile: PASS
- existing tests + Steps 1/2/3A tests: PASS
- collector Docker build: PASS
- Node checks: PASS

### Behavioral scope

Step 3A is intentionally pure proof construction. DBN/DSN/SBK/HSR still have not been switched to the proof object. That integration is Step 3B and must pass its own tests before Step 4 begins.

---

## Step 3B — Hard-state strategy integration

Status: **PASS — GitHub CI; deterministic strategy behavior now uses the proof object.**

Files changed:
- `paper_collector/paper_engine_hardened.py`
- `paper_collector/strategy_runtime_hardened.py`
- `paper_collector/Dockerfile`
- `paper_collector/test_hard_state_integration.py`

### DBN behavior

The hardened DBN path now calls `hard_state_proof.proof_for_weather()` and returns without a signal unless the current weather row is the causal trigger that raises the day's proven lower bound. Bucket death is evaluated by `proof.proves_above(market_upper_bound)`.

The collector's decoded `temperature_f`/`max_temperature_f` cannot raise or lower this deterministic bound. DBN signal/order evidence retains the complete raw proof payload, proof version, LST climate date, trigger grade, raw group, and source weather IDs.

### DSN/SBK/HSR behavior

`strategy_runtime_hardened.py` now has two explicitly separate weather paths:

- **hard core** (`DSN`, `SBK`, `HSR`): constructed only from a newly raised `HardStateProof` and receives `proven=True` from that proof;
- **research** (`WTY`, `RMO`, `PRV`, `LVP`, `HMF`): preserves the legacy weather context for now and is labelled `research_legacy_weather_semantics` where relevant.

This means a probabilistic/decoded weather value cannot leak into the hard-core bucket-death set.

Benchmark isolation remains unchanged: `paper_trade_enabled=true` is insufficient by itself. A bundle must also have `confidence_gate=approved_only` and `auditor_status=approved`. Shadow-only HSR/DSN/SBK therefore cannot spend benchmark cash.

### Required regression cases

- decoded 87.8°F + raw proof lower bound 87°F => upper-87 bucket remains alive;
- raw proof lower bound 88°F => upper-87 bucket is dead and DBN candidate uses exactly 88°F;
- repeated same proof bound => no second DBN trigger;
- missing/off-lattice raw proof => no DBN hard-state trigger;
- 00:30 EDT maps only to the previous LST climate-date event;
- DBN signal audit stores the raw proof payload;
- hard strategy weather view overrides a low decoded current value with the proof lower bound;
- HSR is constructed from the proof and carries the proof provenance;
- a six-hour hidden maximum can kill a bucket while current temperature is lower;
- no hard proof => no DSN/SBK/HSR bundle;
- shadow-only configuration cannot spend benchmark capital.

### Verification

GitHub Actions `Paper Trader CI` run 206:
- Python compile including hardened DBN/runtime: PASS
- existing strategy tests + Steps 1/2/3A + Step 3B integration tests: PASS
- collector Docker build with `asos_evidence.py` and `hard_state_proof.py`: PASS
- Node checks: PASS

### Behavioral scope

Step 3 is complete for the deterministic hard-state core. The richer immutable evidence/state lifecycle is implemented in Step 4A-D below.

---

## Step 4A — Canonical source-neutral information contracts

Status: **PASS — covered by the full Step 4D regression run.**

Files:
- `paper_collector/hard_information_domain.py`
- `paper_collector/test_hard_information_domain.py`
- adapters in `paper_collector/hard_state_proof.py`

### Design

The deterministic pipeline now has explicit contracts for:
- immutable raw-source references;
- normalized observations;
- settlement evidence;
- monotonic hard climate state;
- bucket-elimination facts;
- executable market state;
- settlement/validation truth.

Weather parsers are upstream adapters. Strategy/elimination code no longer needs METAR syntax. Evidence trust is explicit (`benchmark_eligible`, `validation_only`, `research_only`, `rejected`) and fail-closed integrity states prevent unsupported evidence from becoming benchmark authority by accident.

The interface already has evidence types for current ASOS, T-group, six-hour max, isolated 24-hour max, future MADIS 1-minute/reconstructed five-minute evidence, DSM, CLI, and Kalshi settlement truth. Defining a type does not grant it benchmark trust.

### Verification

The domain serialization, trust boundary and existing proof behavior are exercised in the current full suite. No deployment occurred.

---

## Step 4B — Immutable raw journal + replaceable derivations

Status: **PASS — raw capture, derivation versioning, database immutability and migration regression verified.**

Files:
- `sql/013_immutable_hard_information_journal.sql`
- `paper_collector/raw_journal.py`
- `paper_collector/weather_collector.py`
- `paper_collector/test_raw_journal.py`
- `paper_collector/test_weather_collector_capture.py`
- `paper_collector/test_evidence_versioning.py`
- `sql/tests/013_immutable_hard_information_journal_test.sql`

### Design

AWC network response bytes are journaled before parsed weather rows are trusted. Raw captures retain exact bytes, SHA-256, receipt clocks and every source clock actually supplied/measurable. Parsed weather rows can reference the immutable capture through `raw_source_id`.

Evidence is persisted separately in `evidence_derivations`; each derivation carries parser/evidence/calendar versions and links to every immutable raw input through `evidence_source_links`. A changed parser/model produces a new versioned derivation instead of changing the raw record.

Database triggers reject UPDATE/DELETE on raw captures, evidence derivations and evidence-source links. Application retries are idempotent by stable identities and hash verification; identity/hash disagreements fail closed.

### Verification

By Step 4D run 298, all raw-journal, weather-capture, evidence-versioning and real Postgres immutability tests pass. No old raw record is back-filled with invented provenance.

---

## Step 4C — Live ASOS evidence channels

Status: **PASS for current/T/six-hour benchmark core; 24-hour channel remains deliberately non-trading until exact midnight-LST association is proven.**

Files:
- `paper_collector/asos_evidence.py`
- `paper_collector/hard_state_proof.py`
- `paper_collector/test_live_asos_channels.py`
- existing ASOS/calendar/proof tests

### Design

Current main-C, precise T-group, and valid same-climate-day six-hour maximum remain separate evidence items. A single routine report may therefore preserve both a lower current temperature and a higher hidden six-hour maximum without information loss.

`HardStateProof.all_records`/`evidence_records` retain every accepted causal item; `supporting_records` identifies only evidence at the current hard bound. The 24-hour group is parsed but never admitted into benchmark hard state by the current adapter. Ambiguous midnight-LST semantics therefore fail closed rather than being guessed.

### Key regressions

- main 31°C does not prove 88°F;
- main 32°C uses the canonical inverse lattice;
- T0311 = 88°F;
- T0306 = 87°F;
- T0310 fails closed;
- current below six-hour max preserves both facts and raises the hard bound;
- cross-climate-midnight six-hour max is rejected;
- equal/lower evidence cannot create a second bound transition;
- 24-hour max remains isolated/non-trading.

### Verification

All cases pass in run 298 along with the Step 4D accumulator suite.

---

## Step 4D — Canonical monotonic hard-state accumulator

Status: **PASS — GitHub Actions run 298; 92 Python tests passed, plus Node, Docker and Postgres migration/immutability checks.**

Primary files:
- `paper_collector/hard_state_accumulator.py`
- `paper_collector/hard_state_journal.py`
- `paper_collector/test_hard_state_accumulator.py`
- `paper_collector/test_hard_state_accumulator_integration.py`
- `sql/016_hard_state_timeline.sql`
- `sql/tests/016_hard_state_timeline_test.sql`
- `paper_collector/paper_engine_hardened.py`
- `paper_collector/strategy_runtime_hardened.py`
- `.github/workflows/paper-ci.yml`
- `paper_collector/Dockerfile`

### Canonical accumulator

`accumulate_hard_state()` consumes only canonical `SettlementEvidence`. It is unaware of METAR, MADIS, Celsius and Kalshi strike syntax. Evidence must match the exact station, LST climate date and calendar version and must be explicitly benchmark-eligible before it may change state.

State is monotonic. Once 88°F is proven for a climate day, a later 87°F or 74°F observation is retained as corroborating history but cannot reduce the bound.

Causal order is based on the time Mercury actually had an interpreted usable fact: `mercury_interpreted_at` when present, otherwise `mercury_received_at`. Physical observation time, source publication and first-fetchability remain distinct clocks and can never make a live/replay decision occur before Mercury receipt.

### Atomic knowledge batches

A crucial invariant was added during 4D: facts becoming usable at the exact same Mercury timestamp are one atomic knowledge batch. A routine METAR containing a current 74°F fact and a six-hour hidden max of 77°F therefore creates **one** transition to 77°F, not fictional intermediate 74°F then 77°F trading windows inside the same response.

Within one batch, the strongest valid lower bound is authoritative. Equal strongest evidence is deterministically tie-broken by evidence ID while the other item is preserved as same-batch corroboration.

### Append-only timeline

`hard_state_applications` records every versioned evidence application as `transition`, `corroboration`, `rejected`, or `duplicate`, with reason, first usable time, prior/resulting bound, evidence type and version hashes.

`hard_state_transitions` records only actual bound increases with the first-known time, transition evidence ID, supporting evidence IDs and model/calendar versions.

Both tables are append-only and protected by the database immutability trigger. Recomputing identical state is idempotent; a stable identity yielding different bytes fails closed as non-determinism/versioning failure.

Later corroboration can therefore never rewrite the first-known transition. QC/settlement disagreement handling will consume this immutable history in Step 4H rather than mutate it.

### Hard-core integration

DBN now derives its authority from `timeline.current_state`, not from the legacy source-specific trigger field. It persists all accepted canonical ASOS derivations first, computes the source-neutral timeline, and then trades only when the current weather row created the canonical transition.

DSN/SBK/HSR also receive the canonical accumulator state in `strategy_runtime_hardened.py`. Their guarded weather view contains the canonical bound, first-known timestamp, state, timeline and transition-evidence ID. Research strategies remain on the separate legacy/research path.

### New regression cases

- `86 -> 87 -> 88 -> later 85` remains 88;
- later six-hour 88 after earlier precise 88 is corroboration, not transition;
- station/date/calendar/trust mismatch is rejected explicitly;
- a physically earlier but later-received hot observation cannot leak into earlier replay state;
- duplicate evidence ID cannot retrigger;
- the same evidence stream produces identical transitions independent of input order;
- same-response current 74 + hidden six-hour 77 produces one atomic 77 transition;
- DBN follows the canonical transition even when the legacy proof trigger is intentionally set stale/wrong in the test;
- a later lower row cannot retrigger DBN even when a deliberately wrong legacy trigger claims it did;
- application/transition rows reject UPDATE and DELETE in a real Postgres migration test.

### Verification

GitHub Actions `Paper Trader CI` **run 298**:
- Python compile: PASS;
- full Python suite: **92 tests, 0 failures**;
- dependency import check: PASS;
- collector Docker build including accumulator/journal modules: PASS;
- Node checks: PASS;
- all SQL migrations: PASS;
- immutable raw/evidence journal regression: PASS;
- immutable hard-state timeline regression: PASS.

Branch head that triggered the verified run: `0bea79347ad1dede57daafd7e315f4ee3189a73e`.

### Behavioral scope / next step

4D does **not** yet claim the DSM/CLI QC-disagreement audit requirement; that is explicitly deferred to 4H. It also does not implement the final pure strike-semantics boundary.

**Next: Step 4E — pure bucket elimination.** It must receive only exact event/market strike metadata plus canonical `HardClimateState`, derive every and only impossible bucket, preserve a machine-readable elimination proof, and fail closed on incomplete/ambiguous station/date/strike metadata.

No merge, Railway deployment, portfolio reset or new performance replay has occurred.
