# Step 4J plan — deterministic replay as a first-class capability

Status: **4J-A PASS run 561; 4J-B PASS run 575 (272 Python tests + Node + Docker + Postgres); 4J-C and 4J-D IMPLEMENTED but NOT PASS because GitHub Actions currently fails at workflow startup before any job is created.**

Branch: `paper-rigour-v2`

PR: #5

Parent plan: `docs/STEP4_CANONICAL_TODO.md`.

Prerequisite: Step 4I PASS; final docs-only verification run 551 (`32762827396`) is fully green.

Verification / status records:
- `docs/STEP4J_A_VERIFICATION.md`
- `docs/STEP4J_B_VERIFICATION.md`
- `docs/STEP4J_B_DESIGN_NOTE.md`
- `docs/STEP4J_C_VERIFICATION.md`
- `docs/STEP4J_D_VERIFICATION.md`
- `docs/GITHUB_ACTIONS_STARTUP_BLOCKER.md`

## Goal

Given an immutable Mercury session and explicit component versions, replay must reconstruct the benchmark's causal world without using information that Mercury did not yet possess.

The replay must answer, in deterministic order:

```text
what exact source bytes had arrived?
  -> what canonical evidence was knowable?
  -> what monotonic hard state existed?
  -> which Kalshi buckets were then impossible?
  -> what exact rule snapshot and L2 book were available?
  -> what would the configured benchmark engine decide/fill?
  -> what later settlement/audit outcome occurred?
```

Replay is a new derivation over immutable source data. It must never mutate the source session or silently reuse future derived facts from that session.

## Non-negotiable causal rules

1. **Receipt/knowledge time, never physical observation time, controls replay availability.**
   - `raw_source_journal.received_at/received_epoch_ns` controls when source bytes enter the replay world.
   - a derived fact cannot exist before all of its required raw inputs have entered the world;
   - physical `observed_at` remains provenance only.

2. **Historical/archive data cannot masquerade as live knowledge.**
   - `transport='archive_import'`, `source_stream='madis_omo_archive'`, or metadata `live_causal=false` is never assigned a contemporaneous historical first-fetchable time;
   - if an archive record is included in an A/B research replay, its earliest causal availability is its actual Mercury import receipt, never its old observation timestamp;
   - benchmark replay excludes research-only/archive-derived evidence unless the explicitly selected replay policy says research simulation, and even then no backwards leakage is allowed.

3. **Market/rule state is also causal.**
   - a rule snapshot cannot be used before its `captured_at`;
   - an L2 snapshot/delta cannot be used before Mercury receipt;
   - no minute candle/midpoint/proxy can substitute for missing historical L2 in benchmark replay.

4. **Validation/settlement enters only when later received.**
   - DSM/CLI/settlement cannot alter prior hard-state or prior order decisions;
   - it can only grade/audit the replay after its causal arrival.

5. **Same immutable inputs + same version bundle + same configuration => same canonical output hashes.**

6. **Verification state is distinct from implementation state.**
   - no Step 4J substep becomes PASS merely because code/tests exist;
   - a clean full Python/Node/Docker/Postgres gate on the corrected head is mandatory;
   - while GitHub Actions is in the documented pre-job `startup_failure` state, C/D remain implemented-but-unverified.

---

# 4J-A — Causal replay manifest and event stream — PASS

Implemented:
- `paper_collector/replay_domain.py`
- `paper_collector/test_replay_domain.py`

The source-neutral replay contract normalizes immutable raw source captures, Kalshi market messages, rule snapshots, transport continuity events, validation products and later exchange settlement captures into deterministic causal events.

Acceptance verified on GitHub Actions **run 561 (`32764624225`)**: **264 Python tests, 0 failures**, compile/Docker/Node/Postgres and SQL013/016/017/018/019/020/021 PASS. See `docs/STEP4J_A_VERIFICATION.md`.

---

# 4J-B — Canonical hard-state / elimination reconstruction — PASS

Implemented:
- `paper_collector/replay_hard_state.py`
- `paper_collector/test_replay_hard_state.py`

Exact immutable AWC batch bytes are reparsed through the current parser/calendar/evidence/accumulator/elimination stack. Prior evidence derivations and hard-state transitions never seed replay. The current v1 historical `weather_id` is recovered only through the strict identity-only rule documented in `docs/STEP4J_B_DESIGN_NOTE.md` so current state ids remain exactly reproducible without consuming decoded weather values.

Acceptance verified on GitHub Actions **run 575 (`32765146488`)**: **272 Python tests, 0 failures**, compile/Docker/Node/Postgres and SQL013/016/017/018/019/020/021 PASS. See `docs/STEP4J_B_VERIFICATION.md`.

---

# 4J-C — Exact market/execution replay and A/B version selection — IMPLEMENTED, FULL GATE PENDING

Implemented:
- `paper_collector/replay_execution.py`
- `paper_collector/test_replay_execution.py`
- `paper_collector/test_replay_execution_portfolio.py`

At each reconstructed elimination, the replay recreates the configured benchmark decision using exact causal L2 only.

Requirements implemented in code:

