# Step 4H-B verification — immutable validation / settlement / audit journal

Date: 2026-08-21

Branch: `paper-rigour-v2`

PR: #5

Status: **PASS**

Parent plan: `docs/STEP4H_PLAN.md`

## Purpose

4H-B gives the lifecycle objects proved in 4H-A an append-only persistence layer before any new network collector is allowed to write them.

The journal deliberately keeps three concerns separate:

1. NWS validation/corroboration products;
2. contract-authoritative/exchange settlement truth;
3. derived settlement audit results.

A correction, revised truth, or newer auditor creates a new immutable row. Historical facts and historical audits are never rewritten.

## Files

- `sql/019_validation_settlement_audit.sql`
- `paper_collector/settlement_journal.py`
- `paper_collector/test_settlement_journal.py`
- `sql/tests/019_validation_settlement_audit_test.sql`
- `.github/workflows/paper-ci.yml`
- `paper_collector/Dockerfile`

## Database schema

### `validation_products`

Stores versioned normalized DSM/CLI-style validation products with:

- source/product identity;
- station and target climate date;
- reported max and optional max time;
- source issuance and Mercury receipt clocks;
- mandatory immutable `raw_source_journal` FK;
- exact source payload SHA-256;
- lifecycle and authority class;
- correction/revision lineage;
- parser/model/calendar versions;
- fail-closed reason;
- canonical product payload + SHA-256.

### `authoritative_settlements`

Stores authoritative `SettlementTruth` with:

- settlement/truth identity;
- exact event ticker, station and climate date;
- final max;
- mandatory immutable raw-source FK;
- exact rules hash;
- captured rule-source and settlement-source names;
- authority class (`contract_authoritative` or `exchange_result`);
- revision lineage;
- truth/parser versions;
- canonical payload + SHA-256.

### `settlement_audit_results`

Stores immutable audit conclusions and their provenance:

- source settlement and/or validation product;
- severity/status/finding code;
- station and climate date;
- optional hard-state transition, elimination, order and market linkage;
- auditor version;
- structured details;
- canonical audit payload + SHA-256.

## Database-level immutability

All three tables use the existing `mercury_reject_immutable_mutation()` trigger. UPDATE and DELETE are rejected with SQLSTATE `55000`.

The real-Postgres SQL019 fixture also proves that a corrected validation product can coexist with its predecessor through `revision_of` without mutating the first row.

## Application persistence

`paper_collector/settlement_journal.py` implements:

- `persist_validation_product(...)`
- `persist_authoritative_settlement(...)`
- `SettlementAuditResult`
- `persist_settlement_audit_result(...)`

Before a validation product or authoritative settlement is persisted, the code verifies:

- canonical `source_record_id` equals the supplied immutable raw row;
- raw row exists;
- raw row belongs to the same paper session;
- station matches when the raw row carries a station;
- validation product payload hash exactly matches the immutable raw row.

Every journal payload is canonicalized and SHA-256 hashed. Repeating the same write is idempotent; the same stable identity resolving to different canonical bytes fails closed as non-determinism/collision.

No application path issues UPDATE or DELETE against these journals.

## Regression cases added

- validation product write is append-only/idempotent;
- raw source identity mismatch fails closed;
- validation raw hash mismatch fails closed;
- stable validation identity with different stored hash fails closed;
- authoritative settlement write is append-only/raw-linked;
- settlement raw source from another session fails closed;
- settlement audit identity is deterministic/idempotent;
- audit result requires an explicit settlement or validation source;
- SQL migration fixture creates original + corrected validation rows concurrently;
- real Postgres rejects UPDATE/DELETE on validation products;
- real Postgres rejects UPDATE/DELETE on authoritative settlements;
- real Postgres rejects UPDATE/DELETE on settlement audit results.

## Verification

Code-complete branch commit: `ba5a1fb0daa8b893b9a82d57d30e73ce1627f0c6`

GitHub Actions `Paper Trader CI` run **465** (`32543845505`):

- Python compile: PASS
- full Python suite: **209 tests, 0 failures**
- dependency import check: PASS
- collector Docker build: PASS
- Node checks: PASS
- full fresh Postgres migrations: PASS
- SQL013 immutable hard-information journal: PASS
- SQL016 immutable hard-state timeline: PASS
- SQL017 immutable Kalshi market journal: PASS
- SQL018 immutable source-transport events: PASS
- **SQL019 immutable validation/settlement/audit journal: PASS**

No Railway deploy or merge occurred.

## Next canonical substep

**4H-C — raw-first collectors/adapters.**

Network source bytes must be written to `raw_source_journal` before the lifecycle parser sees them. Existing legacy `product_releases` data remains legacy research/backfill material unless exact raw provenance exists; it is not silently promoted into this journal.
