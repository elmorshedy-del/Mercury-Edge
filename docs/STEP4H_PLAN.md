# Step 4H plan — DSM / CLI / settlement auditor

Status: **4H-A PASS on GitHub Actions run 449 (201 Python tests + Node + Docker + Postgres). Next: 4H-B immutable validation/settlement journal.**

Branch: `paper-rigour-v2`

PR: #5

Parent plan: `docs/STEP4_CANONICAL_TODO.md`.

Dedicated H-A evidence: `docs/STEP4H_A_VERIFICATION.md`.

## Why 4H is next

4G-C3 remains blocked on actual MADIS data/live access. It also needs completed-day validation/settlement truth for the full comparison sample. The next unblocked canonical checklist work is therefore 4H.

## Latency-engineering review triage

Reviewed `docs/hard-edge-latency-engineering-review-2026-08-21.md` from `main` before starting 4H.

Decision: **do not preempt the canonical Step 4 sequence with latency optimization.** Hot-window AWC polling, lower paper process-delay experiments, direct in-memory handoff, sub-second fast-path benchmarking and Release Explorer work are performance/replay tasks. They wait until the canonical evidence/settlement/replay path is complete enough to measure them without weakening auditability.

One correctness point from the review is incorporated now: settlement-source authority must remain explicit. NWS DSM/CLI are validation/corroboration and must not automatically be labelled the authoritative Kalshi settlement source for an event whose captured rules identify another source, including The Weather Company.

## Core invariant

Validation products and settlement truth are **post-trade audit inputs**. They cannot create an ordinary intraday benchmark hard-state transition.

```text
exact raw validation/settlement payload
  -> immutable raw_source_journal
  -> source-specific lifecycle parser
  -> canonical ValidationProduct / SettlementTruth
  -> append-only validation/settlement journal
  -> compare against hard-state transitions, eliminations and paper orders
  -> immutable audit result
```

No DSM/CLI parser may feed `hard_state_accumulator` as benchmark evidence.

## Source/lifecycle distinctions

### NWS DSM

A completed Daily Summary Message is validation-only and can describe the previous completed 00:00-23:59 LST climate day. A partial/intermediate DSM remains preliminary. Ambiguous target date, maximum, or maximum-time association fails closed.

### NWS CLI

CLI lifecycle is explicit:

- `CURRENT_DAY_PRELIMINARY`
- `COMPLETED_DAY_PRELIMINARY`
- `AMBIGUOUS` / `REJECTED`

A completed-day CLI remains preliminary NWS climate data in Mercury. It is not silently promoted to final contract truth.

### Contract settlement authority

Authoritative settlement is a separate source class. `SettlementTruth` is contract-authoritative only when its source is compatible with the exact captured event rule snapshot or when it represents the exchange's own resolved result with provenance.

---

# 4H-A — Pure lifecycle normalization — PASS

Implemented:

- `paper_collector/settlement_validation.py`
- `paper_collector/test_settlement_validation.py`
- CI/Docker inclusion
- `docs/STEP4H_A_VERIFICATION.md`

Canonical objects:

- `ValidationLifecycle`
- `ValidationAuthority`
- `ValidationProduct`
- `AuthoritativeSettlement`

Source adapters:

- `parse_nws_dsm(...)`
- `parse_nws_cli(...)`
- `build_authoritative_settlement(...)`

Key rules proven by tests:

- current-day CLI is preliminary;
- next-day CLI can be completed-day preliminary but is not final;
- missing/ambiguous CLI report date fails closed;
- completed DSM maps to exact prior LST climate date and maximum time;
- partial/ambiguous DSM cannot become completed-day truth;
- NWS products remain `CORROBORATION_ONLY` / `VALIDATION_ONLY`;
- validation evidence cannot raise `HardClimateState`;
- authoritative settlement requires exact event-date and rule-source provenance;
- rule-source mismatch fails closed;
- objects are deterministic/versioned and round-trip.

Verification: GitHub Actions run **449** (`32543567380`) on code-complete commit `de9e973ae110bf99c2a2b16ddc4a75abf04f3c7a` — **201 Python tests passed**, Python compile PASS, Docker PASS, Node PASS, full Postgres migrations PASS, SQL013/016/017/018 immutable regressions PASS.

No merge or deployment occurred.

---

# 4H-B — Immutable validation/settlement journal — NEXT

Create append-only database/persistence for:

1. normalized validation products;
2. authoritative settlement truths;
3. settlement audit results/findings.

Requirements:

- exact raw-source links are mandatory for persisted source products/truth;
- parser/model/rules/calendar versions are preserved;
- deterministic canonical payload hash on every row;
- same stable identity + same bytes is idempotent;
- same stable identity + different bytes fails closed;
- revised/corrected products create new rows and may reference a previous version;
- no historical product/truth/audit row is overwritten;
- database triggers reject UPDATE/DELETE;
- a real Postgres regression proves immutability and revision coexistence.

4H-B must pass full CI before 4H-C begins.

---

# 4H-C — Raw-first collectors/adapters

Wire collection without weakening raw-first discipline:

- exact `api.weather.gov` DSM/CLI HTTP entity bytes are stored before parsing;
- source product id/issuance time and Mercury receipt are separate clocks;
- repeated/corrected issuances remain separately inspectable;
- contract settlement/exchange result is captured separately from NWS validation products;
- existing legacy `product_releases` may support research/backfill but cannot be relabelled immutable raw-first truth unless exact raw provenance exists.

No Railway deployment is required merely to complete H-C implementation/tests.

---

# 4H-D — Transition/trade settlement audit

For authoritative completed settlement truth:

- compare final max against canonical hard-state transitions used for benchmark trading;
- `final_max_f < proven_daily_high_min_f` => **critical invariant failure**;
- compare exact elimination/order provenance against settled market result;
- an outcome Mercury proved impossible settling YES => **critical invariant failure**;
- NWS-only disagreement is a validation discrepancy, not an authoritative exchange failure when NWS is not the captured contract source;
- corrections/revisions append new audit results and never rewrite historical knowledge or prior audits.

Acceptance:

- DSM/CLI cannot trigger benchmark hard state;
- preliminary CLI is never final;
- authoritative truth traces to exact transition/raw evidence chain;
- impossible-bucket YES settlement is critical;
- final max below traded hard bound is critical;
- non-authoritative NWS disagreement is classified separately;
- correction/revision history survives intact.

---

# 4H completion gate

4H is complete only when A-D, full CI and immutable DB regressions pass. Then the canonical TODO and refactor documentation are advanced to 4I.

## Explicitly deferred performance work

The latency review remains preserved, but these are **not prerequisites for 4H**:

- station-specific AWC hot-poll windows;
- reducing/calibrating the 6-second paper processing delay;
- direct in-memory/event-queue handoff instead of DB polling;
- sub-second internal latency benchmarking;
- Release Explorer / market-reaction visualization.
