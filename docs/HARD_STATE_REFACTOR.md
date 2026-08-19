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
4. **First-class hidden-max events** — distinguish current print, 6-hour max, 24-hour max/DSM, and final CLI evidence.
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

Step 3 is now complete for the deterministic hard-state core. The 24-hour group/DSM/CLI lifecycle is still deliberately excluded from `HardStateProof`; adding and grading those evidence types is Step 4.
