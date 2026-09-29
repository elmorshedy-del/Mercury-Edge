# Step 4G-B plan — MADIS OMO current 5-minute climate-state decoding

Status: **4G-B1 PASS; 4G-B2 PASS; semantic correction locked. Next: 4G-C empirical validation.**

Branch: `paper-rigour-v2`

PR: #5

Cross-cutting data contract: **all Step 4G work must comply with `docs/INFORMATION_VISIBILITY_ARCHITECTURE_TODO.md`.** Raw source records remain immutable and complete; MADIS parsing/decoding is a replaceable derivation so future public-visibility, market-reaction and alternative source models can be rebuilt without recollecting source data.

## Critical semantic correction — 2026-08-20

The earlier B2 plan assumed MADIS OMO air-temperature `T` was a raw 1-minute temperature that Mercury needed to combine into its own five-minute rolling average. That assumption is not supported by the authoritative ASOS documentation and would add a second averaging step that ASOS itself has already performed.

The current NWS **ASOS User's Guide** states that:

- the hygrothermometer first creates 1-minute average temperature observations;
- once each minute the ASOS ACU calculates the **running 5-minute average** from those 1-minute averages;
- provided at least **four** valid 1-minute values exist in the past five minutes, the five-minute average is computed;
- that five-minute average is rounded to the nearest whole degree Fahrenheit, with midpoint values rounded toward the numerically higher value (`+3.5 -> +4`, `-3.5 -> -3`);
- the resulting five-minute temperature is converted to Celsius and **reported in the OMO and METAR**;
- ASOS uses that running five-minute average once each minute to update hourly and daily maxima/minima.

MADIS documents its dataset as **1-minute ASOS / One Minute Observations (OMO)**, with air temperature variable `T` in Kelvin. Therefore the current architecture treats MADIS OMO `T` as the transmitted OMO current temperature — i.e. the ASOS five-minute climate temperature reported every minute — not as one of the hidden 1-minute inputs used to construct that average.

Authoritative references used for this correction:

- NWS ASOS User's Guide: `https://www.weather.gov/media/asos/aum-toc.pdf`
- MADIS 1-minute ASOS overview: `https://madis.ncep.noaa.gov/madis_OMO.shtml`
- MADIS 1-minute ASOS variable list: `https://madis.ncep.noaa.gov/sfc_OMO_variable_list.shtml`

This correction **simplifies** the information edge. Mercury does not need five consecutive MADIS OMO records to prove that a received OMO temperature state existed. It needs to decode one received OMO `T` back to the unique whole-°F ASOS state without guessing the MADIS Kelvin wire quantization.

## 4G-B1 — Versioned MADIS Kelvin source-encoding / inverse-lattice policy — PASS

Create a pure conversion policy between a **configured MADIS Kelvin encoding model** and candidate whole-°F ASOS five-minute states.

Requirements:

- No hard-coded assumption that the live MADIS product has a particular Kelvin decimal resolution.
- No hard-coded assumption about source rounding mode without an explicit versioned policy.
- A policy must specify at minimum:
  - Kelvin resolution/quantization step;
  - rounding rule;
  - Fahrenheit search bounds appropriate to ASOS air temperatures;
  - policy/model version.
- If source resolution/encoding policy is unknown, mapping fails closed as `UNVERIFIED_SOURCE_ENCODING`.
- Given an explicit policy, inverse mapping returns **all** whole-°F ASOS states that could encode to the supplied Kelvin value.
- A received OMO state is candidate-usable only when that inverse set is unique and the upstream MADIS observation passed the 4G-A research/QC contract.
- Ambiguous and off-policy Kelvin values are preserved with explicit non-tradable statuses; they are never silently rounded to Fahrenheit.
- The mapping layer remains `RESEARCH_ONLY` and cannot by itself create benchmark `HardClimateState`.

Acceptance tests:

- forward encoding and inverse candidate generation are deterministic;
- a unique synthetic lattice point returns exactly one candidate whole °F;
- a deliberately coarse policy can produce an ambiguous inverse set and remains non-tradable;
- an off-policy Kelvin value fails closed;
- unknown source encoding policy fails closed;
- mapping model/version changes alter derivation identity;
- no continuous K->F->round shortcut is used as settlement proof.

Initial verification before semantic correction: GitHub Actions run 366 — 140 Python tests + Node + Docker + Postgres PASS. The inverse-lattice architecture was retained but its forward source model was corrected in B2 verification below to include the documented whole-°F -> 0.1°C OMO encoding before Kelvin storage.

## 4G-B2 — Direct OMO five-minute climate-state research evidence — PASS

The obsolete "re-average five OMO records" design has been replaced with a direct, source-causal adapter:

