# POROTA TRADING RC6 — CHECKPOINT CP3/CP4 RUNTIME RCA — 2026-09-11

Evidence cut: 2026-09-11T14:22:05Z
Canonical branch: `fix/rc6-w10-sector-map-binding-20260910`
Evidence candidate HEAD before this checkpoint commit: `9323b944cdd7b02bfa9a213e3c83670042565b2d`
Audited deployed Droplet HEAD: `e47eeffcb94a9468ac7e7f610beee0869353a77e`
Recovery policy: STRICT NO-ROLLBACK. Failure -> exact signature -> RCA -> smallest evidence-based forward fix -> targeted validation + required regression.

## Safety invariants
- `VERSION=17.0.0-rc6`
- `MODE=PRODUCTION_PAPER`
- `REAL_ORDER_CAPABILITY=BLOCKED`
- `real_orders_sent=0`
- no real order routes
- PPI browser/Contract Evidence read-only and fail-closed; no `/Operar`
- no invented contract/economic values
- `GDELT=SHADOW_ONLY`

## CP1–CP8 matrix
| CP | Status | Exact current evidence / residual |
|---|---|---|
| CP1 | GREEN code/integrated; YELLOW final runtime | Last fully validated code candidate `73024733...` had integrated `1801 passed`, PAPER safety GREEN and no real-order routes. Current diagnostic-only candidate `9323b944...` has required W10/Post-W10 regressions active; final deployed runtime certification remains mandatory. |
| CP2 | GREEN source/regression; YELLOW final runtime | No current evidence of source regression. Final PRODUCTION_PAPER ledger/dashboard live-wiring proof still mandatory. |
| CP3 | YELLOW BLOCKING | Read-only runtime proof now separates safety from freshness. Authenticated historical capture/materialization exists, timer active/enabled, DB materialization GREEN, safety GREEN. Correct semantic gate proves 18 non-read POST attempts were BLOCKED (`MALFORMED_OR_READLIKE=0`), therefore enforcement GREEN. Current endpoint input is empty and DB freshness is stale. Focused RCA found session expiration followed by failed isolated reauth/browser error and one-hour auth backoff. |
| CP4 | GREEN code/tests; YELLOW runtime/data | Deterministic simple DLR source fix remains validated in source. Deployed Droplet has `a3_history_ingest_state_rc6` FUTURES rows=0 and COMPLETE=0, so runtime/backfill/canonical persistence is not proven. |
| CP5 | GREEN source/regression; YELLOW final runtime | `GDELT=SHADOW_ONLY`; final runtime/UX proof remains mandatory. |
| CP6 | OPEN evidence-based residuals | Residual tracks are now concrete: CP3 auth/freshness/materialization flow and CP4 FUTURES runtime/persistence coupling. No blind rework. |
| CP7 | PENDING | Requires CP1–CP6 closure and clean-tree canonical Waves 1–18 18/18 barrier across code/tests/integration/deploy/live wiring/render/routes/data/freshness/cross-wave/safety. |
| CP8 | PENDING | Final audited deploy/postflight and approved timers/schedulers/data jobs only after CP7. |

## CP3 read-only evidence
### Final readonly proof
Workflow `RC6 CP3 CP4 Final Readonly Proof 2026-09-11`
- run `34609390014` SUCCESS
- CE runtime job `103295770068` SUCCESS
- A3 identity job `103295770269` SUCCESS
- Droplet identity exact: `e47eeffcb94a9468ac7e7f610beee0869353a77e`
- mutations: NONE

CE runtime facts:
- timer active and enabled; oneshot service result success
- latest trusted stored capture before RCA: `contract_20260910T213256Z.json`
- stored capture auth: `AUTHENTICATED_TRUSTED_DEVICE`
- stored capture `real_orders_sent=0`
- capture routes=6, endpoints=0
- blocked non-read list=18; examples are POST telemetry/logger/refiner/Zendesk requests that were blocked
- observer CE tables: 7 non-empty tables, 4651 total rows
- `contract_evidence_v2_current` rows=519
- latest current/snapshot observation at evidence cut: `2026-09-09T20:06:20.770262+00:00`

### Corrected semantic gate
Forward diagnostic commit: `f801dc8a2fceefee90a2176b0b9e55a16d6698c6`
Workflow run `34609495272`, job `103296117506`: SUCCESS.
Exact gates:
- `CP3_NONREAD_ENFORCEMENT_GATE=GREEN|BLOCKED_COUNT=18|MALFORMED_OR_READLIKE=0`
- `CP3_REAL_ORDERS_GATE=GREEN|VALUE=0`
- `CP3_ENDPOINT_INPUT_GATE=YELLOW|COUNT=0`
- `CP3_CAPTURE_FRESHNESS_GATE=GREEN|AGE_HOURS=16.76`
- `PAPER_SAFETY_GATE=GREEN|MODE=PRODUCTION_PAPER|REAL_ORDERS=0`
- `CP3_DB_MATERIALIZATION_GATE=GREEN|ROWS=519`
- `CP3_DB_FRESHNESS_GATE=YELLOW|AGE_HOURS=42.23`

