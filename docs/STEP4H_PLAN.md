# Step 4H plan — DSM / CLI / settlement auditor

Status: **PASS — 4H-A run 449; 4H-B run 465; 4H-C run 477; 4H-D run 501. Next unblocked canonical step: 4I debugging/explainability.**

Branch: `paper-rigour-v2`

PR: #5

Parent plan: `docs/STEP4_CANONICAL_TODO.md`.

Verification notes:
- `docs/STEP4H_A_VERIFICATION.md`
- `docs/STEP4H_B_VERIFICATION.md`
- `docs/STEP4H_C_VERIFICATION.md`
- `docs/STEP4H_D_VERIFICATION.md`

## Dependency note

4G-C3 remains blocked on actual MADIS data/live access. Step 4H supplies the completed-day validation/settlement layer needed for that future empirical gate, but **does not promote MADIS evidence to benchmark trust**.

## Latency-engineering review triage

`docs/hard-edge-latency-engineering-review-2026-08-21.md` was reviewed before 4H. No latency optimization preempts the canonical Step 4 sequence. Hot-window polling, lower process-delay experiments, in-memory handoff, sub-second fast-path benchmarking and Release Explorer work remain later performance/replay tasks.

One correctness point from that review is now fully incorporated: **settlement-source authority is explicit.** NWS DSM/CLI are validation/corroboration and are not automatically contract-authoritative when the captured Kalshi rules identify another settlement source.

## Core invariant

Validation products and settlement truth are post-trade audit inputs. They cannot create ordinary intraday benchmark hard state.

```text
exact raw validation/settlement payload
  -> immutable raw_source_journal
  -> source-specific lifecycle parser
  -> canonical ValidationProduct / settlement object
  -> append-only validation/settlement journal
  -> compare against hard-state transition + elimination + paper order
  -> immutable audit result
```

---

# 4H-A — Pure lifecycle normalization — PASS

Implemented in `paper_collector/settlement_validation.py` with tests in `test_settlement_validation.py`.

Key facts:
- DSM completed/partial/correction lifecycle is explicit and uses the fixed LST climate calendar.
- CLI requires an explicit report date and remains preliminary NWS validation data.
- NWS products are `CORROBORATION_ONLY` / `VALIDATION_ONLY` and cannot raise `HardClimateState`.
- authoritative numeric settlement construction requires exact event-date/rule-source provenance.

Verification: run **449** (`32543567380`), commit `de9e973ae110bf99c2a2b16ddc4a75abf04f3c7a`, **201 Python tests** + compile/Docker/Node/Postgres PASS.

---

# 4H-B — Immutable validation/settlement/audit journal — PASS

Implemented:
- `sql/019_validation_settlement_audit.sql`
- `paper_collector/settlement_journal.py`
- `paper_collector/test_settlement_journal.py`
- `sql/tests/019_validation_settlement_audit_test.sql`

Facts:
- normalized validation products, authoritative settlement truths and audit results are separate append-only ledgers;
- exact raw-source links and canonical hashes are required;
- corrections/revisions coexist instead of overwriting history;
- DB triggers reject UPDATE/DELETE with SQLSTATE `55000`.

Verification: run **465** (`32543845505`), commit `ba5a1fb0daa8b893b9a82d57d30e73ce1627f0c6`, **209 Python tests** + compile/Docker/Node/Postgres/SQL019 PASS.

---

# 4H-C — Raw-first collectors/adapters — PASS

Implemented:
- `paper_collector/validation_collector.py`
- `paper_collector/test_validation_collector.py`
- explicit `NWS_VALIDATION_LOCATIONS` in `paper_collector/stations.py`

Facts:
- NWS product index/detail HTTP bytes are journaled before JSON or climate-product parsing;
- source issuance and Mercury receipt clocks remain separate;
- malformed/non-success entities are still preserved;
- corrected DSM products can reference prior immutable canonical versions;
- legacy `product_releases` is not promoted into canonical truth;
- nested Kalshi settled-event bytes are journaled before market-result interpretation;
- unresolved market results fail closed;
- no unproven conversion from Kalshi `expiration_value` to physical final temperature is made.

The collector/adapters are implemented and testable but are **not runtime-enabled or deployed** during Step 4.

Verification: run **477** (`32544064514`), commit `f0451537aea502f7feb5a96304d40481fc1dd80c`, **218 Python tests** + compile/Docker/Node/Postgres/SQL019 PASS.

---

# 4H-D — Transition/trade settlement audit — PASS

Implemented:
- `paper_collector/settlement_audit_domain.py`
- `paper_collector/settlement_auditor.py`
- `paper_collector/test_settlement_auditor.py`
- `paper_collector/test_exchange_settlement_journal.py`
- `sql/020_exchange_market_settlement.sql`
- `sql/tests/020_exchange_market_settlement_test.sql`

Hard invariants:

1. **Hard-state vs authoritative final max**
   - authoritative `final_max_f < proven_daily_high_min_f` => `critical / invariant_failure / HARD_STATE_EXCEEDS_FINAL_MAX`.

2. **Eliminated bucket vs exchange settlement**
   - exact market Mercury proved impossible settles `YES` => `critical / invariant_failure / IMPOSSIBLE_BUCKET_SETTLED_YES`.
   - exact eliminated market settles `NO` => pass.

3. **NWS disagreement when NWS is not contract authority**
   - classified as validation discrepancy/warning only;
   - never mislabeled as an exchange/contract invariant failure.

4. **Identity gates**
   - session, station, climate date, event, market, hard state, elimination and exact event rules must agree;
   - mismatch fails closed.

5. **Revision behavior**
   - newer corrections/final truths generate new deterministic audit identities;
   - prior audit conclusions remain immutable historical outputs.

6. **No invented numeric settlement semantics**
   - exchange market results can be authoritative for contract outcomes without Mercury pretending an undocumented API field is the physical final temperature.

Verification: GitHub Actions **run 501 (`32544466584`)** on code-complete head `4e62e0ecfad2075780b2fb53f7c5e6f3f4736b44`:
- **237 Python tests, 0 failures**;
- Python compile PASS;
- dependency import PASS;
- collector Docker PASS;
- Node PASS;
- full Postgres migrations PASS;
- SQL013/016/017/018/019/020 regressions PASS.

See `docs/STEP4H_D_VERIFICATION.md`.

---

# 4H completion gate — SATISFIED

Step 4H-A through 4H-D are green. The settlement/validation layer is now adequate to audit the benchmark hard-edge chain without contaminating trade-time knowledge.

**Next: Step 4I — world-class debugging and explainability.**

No merge, Railway deployment, portfolio reset or real-money execution occurred.

## Explicitly deferred performance work

- station-specific AWC hot-poll windows;
- reducing/calibrating the paper processing delay;
- direct in-memory/event-queue handoff;
- sub-second internal latency benchmarking;
- Release Explorer / market-reaction visualization.
