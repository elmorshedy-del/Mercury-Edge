# Step 4H-D verification — transition/trade settlement audit

Status: **PASS**

Branch: `paper-rigour-v2`

PR: #5

Verified code-complete head: `4e62e0ecfad2075780b2fb53f7c5e6f3f4736b44`

GitHub Actions: **Paper Trader CI run 501 (`32544466584`)**

## What is proved

Step 4H-D now audits completed validation/settlement information without changing the historical hard-state record.

The authoritative chain is:

```text
immutable trade-time raw evidence
  -> versioned SettlementEvidence
  -> canonical HardClimateState transition
  -> exact BucketElimination
  -> benchmark paper order
  -> immutable exchange/contract settlement capture
  -> deterministic settlement audit result
```

NWS DSM/CLI remain validation/corroboration inputs. They cannot create benchmark hard-state transitions and are not automatically treated as the contract-authoritative source.

## Implemented invariants

- A benchmark order is rehydrated only if its stored canonical hard state, bucket elimination, event, station, market and rules hash agree.
- Exchange settlement capture is accepted only when it is fully resolved, belongs to the same session/station/event, matches its immutable raw hash, and the exact event rule snapshot used by the trade exists.
- If a market Mercury proved impossible later settles `YES`, the auditor emits `IMPOSSIBLE_BUCKET_SETTLED_YES` with severity `critical` and status `invariant_failure`.
- If the same eliminated market settles `NO`, the result is a normal pass.
- If an authoritative numeric final maximum is below the hard lower bound used by the trade, the auditor emits `HARD_STATE_EXCEEDS_FINAL_MAX` as a critical invariant failure.
- NWS validation disagreement with authoritative settlement is classified separately as `NON_AUTHORITATIVE_VALIDATION_DISAGREEMENT`; it is a warning/discrepancy, not a contract invariant failure.
- Revised/corrected truth creates a distinct immutable settlement/audit identity. Historical state, orders and prior audits are never rewritten.
- Exchange market-result settlement is kept separate from numeric final-temperature truth. Mercury does not infer a physical final temperature from an exchange field unless that semantic is independently established.

## Primary files

- `paper_collector/settlement_audit_domain.py`
- `paper_collector/settlement_auditor.py`
- `paper_collector/settlement_validation.py`
- `paper_collector/settlement_journal.py`
- `paper_collector/validation_collector.py`
- `paper_collector/test_settlement_auditor.py`
- `paper_collector/test_exchange_settlement_journal.py`
- `sql/020_exchange_market_settlement.sql`
- `sql/tests/020_exchange_market_settlement_test.sql`

## Key regression coverage

The verified suite includes named cases proving:

- exact hard-state/elimination identity is required for a benchmark trade proof;
- a non-eliminated market cannot become a hard-edge trade proof;
- paper-order audit rehydrates the exact canonical trade proof;
- exchange normalization requires the exact rule snapshot used by the trade;
- station/date/event/market/rules mismatches fail closed;
- eliminated market settling `YES` is critical;
- eliminated market settling `NO` passes;
- final max below traded hard bound is critical;
- final max at/above the hard bound passes;
- NWS disagreement is non-authoritative validation discrepancy only;
- revised truth produces a new audit identity without mutation;
- exchange settlement journal is raw-linked, insert-only and hash-checked.

## Full verification

GitHub Actions run **501** completed green:

- Python compile: **PASS**
- full Python suite: **237 tests, 0 failures**
- dependency import check: **PASS**
- collector Docker build: **PASS**
- Node checks: **PASS**
- fresh Postgres migrations: **PASS**
- SQL013 immutable raw/evidence journal: **PASS**
- SQL016 immutable hard-state timeline: **PASS**
- SQL017 immutable market-data journal: **PASS**
- SQL018 immutable source-transport events: **PASS**
- SQL019 immutable validation/settlement audit journal: **PASS**
- SQL020 exchange-market settlement truth shape/immutability: **PASS**

## Step 4H completion

With 4H-A through 4H-D verified, **Step 4H is PASS**. The next unblocked canonical checklist item is **Step 4I — debugging and explainability**.

No merge, Railway deployment, portfolio reset or real-money execution occurred.
