# CHECKPOINT 00 — WS-TESTS-12 RECONCILE CURRENT CONTRACT — 2026-10-01

## Ownership
- WORKSTREAM_ID: WS-TESTS-12-RECONCILE-CURRENT-CONTRACT
- mode: WRITE_OWNER
- branch: work/ws-tests-12-reconcile-current-contract-20261001
- base_ref: ops/ws-ops-preopen-closure-20260930
- base_sha: 220501ce3929897ed59264cd70c2ae216a98b450
- scope: tests/workflow/checkpoint only; no production-code mutation.
- no deploy; no runtime mutation; no direct production write.
- PAPER/SHADOW ONLY; real_orders_sent=0; PPI Watch untouched.

## Triggering evidence
WS-INTEG-11 full suite run 36804101506 exposed three stale assertions while the cross-workstream focused suite was GREEN:
1. dashboard history test still required the retired text "cerrada y en cuarentena";
2. operational-scope test still expected BONOS to be OUT_OF_SCOPE even though OPERATIONAL_HISTORY_FAMILIES now includes BONOS and the other PAPER families;
3. table-layout test still required sticky headers although the current UX deliberately uses static headers to prevent overlap.

## Rule
Reconcile tests to the current explicit product contract only. Do not change product behavior merely to satisfy legacy assertions.

## GitHub publication
- PR: #398 (DRAFT) -> ops/ws-ops-preopen-closure-20260930
- Trigger evidence: this checkpoint commit occurs after the dedicated workflow exists.
- Status: EN_GITHUB; focal/full-suite CI pending.
