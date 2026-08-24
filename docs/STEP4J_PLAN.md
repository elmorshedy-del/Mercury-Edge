# Step 4J plan — deterministic replay as a first-class capability

Status: **4J-A PASS on run 561 (264 Python tests + Node + Docker + Postgres). Next: 4J-B canonical hard-state / elimination reconstruction.**

Branch: `paper-rigour-v2`

PR: #5

Parent plan: `docs/STEP4_CANONICAL_TODO.md`.

Prerequisite: Step 4I PASS; final docs-only verification run 551 (`32762827396`) is fully green.

Verification:
- `docs/STEP4J_A_VERIFICATION.md`

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

---

# 4J-A — Causal replay manifest and event stream — PASS

Implemented:
- `paper_collector/replay_domain.py`
- `paper_collector/test_replay_domain.py`

The source-neutral replay contract normalizes immutable raw source captures, Kalshi market messages, rule snapshots, transport continuity events, validation products and later exchange settlement captures into deterministic causal events.

Acceptance verified on GitHub Actions **run 561 (`32764624225`)**: **264 Python tests, 0 failures**, compile/Docker/Node/Postgres and SQL013/016/017/018/019/020/021 PASS. See `docs/STEP4J_A_VERIFICATION.md`.

---

# 4J-B — Canonical hard-state / elimination reconstruction — NEXT

Replay weather source bytes through the selected parser/evidence/calendar/accumulator/elimination bundle rather than reading historical `hard_state_transitions` as if they were inputs.

For the current benchmark version:

- replay ASOS current/T/six-hour evidence using immutable raw capture bytes and the fixed LST calendar;
- 24-hour max remains non-admitted under current benchmark policy;
- use exact event/rule metadata available as-of each hard-state transition;
- build hard state monotonically from replay-known evidence;
- run pure `bucket_elimination.evaluate_event(...)` against only rule snapshots causally available then.

Historical persisted derivations/transitions may be compared as expected outputs, but they are never allowed to seed the reconstructed state.

### 4J-B acceptance

- known ASOS stream reproduces the same transition sequence/state ids under the same versions;
- same-response current + hidden six-hour max remains atomic strongest transition;
- later lower temperature cannot reduce state;
- wrong-date Aug18/Aug19 event remains impossible to route;
- a future rule snapshot cannot eliminate a bucket earlier;
- replay with no causally valid rule snapshot produces no authoritative elimination;
- archive-derived/research-only evidence cannot contaminate benchmark hard state.

---

# 4J-C — Exact market/execution replay and A/B version selection

At each reconstructed elimination, recreate the configured benchmark decision using exact causal L2 only.

Requirements:

- reconstruct L2 from `market_data_journal` snapshot/deltas at the configured simulated arrival time;
- preserve connection/sequence integrity and fail closed across fatal/gap-invalid book states;
- use exact fee model, capital state, allocation order and execution latency configured for the replay;
- no candle/midpoint/proxy fallback;
- produce deterministic replay decision/order/fill/portfolio hashes;
- support an A/B replay where one selected component version changes while source journals remain byte-identical;
- explicitly report `UNSUPPORTED_VERSION` rather than silently substituting current logic for an unavailable historical version.

### 4J-C acceptance

- same inputs/version/config replay twice => identical decisions, fills, ending cash/positions and hashes;
- no L2 at arrival => no benchmark fill;
- later L2 cannot fill an earlier simulated order;
- changing execution latency can change execution output while source-input hash stays unchanged;
- changing parser/elimination/execution version changes manifest/version identity and either runs the registered implementation or fails explicitly unsupported;
- benchmark and research/counterfactual P&L remain separate.

---

# 4J-D — Settlement grading, anti-leak real-Postgres regression, and persisted replay result

Build a real-Postgres end-to-end fixture that includes:

1. exact ASOS raw capture(s);
2. an older physical observation received later;
3. a MADIS archive record whose observation timestamp predates the trade but whose import receipt occurs after it;
4. rule snapshots before and after the hard-state event;
5. causal L2 snapshot/deltas;
6. benchmark elimination/order decision;
7. later exchange settlement;
8. validation/settlement audit.

Run replay twice and compare complete canonical output hashes.

Required anti-leak assertions:

- the late weather record cannot affect earlier state;
- the future MADIS/archive record cannot affect the earlier benchmark state/order;
- the future rule snapshot cannot be selected early;
- later settlement cannot influence the trade decision;
- missing historical L2 remains missing rather than becoming a candle proxy;
- replay output can trace every decision to immutable source ids/hashes through the Step 4I explanation layer.

Persist replay summaries/results separately from the source session; never overwrite live source facts or live benchmark P&L.

### 4J-D acceptance

- real Postgres end-to-end replay passes twice with identical canonical hashes;
- historical MADIS/archive future-information leakage regression is permanently green;
- replay settlement result matches the independently persisted authoritative settlement;
- replay can explain its order/evidence/raw chain;
- source session row counts/hashes are unchanged by replay.

---

# Step 4J completion gate

4J is complete only when A-D, full Python/Node/Docker/Postgres CI and the anti-leak real-Postgres regression pass.

Then:

1. update `docs/STEP4_CANONICAL_TODO.md` checkboxes for verified 4H/4I/4J and the permanent archive anti-leak regression;
2. consolidate Steps 4E-4J into `docs/HARD_STATE_REFACTOR.md` with exact runs/commits/test counts;
3. run the whole final branch CI again;
4. perform final hardening/diff review;
5. **stop before merge/deploy and request the user's explicit approval.**

The latency-engineering performance work remains deferred until this correctness/replay gate is complete.
