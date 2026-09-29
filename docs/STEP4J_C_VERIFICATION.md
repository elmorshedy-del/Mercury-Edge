# Step 4J-C verification — exact causal L2 execution replay

Status: **IMPLEMENTED — FULL CI BLOCKED BY GITHUB ACTIONS STARTUP FAILURE; NOT PASS**

Branch: `paper-rigour-v2`

PR: #5

Parent plan: `docs/STEP4J_PLAN.md`

## Verification boundary

Step 4J-C is code-complete enough for the canonical acceptance surface, but it must **not** be marked PASS until the corrected head completes the full Python/Node/Docker/Postgres gate.

The last fully green replay prerequisite is Step 4J-B: Paper Trader CI **run 575 (`32765146488`)**, **272 Python tests, 0 failures**, with compile/Docker/Node/Postgres green.

The first 4J-C full-gate attempt (run 587) executed the expanded Python suite and exposed a legitimate new regression: corrupt target L2 was blocked correctly but classified as `NO_VALID_L2_AT_SIMULATED_ARRIVAL` rather than the stronger `L2_CONNECTION_INTEGRITY_FAILURE`. That finding was fixed after the run. A second static review also found that multiple qualifying dead-NO orders could otherwise size against the same pre-fill cash balance; execution now recomputes available capital before every order and raises on negative cash.

No post-fix full CI success is claimed because GitHub Actions subsequently stopped starting jobs at the platform/workflow-registration layer. See `docs/GITHUB_ACTIONS_STARTUP_BLOCKER.md`.

## Implemented files

- `paper_collector/replay_execution.py`
- `paper_collector/test_replay_execution.py`
- `paper_collector/test_replay_execution_portfolio.py`
- `paper_collector/Dockerfile`
- `.github/workflows/paper-ci.yml`

## Implementation commits

- `a7eb53abc47c28232d0b728885a2ddc22577849f` — causal L2 execution replay
- `4c8196f327b1399ac68347858b23aa02e47ec7ac` — execution regressions
- `d3a79701c5363166f5f2750710e06981a5849572` — build inclusion
- `8b2fdc643ce40b0c0a7bc0c6caba35f968cbcc3e` — CI inclusion
- `ea64294eca3807c8c66081ad7209ae994136d06a` — corrupt-L2 classification fix + sequential capital recheck
- `b754176b64892cdf90053fe41ed3158efc0cb803` — portfolio overspend regression
- `421b18e4c4e9badcce5f4a07f2190500e34f30e5` — CI inclusion for portfolio regression

## Acceptance surface implemented

The code currently implements:

- exact historical `market_data_journal` L2 reconstruction only;
- no candle, midpoint or one-minute-price proxy fallback;
- causal simulated arrival time using the configured execution latency;
- raw WebSocket payload SHA-256 verification;
- connection hash-chain validation;
- snapshot-before-delta and sequence-continuity validation;
- fail-closed `L2_CONNECTION_INTEGRITY_FAILURE` for a corrupt/gapped target connection;
- exact fee-aware dead-NO execution math;
- benchmark virtual cash, positions and event/region/day/trade capital constraints;
- remaining cash re-evaluated before each sequential fill;
- negative-cash invariant;
- deterministic opportunity ranking and deterministic decision hashes;
- explicit `UNSUPPORTED_VERSION` for an unregistered execution model;
- benchmark result objects kept distinct from research/counterfactual result objects.

## Named regression coverage present

`test_replay_execution.py` covers:

- no L2 at simulated arrival does not use a later snapshot;
- sequence gap invalidates the connection;
- payload-hash mismatch fails closed as L2 integrity failure;
- same inputs/config replay identically;
- later L2 cannot fill an earlier order;
- latency A/B changes execution while the source-input hash remains unchanged;
- capital cap applies to exact fee-aware plan;
- multiple dead markets form independent positions without overspend;
- unknown execution version fails explicitly;
- counterfactual/research output cannot masquerade as benchmark.

`test_replay_execution_portfolio.py` independently asserts sequential best-edge-first sizing cannot spend more than the starting cash balance.

## Static review against 4J-C plan

- exact causal L2: **implemented**
- fatal/gap-invalid connection handling: **implemented**
- exact fees/capital/allocation/latency: **implemented**
- no proxy fallback: **implemented**
- deterministic result hashing: **implemented**
- execution latency A/B on identical source data: **implemented/tested in code**
- unsupported version fails explicitly: **implemented/tested in code**
- benchmark vs counterfactual separation: **implemented/tested in code**

## What is still required before PASS

1. GitHub Actions must start normal jobs again.
2. Run the current corrected branch through the complete gate.
3. Record the exact final Python test count and run ID.
4. Only then check 4J-C / Step 4J completion items in the canonical TODO.

No merge, deployment or real-money execution is authorized by this document.
