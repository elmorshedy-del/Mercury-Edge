# Mercury hard-state refactor log

Branch: `paper-rigour-v2`

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

Status: **PASS — implemented and unit-tested; not yet wired into trading behavior.**

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

### Test command

From `paper_collector/`:

```bash
python -m unittest -v test_asos_evidence.py
```

Local verification before commit: **11 tests, 11 passed, 0 failed.**

### Explicit non-changes

Step 1 does **not** alter `weather_collector.py`, `market_calendar.py`, DBN/HSR execution, portfolio state, or Railway. This preserves a clean causal boundary: the evidence primitive is proved first; integration occurs only after Step 1 is accepted.
