# POROTA TRADING RC6 — CONTEXT HANDOFF

Updated evidence cut: 2026-09-11T14:22:05Z
Canonical branch: `fix/rc6-w10-sector-map-binding-20260910`
Latest evidence candidate before checkpoint/handoff docs: `9323b944cdd7b02bfa9a213e3c83670042565b2d`
Checkpoint commit: `498bd55ea51af662dbc0aaf7cca2da0805085b1c`
Audited deployed Droplet HEAD: `e47eeffcb94a9468ac7e7f610beee0869353a77e`
Latest fully validated code/test candidate before diagnostic-only commits: `73024733c22b12fa302bf98baf5fe4991b048b9b`
Latest milestone checkpoint: `POROTA_TRADING_CHECKPOINT_RC6_2026-09-11_CP3_CP4_RUNTIME_RCA.md`

## Compact CP matrix
- CP1: GREEN code/integrated; YELLOW final runtime. Required regression active on current diagnostic lineage.
- CP2: GREEN source/regression; YELLOW final PAPER ledger/dashboard live wiring.
- CP3: YELLOW BLOCKING — timer/safety/materialization GREEN; non-read enforcement GREEN; current endpoints empty and DB freshness stale because session expired and isolated reauth reached `BLOCKED_BROWSER_ERROR`, then safe auth backoff.
- CP4: GREEN code/tests deterministic simple DLR wiring; YELLOW runtime/data — deployed Droplet has FUTURES state rows=0 / COMPLETE=0.
- CP5: GREEN source/regression; YELLOW final runtime/UX; `GDELT=SHADOW_ONLY`.
- CP6: OPEN evidence-based residuals only: CP3 auth/freshness path + CP4 runtime/persistence coupling; no blind rework.
- CP7: PENDING clean-tree W1-W18 18/18 canonical barrier.
- CP8: PENDING final deploy/postflight + scheduler/data readiness.

## Safety/mode flags
`VERSION=17.0.0-rc6`
`MODE=PRODUCTION_PAPER`
`REAL_ORDER_CAPABILITY=BLOCKED`
`real_orders_sent=0`
no real order routes
`GDELT=SHADOW_ONLY`
PPI Contract Evidence: read-only/fail-closed; no `/Operar`; blocked non-read attempts remain blocked; no invented facts.
Recovery: STRICT NO-ROLLBACK; RCA -> smallest forward fix -> targeted + required regression.

## Latest runtime evidence
### CP3/CP4 readonly proof
- Workflow `RC6 CP3 CP4 Final Readonly Proof 2026-09-11`: run `34609390014` SUCCESS.
- CE job `103295770068`; A3 job `103295770269`.
- Exact audited host HEAD `e47eeff...`; mutations NONE.
- CE timer active+enabled, service result success.
- Stored trusted capture existed, `real_orders_sent=0`.
- 7 CE tables nonempty / 4651 total rows; `contract_evidence_v2_current`=519.
- DB max observed `2026-09-09T20:06:20.770262+00:00` (stale at proof time).
- A3 FUTURES state rows=0; COMPLETE=0.

### Corrected semantic gates
- Commit `f801dc8a2fceefee90a2176b0b9e55a16d6698c6`.
- Run `34609495272`, job `103296117506`: SUCCESS.
- non-read enforcement GREEN: 18 POST attempts blocked, malformed/read-like=0.
- PAPER safety GREEN, real orders=0.
- CP3 DB materialization GREEN, rows=519.
- CP3 endpoint input YELLOW count=0.
- CP3 DB freshness YELLOW age=42.23h.
- CP4 FUTURES persistence YELLOW rows=0 / COMPLETE=0.

### CP3 focused RCA
- Diagnostic commit `9323b944cdd7b02bfa9a213e3c83670042565b2d`.
- Workflow `RC6 CP3 Freshness Root Cause 2026-09-11`: run `34609676742`, job `103296720592`, SUCCESS, read-only.
- Runtime state: `BLOCKED_BROWSER_ERROR`, blocked at `2026-09-11T13:31:30Z`, retry 3600s.
- Collector first reported `BLOCKED_AUTH_SESSION_EXPIRED`, endpoints=0/routes=0/orders=0.
- isolated reauth attempted at login page; diagnostic blocked path `z.clarity.ms/collect`; resulting `AMARILLO_AUTH_BLOCKED` / `BLOCKED_BROWSER_ERROR`.
- subsequent timer runs safely report `AMARILLO_AUTH_BACKOFF`, browser not started, orders=0.
- causal chain: `session expired -> isolated reauth -> browser/login-stage error -> one-hour safe backoff -> no fresh endpoint capture -> no fresh importer materialization`.

## Source/code validation baseline
- CP4 source fix lineage: `b4093c71b19eefe5d437dc168ad959511961bd66` -> `73024733c22b12fa302bf98baf5fe4991b048b9b`.
- Integrated validation `34601120408`, job `103268380923`, SUCCESS: `1801 passed`, PAPER safety GREEN, W10 BINDING/fail-closed GREEN, `REAL_ORDER_ROUTES=NOT_CALLED`.
- Companion Post-W10 run `34601120465` SUCCESS.
- Current diagnostic lineage automatically triggers required W10/Post-W10 regression; do not inherit final GREEN until the latest executions complete.

## Active work / exact dependency path
Parallel:
1. CP3: repair/prove isolated reauth browser path at exact `OPEN_LOGIN/BLOCKED_BROWSER_ERROR` signature without weakening order/read-only safety; then fresh capture -> importer -> DB freshness proof.
2. CP4: obtain graph-safe runtime proof of deterministic DLR history/backfill/persistence on candidate code without falsely treating source tests as deployed evidence.
3. CP1/CP2/CP5: revalidate assumptions on every materially changed candidate.
4. CP6: only evidence-based residual gaps above.

Then:
`CP3 + CP4 + CP1/CP2/CP5 -> CP6 closure -> CP7 clean-tree W1-W18 18/18 -> final deploy -> CP8 audited postflight + approved data jobs/timers/schedulers/freshness`.

Final scheduler/timer/next-trigger certification remains CP8; current CP3 timer evidence is diagnostic/closure evidence, not a claim of final postdeploy readiness.

GLOBAL_RC6=YELLOW
GO_18_OF_18=NO
READY_FOR_FINAL_DEPLOY=NO
