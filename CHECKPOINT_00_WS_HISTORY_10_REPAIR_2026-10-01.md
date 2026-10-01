# CHECKPOINT 00 — WS-HISTORY-10 RESUME CUTOFF REPAIR — 2026-10-01

## Ownership
- WORKSTREAM_ID: WS-HISTORY-10-RESUME-CUTOFF-REPAIR
- mode: DEPLOY_OWNER for this bounded runtime-DB mutation only
- branch: work/ws-history-10-resume-cutoff-repair-20261001
- base_ref: ops/ws-ops-preopen-closure-20260930
- base_sha: 220501ce3929897ed59264cd70c2ae216a98b450
- scope: resume the existing rc6_history_cutoff_repair_once.py only; no code deployment.
- runtime mutation: canonical historical DB only through the already-developed resumable repair.
- no Docker rebuild/restart; no systemd changes; no cleanup; no product branch write.
- PAPER/SHADOW ONLY; real_orders_sent=0; real routes NOT_CALLED.
- PPI Watch untouched; FIX-FORWARD ONLY.

## Entry evidence
From WS-HISTORY-09 run 36804275137:
- active repair process: none;
- cutoff 2026-09-21: PARTIAL;
- targets=1,094; complete=899; failed=195; ppi_queries=726;
- canonical rows=439,833; latest=2026-09-28;
- READY with exact history=1,913; without=5,042.

## Execution guard
- global mutex: rc6-unified-paper-deploy / cancel-in-progress=false;
- block if market is not closed;
- block if another cutoff-repair process is already active;
- verify PRODUCTION_PAPER and real_orders_sent=0 before/after;
- preserve artifact/evidence even when repair exits nonzero or times out.
