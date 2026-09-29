# Step 4H-A verification — validation lifecycle and settlement authority

Date: 2026-08-21

Branch: `paper-rigour-v2`

PR: #5

Status: **PASS**

Canonical parent: `docs/STEP4_CANONICAL_TODO.md`

Detailed 4H plan: `docs/STEP4H_PLAN.md`

## Purpose

4H-A establishes the pure, source-aware lifecycle boundary between NWS validation products and authoritative contract settlement before any new collector or audit persistence is added.

The core invariant is unchanged:

> DSM and CLI are post-trade validation/corroboration inputs. They cannot raise benchmark hard state or authorize an intraday trade.

Contract-authoritative settlement is a separate object and requires explicit event/date/rules/source provenance.

## Latency-review disposition before implementation

`docs/hard-edge-latency-engineering-review-2026-08-21.md` was reviewed from `main` before 4H work started.

No latency optimization was allowed to preempt the canonical Step 4 sequence. Hot-window AWC polling, lower process-delay experiments, an in-memory fast path and the Release Explorer remain later performance/replay work.

One correctness dependency from that review was incorporated now: settlement-source authority is explicit. NWS DSM/CLI cannot be treated as contract-authoritative merely because they report the same physical station. This matters in particular for event rules whose settlement source is The Weather Company rather than NWS.

## Files

- `paper_collector/settlement_validation.py`
- `paper_collector/test_settlement_validation.py`
- `.github/workflows/paper-ci.yml`
- `paper_collector/Dockerfile`
- `docs/STEP4H_PLAN.md`

## Canonical lifecycle model

`ValidationLifecycle`:

- `CURRENT_DAY_PRELIMINARY`
- `COMPLETED_DAY_PRELIMINARY`
- `AUTHORITATIVE_FINAL`
- `AMBIGUOUS`
- `REJECTED`

`ValidationAuthority`:

- `CORROBORATION_ONLY`
- `CONTRACT_AUTHORITATIVE`
- `EXCHANGE_RESULT`

`ValidationProduct` preserves:

- source and source product id;
- station;
- explicit target climate date when resolvable;
- reported maximum;
- maximum observation time where the product supplies it;
- issuance and Mercury-receipt clocks separately;
- immutable raw-source id and payload hash;
- lifecycle and authority;
- parser/model/calendar versions;
- correction/revision identity;
- fail-closed reason and metadata.

`AuthoritativeSettlement` wraps canonical `SettlementTruth` plus the exact event ticker, rules hash, captured rule-source name, settlement-source name and authority class.

## NWS DSM rules

The parser recognizes the target station summary line rather than merely searching for a temperature value.

A completed form such as:

```text
KNYC DS 21/08 771425/ ...
```

can be classified `COMPLETED_DAY_PRELIMINARY` only when the target DD/MM resolves mechanically to a climate date before the issuance climate date.

A partial form such as:

```text
KNYC DS 1500 21/08 771425/ ...
```

is not completed-day truth. The partial cutoff remains explicit metadata and the product is classified preliminary.

`COR` is preserved as correction metadata. A correction is a new product/version; it does not imply mutation of an earlier product.

The maximum time is interpreted on the fixed LST climate-day clock using `climate_day_bounds`, not civil/DST midnight.

Malformed date/temperature/time fields or a completed form that cannot safely refer to a prior climate day fail closed.

## NWS CLI rules

The parser requires both:

1. an explicit heading date of the form `CLIMATE SUMMARY FOR <MONTH> <DAY> <YEAR>`; and
2. a `MAXIMUM` value.

Lifecycle is determined from the explicit report climate date relative to the issuance climate date:

- target == issue climate date -> `CURRENT_DAY_PRELIMINARY`;
- target < issue climate date -> `COMPLETED_DAY_PRELIMINARY`;
- target > issue climate date -> `AMBIGUOUS`.

A completed-day CLI remains NWS preliminary/corroboration data in this model. It is never silently relabelled authoritative final settlement.

## Hard-state isolation

`ValidationProduct.to_validation_evidence()` emits only `VALIDATION_ONLY` canonical evidence (or `REJECTED`). The regression suite feeds such evidence to `hard_state_accumulator` and verifies that no `HardClimateState` is created.

This is the direct machine-tested guard against DSM/CLI contaminating benchmark trading.

## Authoritative settlement construction

`build_authoritative_settlement(...)` requires:

- event ticker with parseable date;
- event date exactly equal to target climate date;
- station code;
- rules hash;
- source provenance.

For a contract-source settlement object, normalized captured rule-source name must match settlement-source name. A mismatch fails closed.

An exchange-resolved result uses the separate `EXCHANGE_RESULT` authority class rather than pretending to be the external settlement source.

## Regression cases added

- completed DSM maps to the exact previous LST climate date and max time;
- partial DSM remains preliminary;
- DSM correction identity is preserved;
- same-climate-day completed DSM form fails closed as ambiguous;
- unparseable DSM is rejected;
- same-day CLI remains preliminary;
- next-day CLI for a completed day remains preliminary, not final;
- CLI without explicit report date is rejected;
- future CLI target is ambiguous;
- NWS validation evidence cannot become benchmark eligible or raise hard state;
- validation object serialization round-trips deterministically;
- exact captured rule source + settlement source can construct authoritative truth;
- rule-source mismatch fails closed;
- wrong Kalshi event date fails closed;
- exchange result is an explicit authority class.

## Verification

Code-complete branch commit: `de9e973ae110bf99c2a2b16ddc4a75abf04f3c7a`

GitHub Actions `Paper Trader CI` run **449** (`32543567380`):

- Python compile: PASS
- full Python suite: **201 tests, 0 failures**
- dependency import check: PASS
- collector Docker build: PASS
- Node checks: PASS
- full fresh Postgres migrations: PASS
- SQL013 immutable hard-information journal: PASS
- SQL016 immutable hard-state timeline: PASS
- SQL017 immutable Kalshi market journal: PASS
- SQL018 immutable source-transport events: PASS

No Railway deploy or merge occurred.

## Next canonical substep

**4H-B — immutable validation/settlement/audit journal.**

It must persist these versioned lifecycle/authority objects append-only, preserve revisions rather than overwriting them, link exact raw sources, and add a real-Postgres immutability regression before 4H-C collector wiring begins.
