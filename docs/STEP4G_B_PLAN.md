# Step 4G-B plan — MADIS minute -> candidate ASOS rolling-five-minute reconstruction

Status: **LOCKED BEFORE IMPLEMENTATION**

Branch: `paper-rigour-v2`

PR: #5

## Why 4G-B is split again

The official MADIS OMO contract gives Mercury temperature variable `T` in Kelvin, while the ASOS climate pipeline ultimately depends on one-minute whole-°F values and rolling five-minute averaging. The exact quantization/resolution of the MADIS-delivered Kelvin representation must not be guessed.

Therefore Step 4G-B is implemented in two independently verified pieces.

## 4G-B1 — Versioned source-encoding / inverse-lattice policy

Create a pure conversion policy between a **configured MADIS Kelvin encoding model** and candidate integer ASOS minute Fahrenheit states.

Requirements:

- No hard-coded assumption that the live MADIS product has a particular Kelvin decimal resolution.
- No hard-coded assumption about source rounding mode without an explicit versioned policy.
- A policy must specify at minimum:
  - Kelvin resolution/quantization step;
  - rounding rule;
  - Fahrenheit search bounds appropriate to ASOS air temperatures;
  - policy/model version.
- If source resolution/encoding policy is unknown, mapping fails closed as `UNVERIFIED_SOURCE_ENCODING`.
- Given an explicit policy, inverse mapping returns **all** integer Fahrenheit minute values that could encode to the supplied Kelvin value.
- A raw minute is eligible for rolling-five-minute reconstruction only when that inverse set is unique and the upstream MADIS minute itself passed the 4G-A research/QC contract.
- Ambiguous and off-policy Kelvin values are preserved with explicit non-tradable statuses; they are never silently rounded to Fahrenheit.
- The mapping layer remains `RESEARCH_ONLY` and cannot by itself create `HardClimateState`.

Acceptance tests:

- forward encoding and inverse candidate generation are deterministic;
- a unique synthetic lattice point returns exactly one candidate integer °F;
- a deliberately coarse policy can produce an ambiguous inverse set and remains non-tradable;
- an off-policy Kelvin value fails closed;
- unknown source encoding policy fails closed;
- mapping model/version changes alter derivation identity;
- no continuous K->F->round shortcut is used as settlement proof.

Full Python/Node/Docker/Postgres CI must pass before 4G-B2 begins.

## 4G-B2 — Rolling-five-minute research reconstruction

Only after B1 passes, build a pure rolling-window model over uniquely mapped candidate minute °F values.

Requirements:

- Require five consecutive observation minutes for one station and one LST climate date.
- No interpolation of missing minutes.
- Exact duplicate raw records are idempotent.
- Conflicting duplicate values for one observation minute fail closed.
- Late/out-of-order arrival must not backdate knowledge; conservative v1 will mark the affected reconstruction non-tradable rather than inventing an earlier live state.
- Require acceptable MADIS/QC state; conservative v1 requires verified operating temperature sensor status for reconstruction eligibility.
- Reconnect/sequence discontinuities remain explicit provenance and can invalidate a window if causal continuity cannot be established.
- The reconstructed five-minute value and candidate daily maximum are versioned research derivations.
- Reconstruction evidence is `MADIS_RECONSTRUCTED_5M` but remains `RESEARCH_ONLY` until empirical validation/promotion.
- Mercury-known time is the latest receipt/interpretation time among all inputs required for that result, never the physical ending-minute timestamp.
- Source record ids for all five inputs are preserved.

Synthetic acceptance cases include the NWS-documented style of five whole-°F minute values averaging to a whole-°F five-minute climate state, plus missing-minute, duplicate-conflict, out-of-order, QC/TSS and climate-boundary cases.

Full CI must pass before 4G-B is documented complete.

## Explicit non-goals

- No live LDM network client in 4G-B.
- No benchmark trust promotion.
- No bucket-elimination or execution changes.
- No claim that a guessed MADIS Kelvin precision matches the live feed.
- No historical archive timestamp may be substituted for contemporaneous live receipt time.