`MADIS OMO T (K) -> explicit inverse-lattice policy -> unique whole-°F ASOS five-minute state -> RESEARCH_ONLY SettlementEvidence`

Implemented requirements:

- A unique mapped OMO temperature produces direct five-minute-current research evidence for that observation minute.
- **No second rolling average is applied** to MADIS OMO `T`.
- The forward inverse-lattice source model now follows the documented ASOS path: whole °F -> nearest 0.1°C OMO representation -> Kelvin -> configured/versioned MADIS storage representation.
- Example regression: canonical 88°F -> 31.1°C -> 304.25 K before any configured MADIS storage quantization.
- The OMO observation requires acceptable MADIS/QC status and verified operating temperature sensor status (`TSS=0`) for B2 research evidence.
- The mapped value remains `RESEARCH_ONLY`; it cannot alter benchmark hard state until the source-encoding policy and empirical behavior are validated and explicitly promoted.
- Mercury-known time is receipt/interpretation causal, never backdated to the physical observation timestamp.
- Raw MADIS source-record identity/hash where available, source clocks, parser version, mapping-policy version, mapping-model version and calendar version are preserved.
- Exact duplicate input is idempotent.
- Two different accepted values for the same station/observation minute are an explicit conflict; neither is emitted as usable research evidence.
- Missing OMO minutes do **not** invalidate a directly received state and never justify interpolation. A gap can only cause Mercury to miss a possible maximum.
- Late/out-of-order records can be interpreted, but become knowable only at actual Mercury receipt/interpretation time.
- Research daily-high lower bound is the monotonic maximum of valid received OMO five-minute states for the target LST climate day.
- Direct OMO research evidence is rejected by the benchmark hard-state accumulator because its trust remains `RESEARCH_ONLY`.
- No bucket-elimination or execution code changed.

Acceptance tests passed:

- one unique valid OMO mapping creates one research-only five-minute current evidence item;
- the evidence cannot change benchmark hard state;
- later lower OMO state cannot lower the research daily-high lower bound;
- missing minute cannot create/interpolate a state;
- late older observation uses late Mercury-known time, not old physical timestamp;
- same raw input + same versions is deterministic/idempotent;
- conflicting same-minute accepted values fail closed;
- non-operating/unverified TSS cannot produce B2 research state;
- station/climate-date remain explicit and isolated;
- direct OMO decoding applies no second rolling average;
- corrected OMO Fahrenheit/Celsius/Kelvin lattice is regression-tested.

Verification: GitHub Actions **run 399 (`32395889553`)** — **165 Python tests passed**, Python compile PASS, collector Docker PASS, Node PASS, all Postgres migrations PASS, immutable hard-information journal PASS, immutable hard-state timeline PASS, immutable Kalshi market journal PASS.

Branch commits implementing the semantic correction/direct B2 path include:

- `d43aa8dea3f5aa41535a0d3833219fa580d09989` — correct the locked B2 plan;
- `b3d91219c2116bfff7378fdebca13c644c90fb36` — correct MADIS OMO source semantics;
- `e4eaaaac831a55c3db93738488ffcc69ee8be45b` — direct OMO climate-state decoder/evidence;
- `261c246a716131b011191d676fadeaa7db9b7b32` — corrected/direct OMO regression suite.

## 4G-C — Empirical validation before any trust promotion

The remaining hard problem is no longer reconstructing a five-minute average. It is proving that Mercury has decoded the live MADIS OMO `T` representation correctly and that the live feed is causal/reliable enough for benchmark use.

Required validation:

- empirically determine the actual Kelvin quantization/rounding representation delivered by the chosen live MADIS/LDM path;
- compare decoded OMO five-minute states against precise hourly/SPECI T-groups when timestamps align;
- compare running OMO research maxima against valid six-hour ASOS maxima;
- compare completed-day maxima against DSM/CLI/settlement truth;
- quantify missing records, duplicates, conflicts, QC/TSS failures, reconnects, observation->MADIS/LDM->Mercury latency and any correction behavior;
- preserve cases where OMO exposes a max before ordinary-public METAR disclosure and measure the public-catch-up/market-repricing timeline through the information-visibility architecture.

Promotion remains a separate, versioned trust-policy decision. No validation result may retroactively rewrite what Mercury knew historically.

## Explicit non-goals

- No live LDM network client in B1/B2.
- No benchmark trust promotion.
- No bucket-elimination or execution changes.
- No guessed MADIS Kelvin precision.
- No historical archive timestamp substituted for contemporaneous live receipt time.
- No second rolling average over values that ASOS has already reported as OMO five-minute current temperature.
