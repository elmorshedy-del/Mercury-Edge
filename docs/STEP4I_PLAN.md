# Step 4I plan — debugging and explainability

Status: **PASS — 4I-A run 515; 4I-B run 538; 4I-C run 547. Next: Step 4J deterministic replay.**

Branch: `paper-rigour-v2`

PR: #5

Parent plan: `docs/STEP4_CANONICAL_TODO.md`.

Prerequisite: Step 4H PASS on GitHub Actions run 501 (`32544466584`), 237 Python tests.

Verification:
- `docs/STEP4I_A_VERIFICATION.md`
- `docs/STEP4I_B_VERIFICATION.md`
- `docs/STEP4I_C_VERIFICATION.md`

## Goal

A Mercury benchmark trade must be explainable from one stable query without re-running source parsers or guessing what happened.

```text
paper order / signal
  -> exact event + market
  -> exact BucketElimination
  -> exact HardClimateState transition
  -> supporting SettlementEvidence derivations
  -> exact immutable raw source record(s)
  -> all causal clocks + parser/model/calendar versions
  -> executable L2/execution audit
  -> settlement audit when available
```

Explainability is a read/audit layer. It must not alter strategy decisions, hard state, settlement truth or benchmark P&L.

## 4I-A — Canonical why trace + raw inspection — PASS

Implemented:
- `paper_collector/explainability.py`
- `paper_collector/test_explainability.py`

The trace exposes and validates event/market/station/date, hard state and bound, transition plus all supporting evidence, exact elimination/dead set, raw identifiers and canonical Fahrenheit interpretation, distinct clocks, software/model versions, immutable raw-source ids/hashes, execution identity and settlement audits. It never reparses source syntax. Exact raw bytes are inspectable with independent SHA-256 verification.

Verification: run **515 (`32761450930`)**, **242 Python tests, 0 failures**, compile/Docker/Node/Postgres and SQL013-020 PASS.

## 4I-B — Structured fail-closed event ledger — PASS

Implemented:
- `sql/021_hard_edge_failure_events.sql`
- `sql/tests/021_hard_edge_failure_events_test.sql`
- `paper_collector/failure_events.py`
- `paper_collector/failure_event_sweeper.py`
- `paper_collector/diagnostic_sweep.py`
- audit-daemon sweep wiring and regressions.

The immutable ledger makes source/evidence/elimination/execution/validation/settlement failures countable by stable stage/disposition/reason while keeping ordinary economic skips distinct.

Diagnostics cover ASOS integrity/non-admission cases, bucket-elimination failures, execution blocks/economic skips, rejected validation products, and settlement invariant failures. Raw-linked events validate immutable provenance. UPDATE/DELETE is rejected with `55000`; same fact is idempotent; same identity/different payload fails closed.

Verification: run **538 (`32762176090`)**, **255 Python tests, 0 failures**, compile/Docker/Node/Postgres and SQL013-021 PASS.

## 4I-C — End-to-end explainability regression — PASS

`paper_collector/test_explainability_postgres.py` creates a full canonical benchmark chain in a real migrated Postgres database:

1. immutable raw ASOS response;
2. precise T-group + six-hour evidence;
3. hard-state transition to >=88F;
4. multiple dead buckets;
5. exact eliminated market used by a paper order;
6. causal L2 snapshot identity;
7. immutable exchange settlement result;
8. settlement audit;
9. deterministic order explanation spanning the complete chain.

The same test inserts malformed off-lattice `T0310` source evidence, proves a raw-linked `ASOS_OFF_LATTICE_EVIDENCE` diagnostic is retained, and proves the malformed record creates no evidence-source link capable of authorizing hard state.

Verification: run **547 (`32762649239`)** — **255 standard Python tests, 0 failures**, compile/Docker/Node/Postgres, SQL013-021 and the dedicated real-Postgres end-to-end explainability regression all PASS.

## Completion gate — SATISFIED

**Step 4I is PASS.** Mercury can now explain a benchmark order back to immutable raw evidence and forward to settlement audit, while rejected facts remain countable and inspectable.

**Next: Step 4J — deterministic replay.**

No merge or Railway deployment occurred.
