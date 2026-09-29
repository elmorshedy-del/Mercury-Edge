# GitHub Actions startup blocker — resolved transient platform incident

Status: **RESOLVED — normal repository jobs resumed and the complete hardening gate passed**

Branch: `paper-rigour-v2`

PR: #5

Observed beginning: 2026-08-24 after the Step 4J-C/D commits.

Resolved by evidence: **Paper Trader CI run 595 (`32803316338`)** on hardening head `5061d43a43c964ea878165be78e6d0cf59134033`.

## What happened

The repository's real `Paper Trader CI` workflow operated normally through Step 4J-B. The last green replay run before the incident was run 575 (`32765146488`).

After later 4J-C changes, GitHub began surfacing a synthetic workflow object rather than starting repository jobs:

- path/name: `BuildFailed`
- workflow id: `341498289`
- conclusion: `startup_failure`
- jobs: none; failure occurred before any repository job started

This was isolated from the Mercury code and normal workflow definition as far as repository-side experiments could establish.

## Isolation work performed

1. Restored the exact last-known-green workflow definition. Startup failure persisted.
2. Replaced it temporarily with a minimal one-job smoke workflow. Startup failure persisted before the job began.
3. Deleted/recreated the workflow under a fresh filename/name. The same synthetic startup failure persisted.
4. Reproduced the failure on a separate diagnostic branch outside PR #5.
5. Confirmed the connected GitHub identity had repository admin permission.
6. Restored the intended full `Paper Trader CI` workflow and removed temporary diagnostic probes.

Because a trivial workflow on an unrelated branch failed before job creation, the incident was treated as external verification infrastructure rather than evidence against Step 4J application logic.

## Resolution evidence

GitHub subsequently resumed normal Actions execution without a Mercury code workaround.

Paper Trader CI **run 595 (`32803316338`)** completed successfully on `5061d43a43c964ea878165be78e6d0cf59134033`:

- Python: **289 tests, 0 failures**
- Python compile: PASS
- collector Docker build: PASS
- Node checks: PASS
- fresh Postgres migrations: PASS
- immutable SQL regressions 013/016/017/018/019/020/021/022: PASS
- real-Postgres explainability regression: PASS
- deterministic replay 4J-D real-Postgres anti-leak regression: PASS

Therefore the startup incident is closed as a transient GitHub Actions/platform-registration failure. It does not waive any future CI requirement.

## Diagnostic history retained for future operators

Temporary isolation commits included:

- `170a53b46d934095eabf72e9147cac9ca7bd887b` — temporary smoke gate
- `0dbb8dc14fe46383c58a57e0284d9693b0fc6f44` — remove smoke gate
- `8437cb0c4c4b5bbf87ea76255d2a6684b9f461d9` — restore full workflow
- `531f9f99e68e7b2e47bdaea0283958da1997428a` — isolate with last-known-green workflow
- `72d22a4762eedde5bffd1d504430e3d7b1f89117` — clear temporary registration probe
- `b8b467753eb018364f9bae2d796aba772ff9baf1` — register fresh hardening workflow
- `32305fb4a9c93a3690f3b25cead9331d92911657` — restore intended full hardening gate
- `bd7efc31e9963ebc366b70be73aab2f683025fff` — remove separate-branch reindex probe from hardening history

## Permanent engineering rule

A future pre-job `BuildFailed/startup_failure` is not a test failure and is not a reason to bypass CI. Isolate it separately, retain the intended workflow, and require the next normal full gate to pass before marking a code substep verified.

No merge, deployment or real-money execution is authorized by this document.