This closes the false RCA that treated blocked POST attempts as a safety violation. They prove fail-closed enforcement and remain blocked.

### Focused freshness/auth RCA
Diagnostic commit: `9323b944cdd7b02bfa9a213e3c83670042565b2d`
Workflow `RC6 CP3 Freshness Root Cause 2026-09-11`, run `34609676742`, job `103296720592`: SUCCESS, read-only, `MUTATIONS=NONE`.

Exact runtime signature:
- timer remained active/enabled and last-triggered normally.
- runtime state: `BLOCKED_BROWSER_ERROR`, blocked at `2026-09-11T13:31:30Z`, retry 3600 seconds.
- at `2026-09-11T13:30:53Z`, collector reported `BLOCKED_AUTH_SESSION_EXPIRED`, endpoints=0, routes=0, real_orders_sent=0.
- isolated reauth was attempted; diagnostic stage `OPEN_LOGIN`, page `https://cuenta.portfoliopersonal.com/login`, blocked request diagnostic `z.clarity.ms/collect`.
- service emitted `STATUS=AMARILLO_AUTH_BLOCKED`, `AUTH_STATUS=BLOCKED_BROWSER_ERROR`, `REAUTH_ATTEMPTED=YES`, `REAL_ORDERS_SENT=0`.
- subsequent 5-minute timer invocations correctly emitted `AMARILLO_AUTH_BACKOFF`, with browser not started and real orders 0.
- latest CE v2 STATIC run on 2026-09-10 produced records=0 / AMARILLO.
- materialized `contract_evidence_v2_current` remains at Sep 9 freshness across families.

CP3 RCA therefore is not scheduler inactivity. The blocking chain is:
`session expired -> isolated reauth attempted -> browser/login-stage error -> safe one-hour backoff -> no fresh endpoints -> no fresh importer materialization`.
Safety remained intact throughout.

## CP4 runtime evidence
Readonly proof run `34609390014`, job `103295770269`:
- `A3_FUTURES_STATE_ROWS=0`
- `A3_FUTURES_COMPLETE_ROWS=0`
- legacy alignment observability measured `2026-09-06T09:18:34Z`
- FUTUROS CEM=148, target=43, exact overlap=0
- legacy `execution_allowed=false`, `history_fetch_policy=DISABLED_UNTIL_VERIFIED_EXACT_IDENTITY_MAPPING`
- closing price requests=0

Corrected semantic gate run `34609495272`:
- `CP4_FUTURES_RUNTIME_PERSISTENCE_GATE=YELLOW|ROWS=0|COMPLETE=0`

Source forward fix already allows only deterministic proven simple DLR identity mapping; unsupported variants/spreads remain fail-closed. The deployed runtime predates that source fix, therefore CP4 cannot be falsely closed from source tests alone.

## Active validations at evidence cut
- Current diagnostic candidate `9323b944...` automatically queued required W10 integrated regression run `34609676804`; active at evidence cut.
- Post-W10 regression for the same candidate is also expected/active via push-triggered validation; do not infer GREEN until completion is observed.
- Earlier semantic candidate `f801dc8...` had its own regression executions; no production mutation was performed by the semantic/freshness RCA workflows.

## Material env/context assumptions
- canonical branch: `fix/rc6-w10-sector-map-binding-20260910`
- deployed Droplet HEAD deliberately distinguished from repository evidence candidate: `e47ee...` vs `9323b944...`
- observer image expected by CE runtime: `porota-trading-bot:17.0.0-rc6`
- CE timer poll: every 5 min; job cadence controlled by due policy
- auth failure state uses a 3600-second safe backoff
- current candidate-only CP4 DLR forward fix is not yet present in deployed runtime

## Exact next actions / dependency graph
Parallel tracks:
1. **CP3:** inspect/repair the isolated reauth browser path at the exact `OPEN_LOGIN/BLOCKED_BROWSER_ERROR` signature without weakening read-only/order safety; then obtain a fresh trusted capture with non-empty evidence inputs and prove importer updates DB freshness.
2. **CP4:** establish a graph-safe candidate runtime proof for deterministic DLR historical acquisition/backfill/persistence without final deploy before CP7; if no safe staging/one-shot mechanism exists, preserve this as an explicit deployment-coupling blocker rather than inventing evidence.
3. **CP1/CP2/CP5:** finish required regression on `9323b944...` and revalidate assumptions if any material code changes follow.
4. **CP6:** only the evidence-based residuals above plus anything newly demonstrated.

Then:
`CP3 + CP4 + CP1/CP2/CP5 -> CP6 closure -> CP7 clean-tree 18/18 -> final deploy -> CP8 audited postflight/readiness`.

GLOBAL_RC6=YELLOW
GO_18_OF_18=NO
READY_FOR_FINAL_DEPLOY=NO
