# Step 4H plan — DSM / CLI / settlement auditor

Status: **4H-A PASS run 449; 4H-B PASS run 465; 4H-C PASS run 477. Next: 4H-D transition/trade settlement audit.**

Branch: `paper-rigour-v2`

PR: #5

Parent plan: `docs/STEP4_CANONICAL_TODO.md`.

Verification notes:
- `docs/STEP4H_A_VERIFICATION.md`
- `docs/STEP4H_B_VERIFICATION.md`
- `docs/STEP4H_C_VERIFICATION.md`

## Why 4H is next

4G-C3 remains blocked on actual MADIS data/live access and needs completed-day validation/settlement truth. The next unblocked canonical work is 4H.

## Latency-engineering review triage

`docs/hard-edge-latency-engineering-review-2026-08-21.md` was reviewed before 4H. No latency optimization preempts the canonical Step 4 sequence. Hot-window polling, lower process-delay experiments, in-memory handoff, sub-second fast-path benchmarking and Release Explorer work remain later performance/replay tasks.

One correctness point is incorporated now: **settlement-source authority is explicit.** NWS DSM/CLI are validation/corroboration and are not automatically contract-authoritative when the captured Kalshi rules identify another settlement source.

## Core invariant

Validation products and settlement truth are post-trade audit inputs. They cannot create ordinary intraday benchmark hard state.

```text
exact raw validation/settlement payload
  -> immutable raw_source_journal
  -> source-specific lifecycle parser
  -> canonical ValidationProduct / SettlementTruth
  -> append-only validation/settlement journal
  -> compare against hard-state transitions, eliminations and paper orders
  -> immutable audit result
```

---

# 4H-A — Pure lifecycle normalization — PASS

Implemented in `paper_collector/settlement_validation.py` with tests in `test_settlement_validation.py`.

Key facts:
- DSM completed/partial/correction lifecycle is explicit and uses the fixed LST climate calendar.
- CLI requires an explicit report date and remains preliminary NWS validation data.
- NWS products are `CORROBORATION_ONLY` / `VALIDATION_ONLY` and cannot raise `HardClimateState`.
- authoritative settlement construction requires exact event-date/rule-source provenance.

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

The collector/adapters are implemented and testable but are **not runtime-enabled or deployed** during Step 4. Production activation remains a later explicit deployment decision.

Verification: run **477** (`32544064514`), commit `f0451537aea502f7feb5a96304d40481fc1dd80c`, **218 Python tests** + compile/Docker/Node/Postgres/SQL019 PASS.

---

# 4H-D — Transition/trade settlement audit — NEXT

## Inputs

H-D consumes only canonical/auditable facts:

- authoritative numeric `SettlementTruth` when a final max is actually source-authorized;
- authoritative exchange market result captured from exact Kalshi bytes;
- canonical `HardClimateState` transition;
- canonical `BucketElimination` proof;
- benchmark paper-order identity/provenance;
- optional NWS `ValidationProduct` for corroboration/discrepancy only.

## Required invariant checks

1. **Hard-state vs authoritative final max**
   - if authoritative `final_max_f < proven_daily_high_min_f`: `critical / invariant_failure / HARD_STATE_EXCEEDS_FINAL_MAX`.

2. **Eliminated bucket vs exchange settlement**
   - if an exact market Mercury proved impossible settles `YES`: `critical / invariant_failure / IMPOSSIBLE_BUCKET_SETTLED_YES`.
   - a NO settlement for an eliminated bucket is a pass/corroboration.

3. **NWS disagreement when NWS is not contract authority**
   - classify as validation discrepancy/warning;
   - never mislabel it as an exchange/contract invariant failure.

4. **Identity gates**
   - station, climate date, event and market must agree exactly;
   - mismatches fail closed and cannot be counted as a valid pass/failure comparison.

5. **Revision behavior**
   - newer corrections/final truths generate new deterministic audit rows;
   - prior audit conclusions remain immutable historical outputs.

## H-D acceptance tests

- clean hard state <= final max passes;
- authoritative final max below hard state is critical;
- eliminated market settling NO passes;
- eliminated market settling YES is critical;
- non-eliminated market result cannot produce the impossible-bucket invariant;
- station/date/event/market mismatch fails closed;
- non-authoritative NWS disagreement is warning/discrepancy only;
- audit output carries settlement/state/elimination/order/raw provenance;
- repeated same inputs are deterministic/idempotent;
- revised truth creates a distinct audit identity without mutating the prior result.

Full Python/Node/Docker/Postgres CI must pass before 4H is marked complete.

---

# 4H completion gate

4H is complete only after H-D and full regressions pass. Then update:

- `docs/STEP4_CANONICAL_TODO.md`;
- `docs/HARD_STATE_REFACTOR.md`;
- a dedicated H-D/4H verification note;

and advance to 4I. No merge/deploy occurs merely because 4H passes.

## Explicitly deferred performance work

- station-specific AWC hot-poll windows;
- reducing/calibrating the paper processing delay;
- direct in-memory/event-queue handoff;
- sub-second internal latency benchmarking;
- Release Explorer / market-reaction visualization.
