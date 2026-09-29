# Step 4J-D verification — settlement grading and anti-leak replay

Status: **IMPLEMENTED — FULL CI BLOCKED BY GITHUB ACTIONS STARTUP FAILURE; NOT PASS**

Branch: `paper-rigour-v2`

PR: #5

Parent plan: `docs/STEP4J_PLAN.md`

## Verification boundary

The Step 4J-D implementation and its real-Postgres anti-future-leak regression are present on the hardening branch. They are deliberately **not marked PASS** because GitHub Actions stopped starting jobs before the corrected 4J-C/4J-D head could complete a full gate.

The last fully green replay prerequisite remains Step 4J-B, Paper Trader CI **run 575 (`32765146488`)**, with **272 Python tests, 0 failures**, plus compile/Docker/Node/Postgres green.

See `docs/GITHUB_ACTIONS_STARTUP_BLOCKER.md` for the separately isolated CI platform blocker.

## Implemented files

- `paper_collector/replay_settlement.py`
- `paper_collector/test_replay_settlement.py`
- `paper_collector/test_replay_e2e_postgres.py`
- `sql/022_deterministic_replay_results.sql`
- `sql/tests/022_deterministic_replay_results_test.sql`
- `paper_collector/Dockerfile`
- `.github/workflows/paper-ci.yml`

## Implementation commits

- `602208327b3d3f0ffb9fa56949fa721a5e6314ca` — immutable deterministic replay-result journal
- `23bdab459dfccf9aed81c73df9c162e5a5304c6f` — replay settlement grading and persistence
- `56beb1f4c1b3f5014fe50e2198a8312dfbde308a` — append-only settlement revision identities
- `141e59451b5af3fe0b1422a6cf281d51f54b3704` — SQL022 immutability/revision regression
- `983a594c52c195f89e5ee5452a271dcd9700c4bc` — settlement unit regressions
- `5db16edd7e4844e2171f55653270f11737ea48e0` — real-Postgres anti-leak E2E regression
- `b2f29396283c58145dce6feb8f46a6bf89ae66b8` — build inclusion
- `68f5242d624a005991d788a119567c2248f8e0e1` — full-gate CI wiring

## Settlement behavior implemented

`replay_settlement.py` keeps settlement downstream from execution. It can grade an already-created decision but cannot feed settlement information into hard-state, elimination, L2 reconstruction, allocation or order selection.

For every traded dead-NO it checks:

- execution belongs to the exact replay manifest;
- execution carries the exact hard-state output hash;
- execution configuration hash agrees;
- the traded market is uniquely present in the exact eliminated set for the decision's state;
- the exact rule-snapshot hash agrees with authoritative exchange settlement;
- settlement occurs no earlier than the simulated decision arrival;
- the exact traded market result exists.

A NO settlement produces payout equal to filled contracts and realized P&L. A supposedly impossible bucket settling YES becomes `IMPOSSIBLE_BUCKET_SETTLED_YES` / `invariant_failure`.

## Immutable replay-result persistence

Migration `022_deterministic_replay_results.sql` creates a separate append-only replay-result journal. It stores:

- source session and manifest identity;
- source-input hash;
- version bundle + hash;
- execution config + hash;
- hard-state output hash;
- execution output hash;
- settlement grade + hash;
- full canonical replay payload + hash;
- replay model version.

Corrections/revised settlement truth append a new replay result; they do not rewrite prior results or source facts. UPDATE/DELETE is rejected by the database immutability trigger.

## Real-Postgres anti-leak fixture present

`test_replay_e2e_postgres.py` creates a complete immutable causal scenario containing:

1. an authoritative realtime AWC capture establishing the first hard state;
2. later weather that cannot leak backward;
3. a historical MADIS archive record with an old observation time but a later import receipt;
4. rule snapshots on both sides of the state transition;
5. a valid causal L2 market and a separate dead market with no L2;
6. benchmark execution;
7. later authoritative exchange settlement;
8. immutable replay-result persistence.

The test asserts:

- late weather does not affect the earlier state;
- future MADIS/archive information does not enter benchmark hard state;
- a future rule snapshot cannot authorize the earlier elimination;
- missing L2 remains a blocked trade rather than receiving a proxy fill;
- later settlement grades but cannot alter the prior decision;
- replaying the same source state produces identical canonical output hashes;
- source journal hashes/rows remain unchanged;
- realized P&L is settlement-derived;
- replay output has evidence/state/elimination/rule/L2/settlement traceability.

## Unit regression coverage present

`test_replay_settlement.py` covers:

- correct realized P&L when a traded NO resolves NO;
- invariant failure when a supposedly impossible market resolves YES;
- settlement event mismatch;
- exact rules-hash mismatch;
- deterministic settlement/output hash;
- corrected settlement revision creating a distinct replay-result identity.

## Static review against 4J-D plan

- late weather anti-leak: **implemented in real-Postgres fixture**
- future MADIS/archive anti-leak: **implemented in real-Postgres fixture**
- future rule snapshot anti-leak: **implemented in real-Postgres fixture**
- settlement cannot influence trade decision: **architecturally separated and fixture-covered**
- missing L2 cannot become proxy: **implemented**
- replay decision → evidence/raw/rule/L2/settlement explanation: **implemented**
- immutable separate replay results: **implemented**
- deterministic rerun comparison: **implemented**

## What is still required before PASS

1. Restore normal GitHub Actions job startup.
2. Execute the full corrected branch gate including SQL022 and `test_replay_e2e_postgres.py`.
3. Record the exact run ID and final test count.
4. Only after green CI may the historical MADIS/archive permanent regression and Step 4J checkboxes be marked complete.

No merge, deployment or real-money execution is authorized by this document.
