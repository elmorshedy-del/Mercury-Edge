# Step 4J-B verification — canonical hard-state / elimination replay

Status: **PASS**

GitHub Actions: **run 575 (`32765146488`)**

Verified branch commit: `9f8bd9c11d32875c4f19d0de5fc7485a1aec016f`

Primary commits:
- `b27dd64f601af197ae2bb2d1e3041af082c10f2e` — locked v1 identity-compatibility rule before implementation.
- `4fdabe65bf1a460cffd28c0734c7efc44eb6044c` — source-byte hard-state/elimination replay.
- `9aaae7b6351c6e835bd9ab5cf22adce843da6e8a` — Step 4J-B regressions.
- `ee75e25f72ba74585a12cc5ecba1218e8e177a94` — collector image inclusion.
- `9f8bd9c11d32875c4f19d0de5fc7485a1aec016f` — CI inclusion.

## What passed

- exact immutable AWC batch bytes are reparsed from scratch; prior evidence/state rows never seed replay;
- the old `live_weather_journal.weather_id` is used only as a strictly matched identity index required to reproduce the current v1 evidence/state identity;
- ASOS current/T/six-hour evidence reconstructs the monotonic state sequence deterministically;
- same-receipt current + hidden six-hour maximum is one atomic strongest transition;
- later lower temperature cannot reduce the bound;
- rule snapshots are selectable only when `captured_at <= state.first_known_at`;
- no causal rule snapshot means no authoritative elimination;
- Aug-18/Aug-19 style wrong-date routing fails closed through the pure elimination engine;
- current-version identity mismatch fails closed;
- unavailable parser/evidence/calendar/state/elimination versions return explicit `UNSUPPORTED_VERSION` rather than silently substituting logic.

## Full regression result

- **272 Python tests, 0 failures**;
- Python compile PASS;
- collector Docker build PASS;
- Node check PASS;
- full Postgres migrations PASS;
- SQL013/016/017/018/019/020/021 regressions PASS;
- real-Postgres Step 4I explainability regression PASS.

Step 4J-B is complete. Next: 4J-C exact market/execution replay and A/B version selection.
