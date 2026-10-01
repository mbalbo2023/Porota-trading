# CHECKPOINT 00 — WS-TESTS-15 GOVERNED ROOT RECONCILIATION — 2026-10-01

## Ownership
- WORKSTREAM_ID: WS-TESTS-15-GOVERNED-ROOT-RECONCILIATION
- mode: WRITE_OWNER
- branch: work/ws-tests-15-governed-root-reconcile-20261001
- base integration SHA: 9060d78ed83f0d30df19f7ac45353022125aa414
- scope: root-level legacy history tests + dedicated CI/checkpoint only.
- no product-code mutation.
- no runtime mutation; no deploy.
- PAPER/SHADOW ONLY; real_orders_sent=0; PPI Watch untouched.

## Triggering evidence
Predeploy V2 run 36805733754 on release candidate 3b22860e39a767347542f89446ca57c17cbe7b4d:
- discovered=2386; executed=2386;
- failures=4; errors=0;
- all four failures assert retired history capability OUT_OF_SCOPE_READONLY_LEGACY for families now included in OPERATIONAL_HISTORY_FAMILIES.
- product code explicitly returns READONLY_HISTORY_ALLOWED for those families and never returns READY_PAPER from history_collection_capability.

## Rule
Reconcile stale assertions only. Preserve:
- history collection != PAPER readiness;
- incomplete identities fail closed;
- aliases/retired families such as LICITACIONES remain OUT_OF_SCOPE_READONLY_LEGACY;
- Data912 fallback remains ACCIONES/CEDEARS only.
