# Step 4H plan — DSM / CLI / settlement auditor

Status: **LOCKED BEFORE IMPLEMENTATION**

Branch: `paper-rigour-v2`

PR: #5

Parent plan: `docs/STEP4_CANONICAL_TODO.md`.

## Why 4H is next

4G-C3 remains blocked on actual MADIS data/live access. It also needs completed-day validation/settlement truth for the full comparison sample. The next unblocked canonical checklist work is therefore 4H.

## Latency-engineering review triage

Reviewed `docs/hard-edge-latency-engineering-review-2026-08-21.md` from `main` before starting 4H.

Decision: **do not preempt the canonical Step 4 sequence with latency optimization.** The review's hot-window polling, lower paper process-delay experiments, direct in-memory handoff and fast-path suggestions are performance work. They can wait until the canonical evidence/settlement/replay path is complete and benchmarked. Speed must not be optimized ahead of correctness or causal replay.

One point from the review *does* affect 4H design immediately: settlement-source authority must remain explicit. Kalshi daily-temperature contracts transitioned settlement authority to The Weather Company for contracts from 2026-08-14 onward. NWS DSM/CLI can therefore be valuable validation/corroboration but must not automatically be labelled the authoritative Kalshi settlement source for those events. 4H will model source authority separately from product lifecycle.

This is consistent with current Kalshi market pages, which state the Aug-14 transition to The Weather Company, and with NWS documentation that CLI products are preliminary and issued at least twice daily.

## Core invariant

Validation products and settlement truth are **post-trade audit inputs**. They cannot create an ordinary intraday benchmark hard-state transition.

The 4H path is:

```text
exact raw validation/settlement payload
  -> immutable raw_source_journal
  -> source-specific lifecycle parser
  -> canonical validation product / SettlementTruth
  -> append-only validation/settlement journal
  -> compare against hard-state transitions, eliminations and paper orders
  -> immutable audit findings / critical invariant failures
```

No DSM/CLI parser may feed `hard_state_accumulator` as benchmark evidence.

## Source/lifecycle distinctions that must remain separate

### NWS DSM

Official ASOS documentation describes the completed Daily Summary Message as covering 00:00-23:59 LST for the previous day and normally transmitting early the following day. Mercury may use a completed DSM as **validation-only** evidence when the target climate date is explicit and the product form is unambiguous.

Any partial/intermediate DSM form, ambiguous target date or ambiguous maximum/time association fails closed for completed-day validation.

### NWS CLI

NWS directives say CLI is issued at least twice daily:

- early local time to capture the **previous completed LST day**;
- late afternoon/early evening to capture the **current incomplete day**;
- optional extra issuances may occur.

Therefore CLI lifecycle is explicit:

- `CURRENT_DAY_PRELIMINARY` — current day is still open;
- `COMPLETED_DAY_PRELIMINARY` — target LST day has ended and the CLI refers to that completed day;
- `AMBIGUOUS` — target day cannot be resolved safely.

Even a completed-day CLI remains preliminary NWS climate data; it is not silently promoted to final certified climate truth.

### Kalshi / contract settlement authority

Authoritative contract settlement is a separate source class. For modern daily-temperature contracts the exact event rule snapshot/settlement source controls which external source is authoritative. The auditor must not assume that NWS CLI/DSM equals contract settlement merely because values usually agree.

`SettlementTruth` is only authoritative for a contract/event when its source is compatible with the exact captured rule snapshot for that event/date, or when it represents the exchange's own resolved result/value with provenance.

## 4H-A — Pure lifecycle normalization

Implement a source-neutral validation module before collector wiring.

Required concepts:

- `ValidationLifecycle`: current-day preliminary, completed-day preliminary, final/authoritative settlement, ambiguous/rejected.
- `ValidationAuthority`: corroboration-only, contract-authoritative, exchange-result.
- `ValidationProduct`: source, source product id, station, climate date, reported max, issued/observed clocks, raw source id/hash, lifecycle, authority, parser/model/calendar versions and fail-closed reason.
- deterministic IDs/hashes from exact source identity + parser/model versions.

Required source adapters:

- NWS DSM parser with explicit target `DD/MM`, max and max-time parsing; completed-day status only when the issuance/target date relationship is mechanically valid under the station's LST calendar;
- NWS CLI parser extracting an explicit report date and `MAXIMUM`; lifecycle determined from target climate date versus issuance time, never from `MAXIMUM` alone;
- authoritative settlement adapter that requires explicit event/date/station/source/rules provenance.

4H-A must not do network I/O or DB writes.

### 4H-A acceptance tests

- current-day CLI is preliminary and cannot become final truth;
- next-day CLI for the previous completed LST day is completed-day preliminary only;
- ambiguous/missing CLI report date fails closed;
- completed DSM maps to the exact prior LST climate date and preserves max time;
- partial/ambiguous DSM cannot be labelled completed-day validation;
- every NWS product remains validation/corroboration authority only;
- authoritative settlement construction fails closed without matching event/date/station/rule-source provenance;
- validation objects are deterministic/versioned and round-trip.

Full CI must pass before 4H-A is marked complete.

## 4H-B — Immutable validation/settlement journal

Create append-only DB tables for:

- normalized validation products;
- authoritative settlement truths;
- settlement audit comparisons/findings.

All rows must retain raw source links and parser/model/rules/calendar versions. UPDATE/DELETE must be rejected by DB triggers. Revised/corrected products create new rows and may reference the prior version; no old product is overwritten.

## 4H-C — Raw-first collectors/adapters

Wire collection without weakening raw-first discipline:

- exact api.weather.gov DSM/CLI HTTP entity bytes are stored before parsing;
- source product id/issuance time and Mercury receipt are separate clocks;
- repeated/corrected product issuances remain separately inspectable;
- contract settlement/raw exchange result is captured separately from NWS validation products;
- existing legacy `product_releases` can be read for research/backfill but must not be relabelled as immutable raw-first truth unless an exact raw record exists.

No Railway deploy is required or allowed merely to finish implementation/tests.

## 4H-D — Transition/trade settlement audit

For authoritative completed settlement truth:

- compare final max against every canonical hard-state transition used for benchmark trading;
- if `final_max_f < proven_daily_high_min_f`, emit a **critical invariant failure**;
- compare each exact `BucketElimination`/paper order to the settled market result;
- if a bucket marked mathematically impossible settles YES, emit a **critical invariant failure** with state/elimination/order/raw provenance;
- NWS-only disagreement is a validation discrepancy and must not be mislabeled as an exchange settlement failure when NWS is not the event's authoritative source;
- corrections/revisions append new audit results; they do not rewrite what Mercury knew or previously audited.

### 4H-D acceptance tests

- DSM/CLI cannot enter the benchmark hard-state accumulator;
- same-day preliminary CLI is never final;
- final settlement can trace to the exact hard-state transition and raw evidence chain;
- impossible-bucket YES settlement produces a critical failure;
- final max below a traded hard lower bound produces a critical failure;
- non-authoritative NWS disagreement is classified separately from authoritative settlement failure;
- correction/revision history is preserved.

## 4H completion gate

4H is complete only when A-D, full CI and immutable DB regressions pass. Then update the canonical TODO and refactor log with exact commit/run/test counts.

## Explicitly deferred performance work from the latency review

The following remain valuable but are **not prerequisites for 4H**:

- station-specific AWC hot-poll windows;
- reducing/calibrating the 6-second paper processing delay;
- direct in-memory/event-queue handoff instead of DB polling;
- sub-second internal latency target benchmarking;
- release-explorer UI and market-reaction visualization.

These belong after correctness/replay instrumentation can measure them without compromising auditability.