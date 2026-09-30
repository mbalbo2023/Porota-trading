# CHECKPOINT WS-DEPLOY-RCA-06 — 2026-09-30

## Ownership
- WORKSTREAM_ID: WS-DEPLOY-RCA-06
- Mode: WRITE_OWNER under active DEPLOY_OWNER held by this workstream.
- Branch: `work/ws-deploy-rca-06-disk-byma-preopen-20260930`
- Base/product SHA: `c466e7acbcc333be3239ce2f0de29195a2442981`.
- Scope: exact preopen RED causes from Deploy V2 run 36766030050 only.
- PAPER/SHADOW ONLY.
- real_orders_sent=0.
- Real routes: NONE / NOT_CALLED.
- PPI Watch: UNTOUCHED.
- FIX-FORWARD ONLY.

## Verified failed deployment evidence
Deploy V2 run `36766030050` reached:
- immutable artifact resolution/download/transfer: GREEN;
- contract reconciliation: GREEN;
- readiness total: 6,955;
- scalping bounded stabilization: GREEN;
- immediate zero-known-error runtime audit: GREEN;
- 16/16 audited dashboard routes HTTP 200;
- mode PRODUCTION_PAPER;
- real_orders_sent=0;
- real_routes=[];
- then `rc6_preopen.py --phase T_MINUS_45` returned RED.

The diagnostics guard from WS-DEPLOY-RCA-05 preserved the exact causes:
1. `disk`: free=2,133,151,744 bytes; blocking_minimum=2,147,483,648 bytes.
2. `byma_morning_watch`: `BYMA_WATCH_DEGRADED`, errors `OPEN_DATA:BYMA_NOT_STRUCTURED` and `OPEN_DATA:BYMA_EMPTY`.

All required timers, containers, observer DB/runtime contracts, workers and history quarantine were GREEN. IOL SOURCE_UNAVAILABLE remained scoped AMBER with fail-safe continuation; it was not a RED cause.

## RCA / fix / guards
### Disk
- RCA: preflight reclaimed to 4,313,956,352 bytes, then compressed image + bundle + expanded stage remained resident until the final success cleanup. Preopen therefore sampled transient deploy storage instead of post-install steady-state reserve.
- Fix: after bundle verification/install and Docker image load/tag, delete only the expanded stage plus the already-consumed image tar and bundle tar before host/runtime gates.
- Guard: assert >=2 GiB immediately after this bounded transient cleanup. Frozen candidate and manifests remain for provenance checks.

### BYMA
- RCA: the public panel collector depended on pagination selectors and settlement filters that are not required for authority capture. Current BYMA public API behavior exposes pagination metadata but may ignore page selectors; full capture must be requested in one bounded response.
- Fix: one read-only request per panel with `page_size=5000`, no settlement filter, 16 MB bounded response ceiling.
- Guard: when BYMA publishes `total_elements_count`, require exact equality with returned source records; empty or partial panels remain fail-closed.
- Runtime validation: Deploy V2 explicitly refreshes the canonical read-only BYMA morning pipeline with the exact deployed code before preopen, so an earlier DEGRADED snapshot cannot survive a scraper fix silently.

## Recovery
Require exact Predeploy V2 GREEN for the final branch head. Merge only by PR with a two-parent product merge. The following Deploy V2 run must prove both `RC6_TRANSIENT_ARTIFACT_CLEANUP=GREEN` and `RC6_BYMA_MORNING_REFRESH=GREEN`, then both preopen phases GREEN and the remaining stability/soak/final safety gates GREEN before declaring VALIDADO_RUNTIME.
