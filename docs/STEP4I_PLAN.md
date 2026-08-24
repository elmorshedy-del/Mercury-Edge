# Step 4I plan — debugging and explainability

Status: **4I-A PASS on run 515 (242 Python tests + Node + Docker + Postgres). Next: 4I-B structured fail-closed event ledger.**

Branch: `paper-rigour-v2`

PR: #5

Parent plan: `docs/STEP4_CANONICAL_TODO.md`.

Prerequisite: Step 4H PASS on GitHub Actions run 501 (`32544466584`), 237 Python tests.

Verification: `docs/STEP4I_A_VERIFICATION.md`.

## Goal

A Mercury benchmark trade must be explainable from one stable query without re-running source parsers or guessing what happened.

The explanation chain is:

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

For an order/signal the trace exposes and validates:

- event ticker and traded market;
- station and LST climate date;
- hard-state id and newly proven lower bound;
- transition evidence id and all supporting evidence ids;
- newly dead market set and exact elimination proof/strike rule;
- evidence type/source;
- raw evidence identifier/group/value and canonical Fahrenheit interpretation;
- observation, source-publication, first-fetchable, Mercury receipt and interpretation clocks separately;
- parser/evidence/calendar/state/elimination/execution versions;
- immutable raw-source id and SHA-256 for every evidence input;
- L2/execution identity;
- settlement/audit identity when available.

The explanation never parses METAR, MADIS, DSM or CLI syntax. Raw inspection returns exact bytes as base64 and emits UTF-8 only when decoding is lossless; the stored hash is independently verified.

Acceptance is verified on GitHub Actions **run 515 (`32761450930`)**: **242 Python tests, 0 failures**, compile/Docker/Node/Postgres and SQL013/016/017/018/019/020 PASS.

## 4I-B — Structured fail-closed event ledger — NEXT

Create one append-only normalized ledger for hard-edge failures/rejections so ambiguous inputs and blocked decisions can be counted by stage/reason instead of disappearing into logs.

Canonical fields:

- deterministic failure id;
- session;
- stage (`source_parse`, `evidence`, `hard_state`, `elimination`, `execution`, `validation`, `settlement`, `replay`);
- stable reason code;
- station/climate date/event/market when known;
- raw source id/evidence id/state id/elimination id/signal id/order id when known;
- occurred/known time;
- structured details;
- failure model version + canonical payload hash.

Database UPDATE/DELETE must be rejected.

Initial live integrations must cover at least:

- incoherent/off-lattice ASOS hard-state source rows;
- invalid six-hour climate-window evidence and intentionally isolated 24-hour max evidence as explicit non-admission reasons where encountered;
- bucket-elimination fail-closed results;
- benchmark dead-NO execution blocks;
- rejected/ambiguous validation products;
- settlement auditor invariant failures that are persisted outcomes.

A query helper must return counts grouped by stage + reason code.

### 4I-B acceptance

- same failure fact is idempotent;
- same stable identity with different canonical payload fails closed;
- raw-linked failures preserve exact raw provenance;
- SQL mutation attempts fail with `55000`;
- reason counts are deterministic;
- ordinary `no edge` decisions are distinguishable from integrity/fail-closed failures.

## 4I-C — End-to-end explainability regression

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

Step 4I is PASS only after A-C, full Python/Node/Docker/Postgres CI and immutable failure-ledger regression are green. Then update the canonical TODO/refactor log and move to Step 4J deterministic replay.

No merge or Railway deployment occurs merely because 4I passes.
