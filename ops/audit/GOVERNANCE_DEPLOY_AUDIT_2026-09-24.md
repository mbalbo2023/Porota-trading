# Porota Trading — Governance / Deploy Audit Baseline — 2026-09-24

## Scope

Read-only audit of the successful production-PAPER baseline plus repository governance. No runtime, engine, broker, PPI/IOL, systemd or droplet mutation was performed by this audit.

## Frozen baseline

- Canonical deployed branch: `deploy/rc6-pr69-isolated-20260915`
- Deployed Git SHA: `30d0a6191fdb0166b14e700786bd3fba194216ea`
- Successful deploy run: `36008343899`
- Mode verified: `PRODUCTION_PAPER`
- `real_orders_sent=0`
- Observer and dashboard source verification: GREEN
- Exact image verification: GREEN
- Post-deploy storage cleanup: GREEN
- Available bytes after cleanup: `10306572288` (~10.3 GB decimal)
- Cleanup reclaimed in final pass: `0B` (nothing further safely reclaimable in that pass)

The successful run lasted roughly 22 minutes 48 seconds from workflow creation to completion. This remains too slow for the target deploy architecture.

## Repository scale / debt snapshot

- Branches: **589**
- Protected branches: **1** (`deploy/rc6-pr69-isolated-20260915`)
- `fix/*`: **179**
- `audit/*`: **62**
- `feature/*`: **42**
- `work/*`: **35**
- `diag/*`: **33**
- `ops/*`: **28**
- `hotfix/*`: **21**
- Workflow files: **170**
- RC6-named workflow files: **164**
- Workflow filenames containing `deploy`: **32**
- Audit workflow filenames: **7**
- Read-only workflow filenames: **9**

No bulk deletion is authorized by this report. Existing items must first be classified as KEEP / CONSOLIDATE / ARCHIVE / DELETE.

## Critical deployment findings

### P0 — artifact completeness

The incident involving `bl_candle_engine.py` proved that source tests can pass while a manually curated deploy package omits a runtime dependency.

The current successful emergency fix uses:

`git ls-files '*.py'`

which materially improves Python coverage, but the packaging command still contains a very large manually maintained list for other runtime files and duplicates. This is a transitional fix, not the target architecture.

Target: one complete immutable artifact, automatic manifest, hashes and dependency/import closure validation before the droplet is touched.

### P0 — test exactly what is deployed

Tests must run against the exact deployable image/artifact. Testing the full checkout and then building a different subset is forbidden in Deploy V2.

### P0 — fix-forward only

Rollback is forbidden. A bad candidate must fail before promotion when possible. If a defect is discovered after promotion, correction is a new forward candidate.

### P0 — unambiguous deployment state

A failed workflow must not leave an undefined mixture of new/old runtime components. Deploy V2 must report exactly one of:

- `SUCCESS`
- `FAILED_PREDEPLOY_NO_RUNTIME_CHANGE`
- `FAILED_FIX_FORWARD_REQUIRED`

### P0 — deployment failure learning

Every new failure class must create a permanent regression guard. Repeated known failure signatures are treated as CI architecture defects.

### P0 — accessible control plane

GitHub MCP and GitHub Actions remain the normal administration path. User terminal/SSH is BREAK-GLASS only.

### P0 — safe cleanup after every attempt

Safe Docker image/cache/buildkit/staging cleanup is mandatory after both success and failure. The system must report storage before/after and never touch active runtime data, DBs, volumes, bind mounts or PPI Watch.

## Current deployment inefficiencies to remove progressively

1. Very large deployment workflow combining build, deploy, repair, diagnostics, contract evidence, dashboard validation, storage cleanup and unrelated probes.
2. Repeated expensive image builds used as provenance checks.
3. Manual runtime file lists.
4. Verification after runtime mutation for defects that can be detected in CI.
5. Branch/workflow proliferation caused by one-off diagnostics and forward fixes.
6. Failure knowledge encoded in chats/patches rather than reusable regression guards.

## Progressive cleanup sequence

### Phase 1 — governance foundation
Status: IN PROGRESS.

- agent bootstrap
- machine-readable policy
- failure-learning contract
- current-state schema
- read-only repository audit

### Phase 2 — isolated pre-deploy gate
No production deployment. Build and inspect candidate in CI. Validate artifact completeness, dependency/import closure, runtime smoke and PAPER safety.

### Phase 3 — immutable build-once artifact
Build once in CI, produce digest/manifest, test exact artifact. Do not rebuild on the droplet.

### Phase 4 — controlled promotion
Production deploy consumes the pre-tested artifact. Promotion is serialized and produces the machine-readable current state.

### Phase 5 — repository cleanup
Only after usage/dependency inventory:
- KEEP
- CONSOLIDATE
- ARCHIVE
- DELETE

## Parallelization

Safe in parallel:
- branch/workflow inventory
- failure signature catalog
- dependency/import analysis
- secret-scan design
- artifact manifest design
- governance documentation

Serialized only:
- canonical deploy workflow modification
- promotion to protected deploy branch
- systemd/runtime changes
- Docker operations affecting shared runtime
- DB migrations
- any PPI Watch operation (out of scope)
