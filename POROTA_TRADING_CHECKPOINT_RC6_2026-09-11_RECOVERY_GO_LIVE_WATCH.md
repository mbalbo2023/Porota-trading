# POROTA TRADING RC6 — RECOVERY + GO-LIVE WATCH — 2026-09-11

## Purpose
Canonical incremental checkpoint after recovery from a saturated ChatGPT conversation. This file continues the existing checkpoint chain and must be read before resuming work from another chat.

## Branch / HEAD at recovery
- Branch: `fix/rc6-w10-sector-map-binding-20260910`
- Recovered branch HEAD before this checkpoint write: `1f29f0515403e8f2d3f18e15a48979f18595adbb`
- Previous canonical incremental checkpoint: `POROTA_TRADING_CHECKPOINT_RC6_2026-09-11_PARALLEL_CP2_CP5_BARRIER.md`

## Recovered state
### CP2
- Source + focused tests: GREEN.
- Keep frozen unless an integrated regression proves a real incompatibility.

### CP3 — Contract Evidence
- Root cause had been localized to materialization / normalized endpoint evidence, not login/auth.
- Additional diagnostic evidence recovered from the saturated chat showed the probe itself was misclassifying `blocked_nonread` by assuming DataFrame semantics.
- Runtime shape proof:
  - `blocked_nonread_type=list`
  - `blocked_nonread_len=0`
  - `blocked_nonread_bool=False`
  - representation `[]`
  - no earliest untrusted rows
- Interpretation: current evidence shows zero blocked-nonread rows; remaining work is to promote this into a formal fail-closed gate that accepts the demonstrated shape(s), fails on anomalies/unknown shapes, and does not invent evidence.

### CP4 — A3 historical FUTUROS / DLR
- Root cause localized.
- Deterministic fail-closed mapper already exists for demonstrated simple DLR contracts.
- Historical acquisition path still needs minimal wiring and focused proof.
- Unproven aliases/spreads/variants remain fail-closed.

### CP5
- Source + focused tests: GREEN.
- GDELT must remain `SHADOW_ONLY`.
- Keep frozen unless integrated regression proves a real incompatibility.

## Safety invariants — non-negotiable
- Runtime validation target remains `PRODUCTION_PAPER` unless a later canonical checkpoint explicitly changes it.
- Real-order capability must remain BLOCKED during this work.
- `real_orders_sent=0` must hold.
- Validation must not call real-order routes.
- Authenticated PPI evidence/scraping remains read-only and fail-closed.
- No contract/economic value may be inferred or fabricated when it is absent from explicit evidence.
- GDELT remains `SHADOW_ONLY`.

## Global state at recovery
- CP2: GREEN, frozen.
- CP3: YELLOW, narrow gate formalization/certification pending.
- CP4: YELLOW, DLR historical wiring + certification pending.
- CP5: GREEN, frozen.
- `GLOBAL_RC6=YELLOW`.
- `GO_18_OF_18=NO` until CP3 + CP4 + integrated barrier are certified.

## Execution strategy
Parallel tracks:
1. CP3: formalize the blocked/non-read evidence gate from the demonstrated runtime shape and keep it fail-closed.
2. CP4: wire deterministic DLR identity mapping into A3 historical acquisition with focused tests and fail-closed behavior for unproven variants.
3. CP2/CP5: no source churn; run integrated regression only after A/B are ready.
4. Barrier: integrated 18/18 validation + PAPER/0-order invariants.
5. Deploy: transactional preflight/deploy/postflight with rollback readiness only after barrier GREEN.
6. Data activation after successful postdeploy: historical ingestion/backfill + approved Contract Evidence/scraping/data acquisition jobs, then verify freshness/coverage.

## Autonomous monitoring authorization
The user explicitly authorized continuing the repository/code/config/workflow work needed to reach audited GO-LIVE, including final deployment actions once every required gate is GREEN, while preserving all safety invariants above.

A recurring condition-watch task was created at the platform minimum supported cadence: hourly. It is instructed to:
- resume from the latest canonical checkpoint;
- parallelize CP3/CP4 and preserve CP2/CP5;
- checkpoint major milestones;
- proceed through integrated barrier and audited deploy where available tooling permits;
- after successful postdeploy, activate and verify the approved ingestion/backfill/scraping/data jobs;
- notify only for major milestones, intervention-required blockers, or final completion;
- never claim an action executed when tooling could not execute it.

## Immediate next step
Implement/certify CP3 and CP4 in parallel, then run the integrated barrier. Do not declare 18/18 or deploy before concrete GREEN evidence exists for both tracks and the integrated suite.