- reconstruct L2 from `market_data_journal` snapshot/deltas at the configured simulated arrival time;
- preserve connection/hash-chain/sequence integrity and fail closed across fatal/gap-invalid book states;
- use exact fee model, capital state, allocation order and execution latency configured for the replay;
- no candle/midpoint/proxy fallback;
- produce deterministic replay decision/order/fill/portfolio hashes;
- support A/B execution changes such as latency/config changes while source-input identity stays unchanged;
- explicitly report `UNSUPPORTED_VERSION` rather than silently substituting current logic for an unavailable historical version;
- keep benchmark and research/counterfactual result types separate.

A full 4J-C attempt reached the expanded test suite and found a real classification bug in corrupt target L2. The bug was corrected. Static review then found a portfolio sizing issue where multiple routes could otherwise share a pre-fill cash assumption; execution now rechecks remaining cash before each order and has a negative-cash invariant plus a dedicated overspend regression.

### 4J-C acceptance status

- same inputs/version/config replay twice => identical decisions/fills/cash/positions/hashes: **implemented/test present**
- no L2 at arrival => no benchmark fill: **implemented/test present**
- later L2 cannot fill an earlier simulated order: **implemented/test present**
- changing execution latency can change output while source-input hash stays unchanged: **implemented/test present**
- changing parser/elimination/execution version changes manifest/version identity and either runs registered logic or fails explicitly unsupported: **implemented/tested at registered/unsupported boundaries**
- benchmark and research/counterfactual P&L remain separate: **implemented/test present**
- complete corrected-head CI: **BLOCKED — not yet verified**

See `docs/STEP4J_C_VERIFICATION.md`.

---

# 4J-D — Settlement grading, anti-leak real-Postgres regression, and persisted replay result — IMPLEMENTED, FULL GATE PENDING

Implemented:
- `paper_collector/replay_settlement.py`
- `paper_collector/test_replay_settlement.py`
- `paper_collector/test_replay_e2e_postgres.py`
- `sql/022_deterministic_replay_results.sql`
- `sql/tests/022_deterministic_replay_results_test.sql`

The real-Postgres fixture includes:

1. exact ASOS raw capture(s);
2. an older physical observation received later;
3. a MADIS archive record whose observation timestamp predates the trade but whose import receipt occurs after it;
4. rule snapshots before and after the hard-state event;
5. causal L2 snapshot/deltas;
6. benchmark elimination/order decision;
7. later exchange settlement;
8. immutable replay-result persistence and explanation.

Required anti-leak assertions are implemented in the fixture:

- late weather cannot affect earlier state;
- future MADIS/archive data cannot affect the earlier benchmark state/order;
- future rule snapshot cannot be selected early;
- later settlement cannot influence the trade decision;
- missing historical L2 remains missing rather than becoming a candle proxy;
- replay output traces each decision through canonical state/evidence/raw source, elimination, rule snapshot, exact L2 source and later settlement.

Settlement grading computes realized hold-to-settlement P&L from authoritative per-market outcomes. A dead bucket later settling YES is an explicit invariant failure. Corrected settlement truth creates an append-only replay-result revision rather than rewriting source facts or the earlier result.

### 4J-D acceptance status

- real-Postgres E2E exists and runs replay twice with canonical-hash equality assertions: **implemented**
- historical MADIS/archive future-information leakage regression: **implemented, not yet full-gate verified**
- settlement grade matches independently persisted authoritative exchange settlement: **implemented**
- order/evidence/raw/rule/L2/settlement explanation: **implemented**
- source session immutability checks: **implemented**
- SQL022 append-only replay-result regression: **implemented**
- complete corrected-head CI: **BLOCKED — not yet verified**

See `docs/STEP4J_D_VERIFICATION.md`.

---

# GitHub Actions external verification blocker

After the 4J-C corrections, GitHub began returning a synthetic `BuildFailed` workflow with `startup_failure` before any job is created. The repository-side isolation work reproduced it with the exact prior green workflow, a trivial smoke workflow, a fresh workflow filename, and a separate branch. The intended full `Paper Trader CI` workflow is restored on `paper-rigour-v2`.

This is treated as an explicit external dependency, **not** as permission to waive CI. Full diagnosis and recovery steps are recorded in `docs/GITHUB_ACTIONS_STARTUP_BLOCKER.md`.

---

# Step 4J completion gate

4J is complete only when A-D, full Python/Node/Docker/Postgres CI and the anti-leak real-Postgres regression pass.

When GitHub Actions normal job startup is restored:

1. run the current corrected branch through the complete gate;
2. record the exact run id/test count in C/D verification docs;
3. update `docs/STEP4_CANONICAL_TODO.md` checkboxes for verified 4H/4I/4J and the permanent archive anti-leak regression;
4. consolidate Steps 4E-4J into `docs/HARD_STATE_REFACTOR.md` with exact runs/commits/test counts;
5. run the whole final branch CI again;
6. perform final hardening/diff review;
7. **stop before merge/deploy and request the user's explicit approval.**

The latency-engineering performance work remains deferred until this correctness/replay gate is complete.
