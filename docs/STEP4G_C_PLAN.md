# Step 4G-C plan — empirical MADIS OMO validation and promotion gate

Status: **4G-C1 PASS on run 415 (177 Python tests); 4G-C2 PASS on run 431 (186 Python tests + Node + Docker + Postgres including immutable transport-event regression). Next: 4G-C3 empirical sample run, blocked on actual MADIS access/data. No benchmark promotion.**

Branch: `paper-rigour-v2`

PR: #5

Parent plans:
- `docs/STEP4_CANONICAL_TODO.md` — authoritative Step 4 checklist.
- `docs/STEP4G_B_PLAN.md` — corrected direct-OMO decoding semantics.
- `docs/INFORMATION_VISIBILITY_ARCHITECTURE_TODO.md` — raw-first information/public/market/settlement architecture.

## Purpose

4G-C must answer a narrower question than trading strategy:

> Does the chosen MADIS live path deliver an OMO temperature representation that Mercury can decode causally and reliably into the same settlement-compatible whole-°F ASOS climate state used by authoritative comparison products?

No benchmark promotion is allowed merely because the decoder works on synthetic data. Empirical storage representation, causal live timing, failure modes and agreement with authoritative sources must all be measured first.

## Non-negotiable separation

Four different claims must never be collapsed:

1. **Archive compatibility** — historical MADIS values can be decoded under a candidate storage policy.
2. **Source-state agreement** — decoded OMO states agree with precise public ASOS evidence / later maxima.
3. **Live causality** — Mercury actually received the live record at the recorded time, without archive/future leakage.
4. **Benchmark promotion** — a separately versioned trust-policy decision made only after the evidence above is sufficient.

Passing 1 or 2 does not establish 3. Passing 1-3 does not automatically perform 4.

## External access fact to preserve

NOAA MADIS currently requires a MADIS data application/account for continuous real-time access. The official application offers real-time LDM access and archive/on-demand methods including FTP/OPeNDAP/Text/XML, and lists `1-minute ASOS` as a selectable dataset. Therefore Mercury must not pretend that an archive download is equivalent to a contemporaneous LDM receipt.

Official references:
- `https://madis.ncep.noaa.gov/data_application.shtml`
- `https://madis.ncep.noaa.gov/madis_OMO.shtml`
- `https://madis.ncep.noaa.gov/sfc_OMO_variable_list.shtml`
- `https://docs.unidata.ucar.edu/ldm/current/basics/configuring.html`

## 4G-C1 — Pure empirical validation model — PASS

Implemented as a source-neutral validation layer before any live transport is wired.

Primary files:
- `paper_collector/madis_validation.py`
- `paper_collector/test_madis_validation.py`
- `docs/STEP4G_C1_VERIFICATION.md`

Implemented objects:

- **Storage calibration sample**: exact observed MADIS Kelvin value + known canonical whole-°F comparison state + station/date/observation time + raw provenance.
- **Candidate policy evaluation**: explicit `MadisKelvinEncodingPolicy`, matched/mismatched/off-policy/ambiguous sample identities.
- **Aligned-current comparison**: decoded OMO state versus precise ASOS T-group only when station and physical observation minute align exactly; no arbitrary time tolerance in v1.
- **Window/daily max comparison**: decoded OMO maximum versus authoritative maximum with coverage status explicit.
- **Live-quality metrics**: missing expected observation minutes when the expectation set is supplied, exact duplicates, conflicting duplicates, QC/TSS rejects, clock skew and observation-to-receipt latency summaries.

Rules implemented:

- Candidate storage policies are supplied explicitly; the validator does not invent a resolution or rounding rule from one example.
- A storage policy is `IDENTIFIED` only if exactly one supplied policy remains compatible with all qualifying calibration samples. Multiple survivors are `AMBIGUOUS`; zero survivors are `REJECTED`.
- Precise aligned-current disagreement is a direct contradiction for that sample and is never averaged away.
- For a six-hour/daily window with complete usable OMO coverage, decoded OMO max must equal the authoritative max.
- For an incomplete window, decoded OMO max may be **below** the authoritative max because Mercury may have missed the peak; decoded OMO max **above** the authoritative max remains a contradiction requiring investigation.
- Missing OMO data never permits interpolation.
- Historical/archive samples are marked `archive_only` and carry no live-receipt claim.
- Validation outputs are research/audit derivations only and cannot change `HardClimateState` or trading trust.

### 4G-C1 acceptance tests — PASS

- unique compatible candidate -> `IDENTIFIED`;
- multiple compatible candidates -> `AMBIGUOUS`, no promotion;
- no candidate explains all samples -> `REJECTED`;
- exact-minute OMO/T-group agreement passes;
- exact-minute disagreement is preserved as contradiction;
- no hidden time-tolerance matching is allowed;
- complete maximum coverage requires equality with the authoritative max;
- incomplete coverage allows OMO max below, but not above, authoritative max;
- archive-only calibration never establishes live causality;
- capture-quality metrics preserve duplicate/conflict/gap/QC/TSS/clock-skew/latency information.

