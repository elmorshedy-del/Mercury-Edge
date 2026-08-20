# Step 4G-C plan — empirical MADIS OMO validation and promotion gate

Status: **4G-C1 PASS on GitHub Actions run 415 (`32398166021`), 177 Python tests + Node + Docker + Postgres. Next: 4G-C2 live-capture qualification contract. 4G-C3 remains blocked on actual MADIS access/data.**

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

## 4G-C2 — Live capture qualification contract — NEXT

Define the concrete transport qualification needed for a future LDM receiver without enabling production transport.

Requirements:

- exact received product bytes must enter `raw_source_journal` before parsing;
- one immutable capture per actual receipt, preserving duplicates/revisions;
- preserve LDM product identity/sequence/arrival ordering where exposed;
- preserve wall-clock and monotonic Mercury receipt clocks;
- record parser completion separately;
- reconnects and queue gaps must be explicit audit intervals;
- no observation or source timestamp may substitute for Mercury receipt time;
- archive download/import code must write to a separate replay/import path or explicitly mark `archive_only`; it may never masquerade as live receipt.

C2 can be implemented without enabling Railway production transport. Deployment remains prohibited until the main Step 4 checklist permits it and the user explicitly approves.

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
