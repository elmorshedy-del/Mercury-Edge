# Step 4G-C1 verification — pure empirical MADIS validation model

Date: 2026-08-20

Branch: `paper-rigour-v2`

PR: #5

Status: **PASS**

Parent checklist: `docs/STEP4_CANONICAL_TODO.md`

Detailed 4G-C plan: `docs/STEP4G_C_PLAN.md`

## Purpose

C1 creates the validation machinery required before Mercury can even consider trusting direct MADIS OMO temperature evidence for benchmark hard state. It does **not** collect MADIS data and does **not** promote MADIS evidence.

The layer keeps four claims separate:

1. an archive value can be represented by a candidate MADIS storage policy;
2. the decoded OMO state agrees with authoritative ASOS comparison evidence;
3. a record was actually received causally through a live feed at the recorded time;
4. a later trust policy explicitly promotes that live product to benchmark evidence.

C1 addresses the first two mathematically and provides metrics needed for the third. It cannot perform the fourth.

## Files

- `paper_collector/madis_validation.py`
- `paper_collector/test_madis_validation.py`
- `.github/workflows/paper-ci.yml`
- `paper_collector/Dockerfile`
- `docs/STEP4G_C_PLAN.md`

## Validation contracts

### Storage-policy calibration

`StorageCalibrationSample` preserves:
- exact observed Kelvin value;
- canonical whole-°F comparison state;
- station;
- climate date;
- physical observation timestamp;
- raw source ids;
- `LIVE_CAPTURE` vs `ARCHIVE_ONLY` origin.

`identify_storage_policy()` evaluates only explicitly supplied `MadisKelvinEncodingPolicy` candidates. It never searches for or invents a Kelvin resolution/rounding rule.

Results are:
- `IDENTIFIED` — exactly one candidate is compatible with all qualifying samples;
- `AMBIGUOUS` — multiple candidates remain compatible;
- `REJECTED` — no supplied candidate fits every qualifying sample;
- `NO_QUALIFYING_SAMPLES` — no calibration evidence exists.

Even an identified policy from archive data has `establishes_live_causality=False`.

### Exact current-state agreement

`compare_aligned_current()` compares a direct OMO research state only with a precise ASOS T-group for the same:
- station;
- LST climate date;
- exact physical observation timestamp.

No arbitrary ±N-minute tolerance exists in v1. A decoded state mismatch is an explicit contradiction, not a score to average away.

### Maximum agreement with coverage awareness

`compare_maximum()` compares OMO maxima with an authoritative exact maximum while preserving coverage status.

Rules:
- complete OMO coverage -> exact equality required;
- incomplete OMO coverage + OMO max below authoritative max -> permitted because the missing observation can contain the peak;
- OMO max above authoritative max -> contradiction even when coverage is incomplete;
- no interpolation of missing OMO observations.

This same primitive is designed for valid six-hour maxima and later completed-day validation truth.

### Capture-quality metrics

`assess_live_quality()` preserves/counts:
- total records;
- unique record identities;
- exact duplicate inputs;
- conflicting values claiming the same station/date/physical minute;
- QC rejects;
- unverified temperature-sensor status;
- clock-skew records;
- missing observations only when an exact expected-observation set is supplied;
- observation-to-Mercury-receipt latency min/median/p95/max.

It does not guess an expected cadence interval when one has not been supplied by a qualified live-capture context.

## Acceptance regressions

New automated cases verify:
- one compatible candidate is identified;
- two candidates remain ambiguous;
- no candidate is rejected;
- archive calibration never claims live causality;
- exact-minute OMO/T agreement;
- exact-minute OMO/T contradiction;
- different observation minutes are not matched by a hidden tolerance;
- complete max coverage requires equality;
- incomplete coverage can explain OMO max below authoritative max;
- incomplete coverage cannot explain OMO max above authoritative max;
- duplicate/conflict/gap/QC/TSS/clock-skew/latency metrics remain explicit.

## Verification

GitHub Actions `Paper Trader CI`:
- run number: **415**
- run id: **32398166021**
- code-complete branch commit: **`1c213fb8cf6ffe5121c4c3ef4fb168cc564f1373`**

Results:
- Python compile: **PASS**
- Python tests: **177 passed, 0 failed**
- collector Docker build: **PASS**
- Node checks: **PASS**
- fresh Postgres migrations: **PASS**
- SQL013 immutable hard-information journal regression: **PASS**
- SQL016 immutable hard-state timeline regression: **PASS**
- SQL017 immutable Kalshi market journal regression: **PASS**

The collector log ended with:

```text
Ran 177 tests in 0.075s
OK
```

## Commits

- `08f2f83d0f0f61df8f474bef108d8db3ee351710` — lock 4G-C plan before implementation
- `3c4363d49577705056ff89aef28871f4d4fda007` — pure empirical validation primitives
- `19df3953cb13310b512997a9334fb54982fc67a0` — validation regression suite
- `906a6fd7a3eb8360461633f99ca44cf96db57001` — CI inclusion
- `1c213fb8cf6ffe5121c4c3ef4fb168cc564f1373` — collector image inclusion / code-complete C1 head

## Explicit non-changes

C1 made no changes to:
- benchmark evidence trust;
- hard-state accumulation;
- bucket elimination;
- dead-NO execution;
- portfolio logic;
- Railway deployment;
- real-money execution.

Direct OMO evidence remains `RESEARCH_ONLY`.

## Next step

**4G-C2 — live-capture qualification contract.**

C2 must make the future MADIS transport boundary explicit: exact bytes before parsing, immutable receipt identity, live-vs-archive origin, receipt/monotonic clocks, product/sequence/connection provenance, and explicit reconnect/gap events. It must still not enable production transport or benchmark promotion.
