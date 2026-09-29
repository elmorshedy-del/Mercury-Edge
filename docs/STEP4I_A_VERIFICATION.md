# Step 4I-A verification — canonical why trace and raw inspection

Status: **PASS**

Branch: `paper-rigour-v2`

PR: #5

Code-complete head: `8cb5c447e30c90a0afe5b2e704754625b9144067`

GitHub Actions: **Paper Trader CI run 515 (`32761450930`)**

## Implemented

- `paper_collector/explainability.py`
- `paper_collector/test_explainability.py`
- CI/Docker inclusion

The explanation layer is source-neutral: it never reparses METAR, MADIS, DSM or CLI syntax. It reconstructs a benchmark order from persisted canonical objects and immutable provenance only.

For a benchmark order the trace validates and exposes:

- event, market, station and climate date;
- canonical hard-state id and proven lower bound;
- exact transition evidence id plus **all** supporting evidence ids;
- exact bucket elimination and dead-market set;
- evidence type/source/raw identifier and canonical Fahrenheit interpretation;
- observation/publication/first-fetchable/Mercury receipt/interpreted clocks separately;
- parser/evidence/calendar/state/elimination/execution versions;
- immutable raw-source ids and SHA-256 hashes;
- L2/execution identity from the paper order;
- settlement audit results when present.

`inspect_raw_source(...)` retrieves the exact immutable `raw_bytes`, re-hashes them, returns base64 for binary-safe inspection, and only emits UTF-8 text when decoding round-trips losslessly. Any hash disagreement is an integrity failure.

Missing or inconsistent canonical links fail closed instead of producing a partial authoritative explanation.

## Acceptance regressions

Verified named tests prove:

- order trace contains state, elimination, full supporting evidence, raw hashes, dead markets and settlement audit;
- same database facts produce byte-identical canonical explanation JSON;
- missing evidence-to-raw links fail closed;
- raw inspection returns the exact stored bytes with matching SHA-256;
- raw hash mismatch raises an integrity failure.

## Full verification

Run **515** completed green:

- Python compile: **PASS**
- full Python suite: **242 tests, 0 failures**
- dependency imports: **PASS**
- collector Docker build: **PASS**
- Node checks: **PASS**
- fresh Postgres migrations: **PASS**
- SQL013/016/017/018/019/020 regressions: **PASS**

## Next

**Step 4I-B — append-only structured fail-closed event ledger.**

No merge or deployment occurred.
