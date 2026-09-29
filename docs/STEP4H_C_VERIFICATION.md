# Step 4H-C verification — raw-first validation / exchange-result collectors

Date: 2026-08-21

Branch: `paper-rigour-v2`

PR: #5

Status: **PASS — collector/adapters implemented and tested; not runtime-enabled or deployed.**

Parent plan: `docs/STEP4H_PLAN.md`

## Purpose

4H-C closes the source-capture gap identified before 4H: DSM/CLI and exchange settlement results now have a canonical raw-first collection path instead of relying on legacy parsed rows.

The ordering invariant is machine-tested:

```text
HTTP entity bytes
  -> raw_source_journal
  -> JSON/product identity checks
  -> H-A lifecycle normalization
  -> H-B immutable validation journal
```

A malformed or non-success source response is still preserved as an immutable raw capture. Parsing failure cannot erase what Mercury actually received.

## Files

- `paper_collector/validation_collector.py`
- `paper_collector/test_validation_collector.py`
- `paper_collector/stations.py`
- `.github/workflows/paper-ci.yml`
- `paper_collector/Dockerfile`

## Explicit NWS product-location map

`paper_collector/stations.py` now contains `NWS_VALIDATION_LOCATIONS` for every canonical Python `WEATHER_STATIONS` entry.

The product-location identifier is explicit rather than inferred from ICAO because `api.weather.gov` climate-product lookup uses NWS product locations. A regression requires exact station-map coverage.

## HTTP capture primitive

`HttpEntity` preserves:

- exact response body bytes;
- HTTP status and headers;
- request-start wall clock;
- request-start monotonic clock;
- Mercury receipt wall clock;
- receipt epoch nanoseconds;
- receipt monotonic nanoseconds;
- request RTT derived from monotonic clocks;
- exact payload SHA-256.

`fetch_http_entity(...)` also retains HTTP error entity bytes. `journal_http_entity(...)` turns the entity into the existing immutable `RawCapture` contract before any source-specific interpretation.

## NWS DSM / CLI collection

`collect_nws_validation_once(...)`:

1. requires a canonical station and explicit NWS product location;
2. fetches the NWS product index;
3. writes the exact index bytes to `raw_source_journal` **before JSON decoding**;
4. validates the index/product type;
5. fetches each new product detail;
6. writes exact detail bytes to `raw_source_journal` **before product parsing**;
7. checks product id, product code, product text and issuance-time consistency;
8. runs the already-tested H-A DSM/CLI lifecycle parser;
9. preserves correction lineage where a prior canonical validation version exists;
10. persists through the H-B immutable validation journal.

The canonical collector does **not** read or promote legacy `product_releases` as raw-first truth. Existing rows there remain research/backfill material unless exact immutable raw provenance exists independently.

## Exchange settlement-result capture

`capture_kalshi_settled_event_once(...)` fetches the nested event payload and journals its exact bytes before inspecting market results.

It preserves explicit per-market `yes` / `no` results and fails closed if any nested market is unresolved or if event identity/payload structure is invalid.

It deliberately does **not** infer a final physical temperature from `expiration_value` or other market fields whose settlement-value semantics have not yet been mechanically proven for this contract family. This prevents an exchange outcome from being mislabeled a numeric climate truth.

The raw exchange result is sufficient for H-D to test the strongest contract-level invariant: a bucket Mercury proved mathematically impossible must never settle YES.

## Runtime activation boundary

This substep implements the canonical collectors/adapters but does **not** add a new always-on child to `runner.py` and does not deploy them. That is intentional:

- Step 4 remains unmerged/unpromoted;
- production activation is a deployment/runtime decision, not evidence-model correctness;
- the canonical collector can be invoked deterministically in replay/tests without silently changing the currently running Railway topology.

When the whole Step 4 promotion gate is reached, runtime scheduling can be enabled deliberately from this documented entry point rather than recreating source logic.

## Regression cases added

- every canonical weather station has an explicit NWS product location;
- malformed NWS index JSON is raw-journaled before parse failure;
- product detail bytes are journaled before validation persistence;
- canonical validation product links the exact detail raw id and payload hash;
- source issuance and Mercury receipt remain distinct clocks;
- product identity mismatch preserves raw bytes but creates no normalized product;
- corrected DSM links to a prior immutable validation version without mutation;
- non-success detail response is still raw-journaled;
- canonical collection never consults legacy `product_releases`;
- settled Kalshi event is raw-journaled before market-result interpretation;
- invalid settled-event JSON is preserved and fails closed;
- any unresolved nested market prevents a fully-resolved settlement claim.

## Verification

Code-complete branch commit: `f0451537aea502f7feb5a96304d40481fc1dd80c`

GitHub Actions `Paper Trader CI` run **477** (`32544064514`):

- Python compile: PASS
- full Python suite: **218 tests, 0 failures**
- dependency import check: PASS
- collector Docker build: PASS
- Node checks: PASS
- full fresh Postgres migrations: PASS
- SQL013 immutable hard-information journal: PASS
- SQL016 immutable hard-state timeline: PASS
- SQL017 immutable Kalshi market journal: PASS
- SQL018 immutable source-transport events: PASS
- SQL019 immutable validation/settlement/audit journal: PASS

No Railway deploy or merge occurred.

## Next canonical substep

**4H-D — transition/trade settlement audit.**

H-D must use authoritative exchange market results for impossible-bucket outcome checking, use numeric final-max truth only when source authority/provenance is actually established, classify non-authoritative NWS disagreement separately, and persist every conclusion through the H-B immutable audit journal.
