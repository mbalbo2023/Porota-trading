# CHECKPOINT — WS-DISK-08 HOUSEKEEPING SCHEDULER — 2026-10-01

## Scope
- WORKSTREAM_ID: WS-DISK-08-HOUSEKEEPING-SCHEDULER
- branch: ops/ws-disk-08-housekeeping-scheduler-20261001
- target: main (default branch scheduler control plane)
- product baseline SHA: 5912d02bd8bdf9f0acfbbeaaf0a605fe5b5462e6
- product ref: deploy/rc6-pr69-isolated-20260915
- scheduler only; no direct product runtime code in main

## Schedule
- daily 23:30 UTC / 20:30 ART: mode=auto
- Sunday 14:00 UTC / 11:00 ART: mode=cleanup
- manual: audit / auto / cleanup

## Safety
- concurrency.group = rc6-unified-paper-deploy
- cancel-in-progress = false
- exact checkout of canonical product ref
- deployed script SHA256 must equal product checkout SHA256
- deployed policy SHA256 must equal product checkout SHA256
- PRODUCTION_PAPER required
- REAL_ORDERS_SENT=0 required
- CASH_SWEEP_ORDER_ROUTING_ALLOWED=False remains an RC6 invariant
- housekeeping script is fail-closed
- PPI Watch unchanged by the housekeeping engine
- JSON evidence uploaded each run

## Activation gate
Do not merge/activate until WS-DISK-07 / PR #409 is deployed and the exact housekeeping script and policy exist on the Droplet.
