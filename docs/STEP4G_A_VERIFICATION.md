# Step 4G-A verification — MADIS OMO source/trust contract

Status: **PASS**

Branch: `paper-rigour-v2`

PR: #5

Final implementation commit for this substep: `a04b84c1936d21f6a98cf98c1236e2a531c24352`

GitHub Actions: **Paper Trader CI run 352 — PASS**

## Scope

Step 4G-A defines the stable source boundary for future real-time MADIS/LDM 1-minute ASOS observations without granting those raw minute values settlement authority.

New files/components:

- `paper_collector/madis_omo.py`
- `paper_collector/test_madis_omo.py`

The adapter deliberately does **not** implement rolling-five-minute ASOS climate reconstruction yet. It also does not alter `bucket_elimination.py`, `dead_no_executor.py`, or any benchmark strategy/execution path.

## Official source semantics encoded

The contract follows the official MADIS 1-minute ASOS variable metadata:

- `T` is air temperature;
- the published MADIS unit is Kelvin (`K`);
- `TSS` is temperature sensor status;
- `TSS = 0` is treated as verified operating/data-available for future reconstruction gating;
- supplied non-zero `TSS` values are rejected by this conservative research adapter;
- a missing `TSS` may be preserved for research but is explicitly marked unverified.

The raw upstream Kelvin value is preserved exactly. There is no silent Kelvin/Celsius/Fahrenheit round-trip in the adapter.

## Trust boundary

A parsed `MADIS_OMO_1MIN` item can become a canonical `NormalizedObservation`, but its derived `SettlementEvidence` is always:

- `EvidenceType.MADIS_OMO_1MIN`;
- `EvidenceTrust.RESEARCH_ONLY`;
- no `proven_min_f` or `proven_max_f`;
- fail-closed reason `raw_madis_minute_is_not_settlement_climate_state`.

Therefore the existing hard-state accumulator rejects raw MADIS minute evidence and it cannot create benchmark bucket eliminations or paper trades.

Promotion later must happen only through a separately versioned, validated rolling-five-minute reconstruction/trust policy.

## Causal clocks and provenance

The contract preserves separately when available:

- physical observation time;
- source/MADIS publication time;
- first-fetchable time;
- LDM/Mercury receipt time;
- Mercury interpretation time;
- source record id/hash;
- transport/sequence key;
- LST climate date.

It records observation-to-LDM, source-release-to-LDM, first-fetchable-to-LDM, and LDM-to-interpretation latency without substituting one clock for another.

## Fail-closed cases verified

- wrong raw source;
- wrong upstream variable;
- non-Kelvin unit;
- missing temperature;
- explicit bad QC;
- non-operating supplied `TSS`;
- receipt before observation clock;
- interpretation before receipt;
- LST climate-date handling across DST.

## Verification

GitHub Actions **run 352**:

- **131 Python tests passed, 0 failed**;
- Python compile PASS;
- collector Docker build PASS;
- Node checks PASS;
- full Postgres migrations PASS;
- immutable raw/evidence journal regression PASS;
- immutable hard-state timeline regression PASS.

## Explicitly unfinished

Step 4G-A is only the adapter/trust boundary. The following remain for later Step 4G substeps:

- real LDM transport ingestion once access/feed details are available;
- exact immutable MADIS/LDM capture in production;
- versioned Kelvin/raw-minute -> candidate ASOS minute-state conversion policy;
- rolling-five-minute reconstruction;
- missing/duplicate/out-of-order/correction/reconnect behavior;
- empirical validation against precise T-groups, six-hour maxima, DSM/CLI and settlement outcomes;
- any benchmark trust promotion.

No merge and no Railway deployment were performed.
