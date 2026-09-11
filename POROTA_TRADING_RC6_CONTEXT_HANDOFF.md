# POROTA TRADING RC6 — CONTEXT HANDOFF

Updated evidence cut: 2026-09-11T12:57Z
Canonical branch: `fix/rc6-w10-sector-map-binding-20260910`
Branch HEAD immediately before this handoff commit: `9d3274711ce4feceefc71e17bd4251dea044d14b`
Latest code/test candidate validated: `73024733c22b12fa302bf98baf5fe4991b048b9b`
Latest milestone checkpoint: `POROTA_TRADING_CHECKPOINT_RC6_2026-09-11_CP4_DLR_FORWARD_FIX.md`

## Compact CP matrix
- CP1: GREEN code/integrated; YELLOW final runtime certification.
- CP2: GREEN source/regression; YELLOW final PAPER live wiring.
- CP3: YELLOW BLOCKING — authenticated trusted read-only CE capture/materialization/freshness/scheduler still required; do not relax blocked non-read requests.
- CP4: GREEN code/tests deterministic simple DLR wiring; YELLOW runtime/backfill/persistence proof.
- CP5: GREEN source/regression; YELLOW final runtime/UX; `GDELT=SHADOW_ONLY`.
- CP6: PENDING evidence-based residuals only.
- CP7: PENDING clean-tree 18/18 canonical barrier.
- CP8: PENDING final deploy/postflight + scheduler/data readiness.

## Safety/mode flags
`VERSION=17.0.0-rc6`
`MODE=PRODUCTION_PAPER`
`REAL_ORDER_CAPABILITY=BLOCKED`
`real_orders_sent=0` required at every runtime gate
`REAL_ORDER_ROUTES=NOT_CALLED` on latest integrated validation
`GDELT=SHADOW_ONLY`
PPI evidence: authenticated/read-only/fail-closed; no `/Operar`; no invented facts.
Recovery: STRICT NO-ROLLBACK; RCA -> smallest forward fix -> targeted + required regression.

## Latest validated evidence
- CP4 discovery: run `34598850508`, job `103260956418`.
- CP4 code forward fix lineage: `b4093c71b19eefe5d437dc168ad959511961bd66` -> `73024733c22b12fa302bf98baf5fe4991b048b9b`.
- Integrated validation on `73024733...`: run `34601120408`, job `103268380923`, SUCCESS, `1801 passed`, `INTEGRATED_TESTS=GREEN`, PAPER safety GREEN, W10 BINDING/fail-closed GREEN, `REAL_ORDER_ROUTES=NOT_CALLED`.
- Parallel Post-W10 RCA validation: run `34601120465`, same SHA, SUCCESS.

## Exact dependency path
`CP3 trusted CE proof` || `CP4 Droplet history/backfill/persistence proof` || `CP1/CP2/CP5 changed-candidate revalidation`
-> `CP6 residuals only`
-> `CP7 W1-W18 18/18`
-> `final deploy`
-> `CP8 audited postflight + approved data jobs/timers/schedulers/freshness`.

## Scheduler / runtime state
Final scheduler/timer/next-trigger certification is not yet claimed; it belongs to CP8 after CP7/deploy. CP3/CP4 live proofs must remain read-only and verify actual audited Droplet/runtime identity before claiming closure.

GLOBAL_RC6=YELLOW
GO_18_OF_18=NO
READY_FOR_FINAL_DEPLOY=NO
