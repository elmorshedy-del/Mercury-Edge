# Step 4G-C2 verification — MADIS live-capture qualification contract

Date: 2026-08-20

Branch: `paper-rigour-v2`

PR: #5

Status: **PASS**

Parent checklist: `docs/STEP4_CANONICAL_TODO.md`

Detailed plan: `docs/STEP4G_C_PLAN.md`

## Purpose

C2 defines and tests the causal/raw transport boundary that any future MADIS LDM receiver must satisfy. It intentionally does **not** implement the network receiver itself.

The contract prevents a future implementation from bypassing the raw journal, backdating knowledge to an observation timestamp, or importing archive data and accidentally treating it as a real-time receipt.

## Files

- `paper_collector/madis_transport.py`
- `paper_collector/test_madis_transport.py`
- `sql/018_source_transport_events.sql`
- `sql/tests/018_source_transport_events_test.sql`
- `.github/workflows/paper-ci.yml`
- `paper_collector/Dockerfile`

## Canonical transport path

```text
LDM / archive bytes
    -> MadisTransportEnvelope
    -> RawCapture
    -> insert_raw_capture()
    -> CapturedMadisRecord
    -> RawSourceRecord
    -> ContractMadisOmoAdapter
    -> MadisOmoMinute
```

There is no C2 function that parses a `MadisTransportEnvelope` directly. The parser-facing wrapper exists only after the immutable raw insert returns an id.

## Live versus archive origin

`MadisDataOrigin` has two explicit values:
- `LIVE_LDM`
- `ARCHIVE_IMPORT`

The origin changes the raw source stream and transport label:
- live: `madis_omo_ldm` / `ldm`
- archive: `madis_omo_archive` / `archive_import`

Both remain exact immutable raw captures. Only the live path is marked `live_causal=True`.

If an archive import carries a purported historical `first_fetchable_at`, C2 does not place that value into the canonical live-causal clock. It is preserved only as `archive_claimed_first_fetchable_at` metadata. This prevents future replay from silently turning archive metadata into a live information edge.

## Receipt identity

The existing `RawCapture` identity includes Mercury receipt time and payload hash. Therefore identical MADIS bytes received twice at different times are two causal captures, while an idempotent retry of the exact same receipt remains one capture.

C2 preserves:
- exact raw bytes;
- Mercury wall-clock receipt;
- Mercury epoch-nanosecond receipt;
- Mercury monotonic receipt;
- product id;
- connection id;
- source sequence key when available;
- reconnect generation;
- station / observation / source-publication clocks when supplied;
- content type / encoding;
- transport model version.

## Parser causality

`parse_captured_madis_omo()` receives only `CapturedMadisRecord`. The existing MADIS adapter therefore sees the `RawSourceRecord` whose Mercury receipt time is the actual capture/import receipt.

The resulting `MadisOmoMinute` is annotated with:
- `madis_data_origin`;
- `live_causal`;
- transport product id;
- connection id;
- reconnect generation;
- immutable raw source id;
- transport model version.

No source observation time substitutes for Mercury receipt.

## Explicit continuity events

C2 adds generic append-only `source_transport_events` rather than hiding reconnect/gap information inside decoded weather rows.

`SourceTransportEvent` can represent:
- connected;
- disconnected;
- reconnected;
- sequence gap;
- queue gap;
- unknown coverage gap.

It preserves detection clocks, optional gap interval, connection id, prior/next sequence keys, details, deterministic payload hash and version.

This is important for later replay: absence of a MADIS observation is not automatically evidence that no higher state existed. A source-coverage gap is separately observable and can cause the validation/replay layer to mark the interval incomplete.

`source_transport_events` is protected by the existing database immutability trigger. Corrections append new audit facts; historical transport continuity is never rewritten.

## Acceptance regressions

Automated cases verify:
- exact binary live payload preservation;
- complete live receipt/product/connection provenance;
- archive/live source-stream separation;
- archive cannot claim canonical live fetchability;
- identical bytes at different receipt times have different capture identities;
- non-byte payload is rejected before journaling;
- raw persistence occurs before parser-facing wrapper creation;
- live parse retains the actual receipt clock and live-causal flag;
- archive parse remains explicitly non-live;
- sequence-gap event identity is deterministic;
- gap intervals cannot end before they start;
- DB UPDATE/DELETE on transport events fails with the immutable-journal SQLSTATE.

## Verification

GitHub Actions `Paper Trader CI`:
- run number: **431**
- run id: **32398894745**
- code-complete branch commit: **`f37f9816b1c0c148c47032f55dd33a439a96e8b1`**

Results:
- Python compile: **PASS**
- Python tests: **186 passed, 0 failed**
- collector Docker build: **PASS**
- Node checks: **PASS**
- fresh Postgres migrations: **PASS**
- SQL013 immutable hard-information journal: **PASS**
- SQL016 immutable hard-state timeline: **PASS**
- SQL017 immutable Kalshi market journal: **PASS**
- SQL018 immutable source-transport events: **PASS**

Collector log:

```text
Ran 186 tests in 0.095s
OK
```

## Commits

- `444e222e935d2d9fcf2083167c74bbebecdc19a9` — immutable transport-event migration
- `4a3bb81fd83ec1dadd2a47461df7cfb83d4ce10e` — MADIS transport qualification contract
- `26a0e3b023b354b48b56f3fd8e947a8d56b1c416` — SQL018 immutability regression
- `f22322ef0a695bae63a51b9e0e07b5053377b9bb` — transport contract regressions
- `69eae2190c2e8e1c09bb20ef1929bd0a617c0561` — CI inclusion
- `f37f9816b1c0c148c47032f55dd33a439a96e8b1` — collector image inclusion / code-complete C2 head

## Explicit non-changes

C2 does not:
- open an LDM socket;
- request a MADIS account;
- import an archive dataset;
- promote MADIS evidence;
- change hard-state accumulation;
- change bucket elimination;
- change execution;
- deploy Railway;
- enable real money.

## Next step / blocker

**4G-C3 — empirical sample run** requires actual MADIS data/access. The validation and transport contracts are now ready to consume that data without changing their semantics.

Until a real sample exists, direct OMO evidence remains `RESEARCH_ONLY`, and no live-latency or reliability claim is permitted.