Verification: GitHub Actions **run 415 (`32398166021`)** on code-complete commit `1c213fb8cf6ffe5121c4c3ef4fb168cc564f1373` — **177 Python tests passed**, Python compile PASS, collector Docker PASS, Node PASS, full Postgres migrations PASS, SQL013 immutable hard-information journal PASS, SQL016 immutable hard-state timeline PASS, SQL017 immutable Kalshi market journal PASS.

## 4G-C2 — Live capture qualification contract — PASS

Primary files:
- `paper_collector/madis_transport.py`
- `paper_collector/test_madis_transport.py`
- `sql/018_source_transport_events.sql`
- `sql/tests/018_source_transport_events_test.sql`
- `docs/STEP4G_C2_VERIFICATION.md`

The transport boundary is now explicit without implementing or deploying an LDM client:

`exact received bytes -> immutable raw_source_journal capture -> RawSourceRecord -> MADIS parser`

Implemented rules:

- exact binary payload is required; semantic parsing cannot create the parser-facing `CapturedMadisRecord` until `insert_raw_capture()` has completed;
- identical bytes received at different receipt times remain distinct causal captures;
- `LIVE_LDM` and `ARCHIVE_IMPORT` are separate origins and source streams;
- live captures preserve product id, connection id, sequence key, reconnect generation, receipt wall-clock and monotonic clocks;
- archive imports cannot populate canonical `first_fetchable_at` as though an archive timestamp were contemporaneous availability; a supplied archive claim is retained only as metadata;
- parsed MADIS minutes inherit explicit `madis_data_origin` and `live_causal` metadata from the persisted capture;
- receipt time used by the parser remains the actual import/live receipt, never the physical observation time;
- transport continuity facts such as reconnects, sequence gaps and queue gaps have deterministic `SourceTransportEvent` objects;
- `source_transport_events` is append-only and DB-immutable, so later replay can distinguish complete source silence from a period where Mercury's transport coverage was incomplete.

### 4G-C2 acceptance tests — PASS

- live LDM envelope preserves exact bytes and receipt identity;
- archive import is structurally distinct and cannot masquerade as live fetchability;
- same bytes received later produce a distinct capture identity;
- non-bytes payload fails before journaling;
- persistence precedes parser-facing record creation;
- live parse uses the actual receipt clock and carries live-causal provenance;
- archive parse remains explicitly non-live;
- sequence-gap event identity is deterministic and interval-aware;
- invalid negative gap interval fails closed;
- real Postgres regression rejects UPDATE/DELETE on source transport events.

Verification: GitHub Actions **run 431 (`32398894745`)** on code-complete commit `f37f9816b1c0c148c47032f55dd33a439a96e8b1` — **186 Python tests passed**, Python compile PASS, collector Docker PASS, Node PASS, full Postgres migrations PASS, SQL013/016/017/018 immutable regressions PASS.

C2 deliberately does **not** create a live MADIS network connection or deploy anything. It qualifies the interface that a future receiver must satisfy.

## 4G-C3 — Empirical sample run — BLOCKED ON ACTUAL MADIS ACCESS/DATA

Collect a substantial multi-station sample spanning ordinary and edge cases. The sample should deliberately include:

- multiple temperature levels so storage-policy candidates can actually be distinguished;
- precise T-group alignments;
- valid six-hour-max windows;
- complete and incomplete OMO coverage windows;
- QC/TSS rejects;
- duplicate/conflicting/corrected observations if they occur naturally;
- reconnects/gaps;
- completed climate days with DSM/CLI/final settlement truth when those collectors exist.

Do not choose a promotion threshold before seeing the empirical distributions. Report the raw counts/rates/disagreements first. Any later threshold is a separately documented trust-policy decision, not retrofitted into the validator.

## 4G-C4 — Promotion decision

Promotion is explicitly **not part of the decoder**.

A future promotion document must state:

- exact live MADIS product/path being trusted;
- identified Kelvin storage policy and evidence supporting it;
- sample size / stations / dates;
- precise-current disagreement count;
- six-hour and daily-max disagreement counts split by complete/incomplete coverage;
- missing/duplicate/conflict/QC/TSS/reconnect rates;
- latency distribution using live receipt clocks;
- known limitations;
- chosen trust-policy version;
- rollback/fail-closed behavior.

Until that document exists and is approved, direct OMO evidence remains `RESEARCH_ONLY`.

## Explicit non-goals

- no new trading sleeve;
- no capital-timing optimization;
- no chart/dashboard work;
- no crowd model;
- no bucket-elimination changes;
- no execution changes;
- no guessed Kelvin precision;
- no historical archive timestamp treated as live availability;
- no automatic benchmark promotion.

## Documentation discipline

After every completed C substep:

1. run the full regression suite;
2. record exact test count, CI run and commit SHA in the Step 4 refactor documentation;
3. update `docs/STEP4_CANONICAL_TODO.md` only for items actually proven complete;
4. add a dedicated verification note when a substep materially changes architecture;
5. leave blocked/live-access-dependent items unchecked with the exact blocker stated.
