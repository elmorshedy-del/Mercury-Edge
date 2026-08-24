# Step 4I plan — debugging and explainability

Status: **4I-A PASS run 515; 4I-B PASS run 538 (255 Python tests + Node + Docker + Postgres). Next: 4I-C end-to-end explainability regression.**

Branch: `paper-rigour-v2`

PR: #5

Parent plan: `docs/STEP4_CANONICAL_TODO.md`.

Prerequisite: Step 4H PASS on GitHub Actions run 501 (`32544466584`), 237 Python tests.

Verification:
- `docs/STEP4I_A_VERIFICATION.md`
- `docs/STEP4I_B_VERIFICATION.md`

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
- unit/integration regressions and audit-daemon sweep wiring.

The immutable ledger makes source/evidence/elimination/execution/validation/settlement failures countable by stable stage/disposition/reason while keeping ordinary economic skips distinct.

Initial diagnostics cover:
- ASOS off-lattice input;
- main/T contradiction;
- six-hour max below precise current;
- six-hour interval crossing LST climate midnight;
- benchmark-deferred 24-hour max evidence;
- bucket-elimination fail-closed findings;
- benchmark execution blocks;
- ordinary economic no-edge/portfolio skips as a separate disposition;
- rejected/ambiguous validation products with raw provenance;
- settlement invariant failures with trade identity.

Database UPDATE/DELETE is rejected with `55000`; same fact is idempotent; same identity/different payload fails closed.

Verification: run **538 (`32762176090`)**, **255 Python tests, 0 failures**, compile/Docker/Node/Postgres and SQL013-021 PASS.

## 4I-C — End-to-end explainability regression — NEXT

Build a synthetic canonical benchmark case that includes:

1. immutable raw ASOS response;
2. accepted precise/six-hour evidence;
3. hard-state transition;
4. multiple dead buckets;
5. exact eliminated market chosen for a paper order;
6. causal L2 execution identity;
7. exchange settlement result;
8. settlement audit.

The final order explanation must trace from order back to every raw evidence input and forward to settlement audit without source-specific re-parsing.

Also add a deliberately malformed ASOS case proving the structured failure ledger retains a countable reason code + raw provenance while producing no hard-state authorization.

## Completion gate

Step 4I is PASS only after 4I-C and full Python/Node/Docker/Postgres CI are green. Then update the canonical TODO/refactor log and move to Step 4J deterministic replay.

No merge or Railway deployment occurs merely because 4I passes.
