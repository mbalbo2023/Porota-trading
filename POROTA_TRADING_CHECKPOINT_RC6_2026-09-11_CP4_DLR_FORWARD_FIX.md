# POROTA TRADING RC6 — CHECKPOINT CP4 DLR FORWARD FIX — 2026-09-11

Timestamp evidence cut: 2026-09-11T12:55Z
Branch: `fix/rc6-w10-sector-map-binding-20260910`
Validated candidate HEAD before this documentation commit: `73024733c22b12fa302bf98baf5fe4991b048b9b`
Policy: NO ROLLBACK. Failure -> exact signature -> RCA -> smallest forward fix -> affected validation + required regression.

## Safety invariants
- `MODE=PRODUCTION_PAPER`
- `REAL_ORDER_CAPABILITY=BLOCKED`
- `real_orders_sent=0`
- `REAL_ORDER_ROUTES=NOT_CALLED` in current-head integrated validation
- PPI evidence browser/read-only/fail-closed; no `/Operar`; no invented contract/economic values
- GDELT remains `SHADOW_ONLY`

## CP1-CP8 matrix
| CP | Status | Exact evidence / residual |
|---|---|---|
| CP1 | GREEN/YELLOW final-runtime | Current candidate full integrated suite: 1801 passed, 0 failed; PAPER safety and no real-order routes green. Must still be re-certified against final deployed HEAD/runtime. |
| CP2 | GREEN/YELLOW final-runtime | Source/focused evidence previously green; current full suite has no regression. Final PRODUCTION_PAPER ledger/dashboard live wiring proof remains mandatory. |
| CP3 | YELLOW BLOCKING | Trusted Contract Evidence path remains fail-closed. Prior RCA: non-read attempts are blocked; no permission to relax POST. Need authenticated read-only facts -> normalization/materialization -> freshness/scheduler proof. |
| CP4 | GREEN code/tests; YELLOW runtime/data | RCA closed in source: A3 historical acquisition did not consume existing deterministic DLR mapper. Forward fix now resolves only proven simple FUTUROS DLR, queries A3 source identity, persists canonical Porota identity + provenance; spreads/variants remain fail-closed. Current candidate full integrated suite GREEN. Runtime/backfill/persistence proof on Droplet still mandatory. |
| CP5 | GREEN/YELLOW final-runtime | GDELT remains SHADOW_ONLY; current integrated suite has no regression. Final runtime/UX proof remains mandatory. |
| CP6 | PENDING | Open only evidence-based residual gaps after CP1-CP5. No blind rework. |
| CP7 | PENDING | Requires final clean-tree 18/18 barrier including deploy/live wiring/render/routes/data/freshness/cross-wave/safety. Current 1801/1801 suite is necessary evidence but not sufficient for CP7. |
| CP8 | PENDING | Final audited deploy/postflight/readiness, timers/schedulers/next triggers and postdeploy data acquisition activation/verification only after CP7. |

## CP4 RCA and forward fix
Failure/gap signature: `ew_a3_history_rc6.py` required exact source symbol/family match and therefore did not consume the already-existing deterministic DLR identity mapper for canonical Porota symbols such as `DLR/OCT26` versus compact A3 identities such as `DLR102026`.

Forward fix:
1. Direct exact source identity still has priority.
2. Only `FUTUROS` may fall through to deterministic simple DLR mapping via the existing mapper.
3. Unsupported/ambiguous identities, options, spreads and variants remain `ALIGNMENT_UNVERIFIED` and make no historical request.
4. A3 request uses the proven A3 source symbol.
5. Stored history keeps the canonical Porota symbol.
6. Provenance metadata records `a3_source_symbol`, `porota_canonical_symbol`, and `identity_alignment`.
7. No live trading/order path changed.

Source forward-fix lineage:
- `b4093c71b19eefe5d437dc168ad959511961bd66` — wire deterministic DLR identity into A3 history.
- `73024733c22b12fa302bf98baf5fe4991b048b9b` — tests cover compact DLR mapping, canonical persistence/provenance, and fail-closed spread/variant behavior.

## Current-head validation evidence
- Workflow: `RC6 W10 Forward Fix Validation 2026-09-10`
- Run: `34601120408`
- Head SHA: `73024733c22b12fa302bf98baf5fe4991b048b9b`
- Conclusion: SUCCESS
- Job: `103268380923` (`w10-forward-fix`) SUCCESS
- `RC6_PAPER_SAFETY=GREEN`
- `W10_POLICY=BINDING`
- `W10_UNMAPPED_FAIL_CLOSED=GREEN`
- Causal regression: GREEN
- Full integrated suite: `1801 passed, 1 warning`, zero failures
- `INTEGRATED_TESTS=GREEN`
- `REAL_ORDER_ROUTES=NOT_CALLED`
- `W10_FORWARD_FIX=GREEN`

Parallel companion validation:
- Workflow: `RC6 Post-W10 Next Failure RCA 2026-09-10`
- Run: `34601120465`
- Same head SHA `73024733c22b12fa302bf98baf5fe4991b048b9b`
- Conclusion: SUCCESS

## Runtime / Droplet evidence
CP4 discovery run `34598850508`, job `103260956418`, located the actual runtime A3 history path and mapper under read-only inspection and introduced no runtime mutation/order route. Final CP4 closure still requires execution/proof of historical acquisition/backfill using the deterministic identity mapping and evidence that canonical persistence/provenance occurred on the audited Droplet while safety invariants remain intact.

CP3 remains independent and blocking. Do not weaken read-only policy to fabricate facts.

## Context / env assumptions that matter
- RC6 version identity remains `17.0.0-rc6`.
- `MODE=PRODUCTION_PAPER` and real-order capability blocked are required before every runtime/data proof.
- A3 historical acquisition is background/read-only and must run only under its existing safe runtime guards.
- Simple deterministic DLR mapping is the only newly permitted source identity translation; fuzzy/variant/spread identities are fail-closed.
- Generic GDELT execution remains disabled from trading decisions; GDELT is SHADOW_ONLY.

## Dependency graph / exact next actions
`CP3 trusted read-only materialization proof` || `CP4 Droplet historical/backfill/persistence proof` || `revalidate CP1/CP2/CP5 assumptions on materially changed candidate`
-> `CP6 only real residual gaps`
-> `CP7 clean-tree W1-W18 18/18 canonical barrier`
-> `final deploy`
-> `CP8 audited postflight + overnight/preopen scheduler/data readiness`.

GLOBAL_RC6=YELLOW
GO_18_OF_18=NO
READY_FOR_FINAL_DEPLOY=NO
