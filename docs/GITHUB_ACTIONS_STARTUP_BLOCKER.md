# GitHub Actions startup blocker — external verification dependency

Status: **ACTIVE EXTERNAL BLOCKER — repository implementation work may continue, but no affected substep may be marked PASS without a clean full gate**

Branch: `paper-rigour-v2`

PR: #5

Observed beginning: 2026-08-24 after the Step 4J-C/D commits.

## What changed

The repository's real `Paper Trader CI` workflow operated normally through Step 4J-B. The last fully green replay run was:

- workflow: `Paper Trader CI`
- workflow id: `336950848`
- run: **575** (`32765146488`)
- tested head: `9f8bd9c11d32875c4f19d0de5fc7485a1aec016f`
- result: success
- Python: **272 tests, 0 failures**
- compile/Docker/Node/Postgres: PASS

A later 4J-C run did start and exposed a legitimate execution-regression failure; that failure was corrected in subsequent commits. Afterward GitHub stopped launching normal jobs.

The failing GitHub-side object is consistently surfaced as:

- workflow/path: `BuildFailed`
- workflow id: `341498289`
- conclusion: `startup_failure`
- jobs: none / failure occurs before a repository job starts

## Isolation work already performed

This failure has been separated from the current workflow YAML as far as repository-side tests can establish:

1. Restored the exact last-known-green workflow definition. Startup failure persisted.
2. Replaced it temporarily with a minimal one-job smoke workflow. Startup failure persisted before the job began.
3. Deleted/recreated the workflow under a fresh filename/name. The same synthetic `BuildFailed` startup failure persisted.
4. Created a separate diagnostic branch outside PR #5 with a trivial workflow. The same startup failure persisted.
5. Confirmed the connected GitHub identity has repository admin permission.
6. Restored the hardening branch to the intended full `Paper Trader CI` gate and removed temporary diagnostic workflow files/probes.

Therefore this document does **not** attribute the failure to Step 4J application/test code or to any particular YAML line. The evidence currently supports a GitHub Actions workflow-registration/account/platform startup problem because execution stops before any job exists.

## Diagnostic commits

Temporary isolation commits included:

- `170a53b46d934095eabf72e9147cac9ca7bd887b` — temporary smoke gate
- `0dbb8dc14fe46383c58a57e0284d9693b0fc6f44` — remove smoke gate
- `8437cb0c4c4b5bbf87ea76255d2a6684b9f461d9` — restore full workflow
- `531f9f99e68e7b2e47bdaea0283958da1997428a` — isolate with last-known-green workflow
- `72d22a4762eedde5bffd1d504430e3d7b1f89117` — clear temporary registration probe
- `b8b467753eb018364f9bae2d796aba772ff9baf1` — register fresh hardening workflow
- `32305fb4a9c93a3690f3b25cead9331d92911657` — restore intended full hardening gate
- `bd7efc31e9963ebc366b70be73aab2f683025fff` — remove separate-branch reindex probe from hardening history

The diagnostic branch itself also reproduced `BuildFailed/startup_failure`, which is important because it rules out PR #5 as the sole trigger.

## Canonical engineering consequence

Until normal Actions startup is restored:

- continue only work that can be statically reviewed/documented under this explicit external-dependency exception;
- do not claim a new full-gate PASS;
- do not check 4J-C, 4J-D, Step 4J or the permanent historical-MADIS anti-leak regression as verified;
- do not merge PR #5;
- do not deploy Railway;
- do not enable real-money execution.

The branch must still pass the complete current `.github/workflows/paper-ci.yml` after the external blocker is cleared.

## Manual GitHub-side recovery if repository work is otherwise complete

Because the connected tool surface does not expose the repository Actions enable/disable control, the likely safe reset path must be done in GitHub's UI by an administrator:

1. Repository **Settings** → **Actions** → **General**.
2. Temporarily disable Actions for the repository and save.
3. Re-enable the intended Actions policy and save.
4. Push/re-run the current hardening head.
5. If the synthetic `BuildFailed` registration still appears before any job starts, escalate to GitHub Support with the workflow/run ids in this document.

This is a verification-infrastructure reset only. It must not be used to bypass the required Step 4 full gate.
