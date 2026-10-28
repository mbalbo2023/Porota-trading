# CHECKPOINT WS-DEPLOY-RCA-06 — 2026-09-30

## Ownership
- WORKSTREAM_ID: WS-DEPLOY-RCA-06
- Branch: `work/ws-deploy-rca-06-preopen-root-fixes-20260930`
- Base/product SHA: `c466e7acbcc333be3239ce2f0de29195a2442981`
- Scope: preopen disk/BYMA root fixes + guards.
- PAPER/SHADOW ONLY; real_orders_sent=0; real routes blocked.
- PPI Watch UNTOUCHED.
- FIX-FORWARD ONLY.

## Evidence
Deploy V2 run `36766030050` reached:
- immutable runtime promotion;
- full contract reconciliation GREEN;
- READY=6,955;
- dashboard 16/16 HTTP 200;
- scalping RUNNING after post-gate heartbeat;
- immediate Zero-Known-Error audit GREEN;
then failed in dry preopen with exactly:
- `disk`: free=2,133,151,744 bytes, blocking minimum=2,147,483,648 (short by 14,331,904 bytes);
- `byma_morning_watch`: DEGRADED, changed CALENDAR, errors only `OPEN_DATA:BYMA_NOT_STRUCTURED` and `OPEN_DATA:BYMA_EMPTY`.

## RCA
1. Disk: Deploy V2 checked preopen before deleting its own compressed image, bundle and extracted stage. The safety gate was measuring deploy-transient bytes as if they were steady-state runtime consumption.
2. BYMA: the watcher treated complementary structured/open-data capture errors as authority-page failure. Separately, the official BYMA calendar currently contains the 2026-11-09 exceptional date, which the audited local calendar did not yet contain.

## Fix
- reclaim only deploy-transient image/bundle/stage before the preopen disk gate;
- refresh BYMA morning evidence before dry preopen;
- keep official BYMA page failures fail-closed;
- scope `OPEN_DATA:*` errors as complementary rather than calendar authority failure;
- add 2026-11-09 to the conservative BYMA non-operational calendar and move audit date to 2026-09-30.

## Guards
- regression for 2 GiB reclaim ordering;
- regression for BYMA refresh ordering;
- regression that OPEN_DATA-only failure cannot mark authority DEGRADED;
- regression that official CALENDAR failure remains DEGRADED;
- calendar regression for 2026-11-09.
