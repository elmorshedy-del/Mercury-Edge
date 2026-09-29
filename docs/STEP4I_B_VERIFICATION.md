# Step 4I-B verification — structured fail-closed event ledger

Status: **PASS**

Branch: `paper-rigour-v2`

PR: #5

Code-complete head: `669095db5fdddf60db39d1af713c858b70af1d1c`

GitHub Actions: **Paper Trader CI run 538 (`32762176090`)**

## Implemented

- `sql/021_hard_edge_failure_events.sql`
- `sql/tests/021_hard_edge_failure_events_test.sql`
- `paper_collector/failure_events.py`
- `paper_collector/failure_event_sweeper.py`
- `paper_collector/diagnostic_sweep.py`
- `paper_collector/test_failure_events.py`
- `paper_collector/test_failure_event_sweeper.py`
- audit-daemon integration

The ledger is append-only and separates the diagnostic stage from the disposition class. Stable stages are `source_parse`, `evidence`, `hard_state`, `elimination`, `execution`, `validation`, `settlement`, and `replay`. Dispositions distinguish integrity/fail-closed/non-admission/invariant failures from ordinary economic skips.

## Initial live/audit integrations

The downstream diagnostic sweeper now derives countable events for:

- ASOS off-lattice evidence;
- main/T-group contradiction;
- impossible six-hour max below precise current evidence;
- six-hour extrema whose interval crosses the LST climate-day boundary;
- the intentionally benchmark-deferred 24-hour max channel;
- pure bucket-elimination fail-closed audit findings;
- benchmark execution blocks;
- ordinary no-positive-guaranteed-return / portfolio economic skips as a **separate** disposition;
- rejected/ambiguous validation products with immutable raw-source linkage;
- persisted settlement invariant failures with state/elimination/order identity.

The diagnostic sweep runs after the normal replay audit cycle. It is downstream-only: a failure of the diagnostic sweep is surfaced but cannot stop evidence capture or authorize/alter a benchmark trade.

## Ledger invariants

- deterministic failure identity;
- same fact is idempotent;
- same identity with changed canonical payload raises a collision/integrity error;
- raw-linked events verify raw-source session/station provenance;
- UPDATE/DELETE are rejected by the database immutability trigger (`55000`);
- grouped stage/disposition/reason counts are deterministic;
- economic no-edge decisions are not mislabeled as integrity failures.

## Full verification

GitHub Actions run **538** completed green:

- Python compile: **PASS**
- full Python suite: **255 tests, 0 failures**
- dependency imports: **PASS**
- collector Docker build: **PASS**
- Node checks: **PASS**
- fresh Postgres migrations: **PASS**
- SQL013/016/017/018/019/020/021 regressions: **PASS**

## Next

**Step 4I-C — end-to-end canonical explainability regression.**

No merge or deployment occurred.
