# Information Visibility J1 — Raw Coverage Audit

Date: 2026-08-20

Branch: `paper-rigour-v2`

Status: **PASS for current AWC + Kalshi causal capture substrate, with explicit timing limitations below.**

This audit implements the raw-first requirement in `INFORMATION_VISIBILITY_ARCHITECTURE_TODO.md`: later crowd/public-state models must be disposable projections over immutable source records.

## 1. NOAA/AWC weather path

Current live path: `paper_collector/weather_collector.py`.

Verified behavior:

- The HTTP response entity is read as bytes before JSON parsing.
- The exact response bytes are inserted into `raw_source_journal` before report-level normalization.
- Each capture records Mercury request start, response completion, wall-clock receipt, monotonic receipt and request RTT metadata.
- Parsed `live_weather_journal` rows link back to the immutable `raw_source_journal` row through `raw_source_id`.
- Raw METAR text is preserved on the parsed row.
- AWC `receiptTime` is preserved separately as `source_received_at` on the parsed weather row and remains present in the raw JSON payload.
- The immutable raw-source table is protected by a database trigger rejecting UPDATE/DELETE.
- Identical source bytes received later remain a distinct causal capture because receipt time is part of raw-capture identity.

Important timing limitation:

- Mercury currently polls AWC on a configured cadence (default 10 seconds).
- Therefore Mercury's first successful public fetch is a conservative upper bound on when the ordinary public product became fetchable; it is **not** automatically the exact first-public timestamp.
- AWC `receiptTime` is preserved but must not be relabelled as source publication/first-fetchability unless that semantic equivalence is separately established.
- Visibility/replay models must preserve this distinction rather than backdating public knowledge to physical observation time.

No raw weather gap was found that would require recollecting current AWC data for the planned public-vs-Mercury analysis.

## 2. Kalshi market path

Current live path: `paper_collector/collector.py`.

Verified behavior:

- Raw WebSocket text is preserved before market-state interpretation.
- Order-book snapshots/deltas and trade messages are both subscribed and journaled.
- Each message records source sequence (`sid`/`seq` where supplied), connection identity, exchange timestamp when supplied, Mercury wall-clock receipt, nanosecond receipt and monotonic receipt.
- Exact received UTF-8 text is hashed.
- Per-connection chain hashes preserve message-order auditability.
- Sequence gaps and delta-before-snapshot conditions are explicit fatal/invalidation events rather than silently reconstructed.
- REST order-book reads are stored as audit cross-checks and are not used as substitutes for the causal WebSocket stream.
- Open market discovery covers the configured daily-temperature series so the journal can reconstruct event-wide bucket repricing, not only the market Mercury trades.

Gap found during this audit:

- `market_data_journal` was insert-only by application behavior but did not have the same database-level UPDATE/DELETE protection as `raw_source_journal`.

Fix:

- `sql/017_market_data_journal_immutability.sql` adds the same immutable-journal trigger to the raw Kalshi market journal.
- `sql/tests/017_market_data_journal_immutability_test.sql` verifies UPDATE and DELETE are rejected with SQLSTATE `55000`.

## 3. Historical market-rule context

Settlement/event rule snapshots are preserved separately from the raw WebSocket stream. They are required to interpret which bucket each price represented historically. They are not a substitute for raw market messages.

A later visibility/replay audit should explicitly verify that every event/day selected for analysis has a causally available rule snapshot and that revisions are retained rather than silently replacing historical strike semantics.

## 4. MADIS/LDM

Current Step 4G work defines the MADIS research adapter/reconstruction boundary, but live LDM transport is not yet enabled.

Requirement remains unchanged:

- a future LDM receiver must write the exact received raw record into the immutable raw-source path before normalization or rolling-five-minute reconstruction;
- archive availability must never be treated as contemporaneous live receipt time.

## 5. DSM / CLI / settlement truth

These validation sources are not yet fully implemented as raw-first collectors. This does not block current AWC/MADIS information-lead research, but it remains required before Step 4H/J can claim complete settlement-lifecycle replay.

## 6. Result

For the current intraday question — **what did Mercury know, what ordinary public AWC information was available, and what was Kalshi quoting/trading at that moment?** — the raw capture substrate is sufficient and no fixed noon/3 PM/5 PM observation window needs to be stored in advance.

Future analysis should derive arbitrary event windows from the complete journals instead of collecting only preselected windows.
