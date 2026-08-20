# Step 4G-B plan — MADIS OMO current 5-minute climate-state decoding

Status: **LOCKED / CORRECTED FROM EARLIER ROLLING-RECONSTRUCTION ASSUMPTION**

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

Verification before semantic correction: GitHub Actions run 366 — 140 Python tests + Node + Docker + Postgres PASS. The mathematical inverse-lattice code remains useful; only its interpretation changes from "raw minute" to "OMO current five-minute state."

## 4G-B2 — Direct OMO five-minute climate-state research evidence

Replace the obsolete "re-average five OMO records" design with a direct, source-causal adapter:

`MADIS OMO T (K) -> explicit inverse-lattice policy -> unique whole-°F ASOS five-minute state -> RESEARCH_ONLY SettlementEvidence`

Requirements:

- A unique mapped OMO temperature may produce `MADIS_OMO_5M_CURRENT` research evidence for that observation minute.
- No second rolling average may be applied to MADIS OMO `T`.
- The OMO observation must have acceptable MADIS/QC status and verified operating temperature sensor status (`TSS=0`) for B2 research evidence.
- The mapped value remains `RESEARCH_ONLY`; it cannot alter benchmark hard state until the source-encoding policy and empirical behavior are validated and explicitly promoted.
- Mercury-known time is receipt/interpretation causal, never backdated to the physical observation timestamp.
- The exact raw MADIS source-record id, raw hash where available, source clocks, parser version, mapping-policy version and mapping-model version must be preserved.
- Exact duplicate derivations are idempotent.
- Two different accepted values for the same station/observation minute are an explicit conflict; neither may silently overwrite the other.
- Missing OMO minutes do **not** invalidate a received state or justify interpolation. They can only make Mercury miss a possible maximum. A gap can reduce sensitivity, never fabricate a higher bound.
- Late/out-of-order records may still be interpreted, but their information cannot be backdated: they become knowable only at their actual Mercury receipt/interpretation time.
- Reconnect/sequence gaps remain explicit provenance/audit information. They do not erase a directly received valid state, but they prevent claims that Mercury observed every intervening state.
- Research daily-high lower bound is simply the monotonic maximum of valid received OMO five-minute states for the target LST climate day.

Acceptance tests:

- one unique valid OMO mapping creates one research-only five-minute current evidence item;
- the evidence cannot change benchmark hard state;
- a later lower OMO state cannot lower the research daily-high lower bound;
- a missing minute cannot create/interpolate a state;
- a late older observation uses the late Mercury-known time, not the old physical timestamp;
- same raw input + same versions is deterministic/idempotent;
- conflicting same-minute accepted values fail closed;
- non-operating/unverified TSS cannot produce B2 research state;
- station/climate-date mismatch cannot contaminate another event;
- direct OMO decoding requires no changes to bucket elimination/execution.

Full Python/Node/Docker/Postgres CI must pass before B2 is documented complete.

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
