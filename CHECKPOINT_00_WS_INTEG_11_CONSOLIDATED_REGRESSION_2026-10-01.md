# CHECKPOINT 00 — WS-INTEG-11 CONSOLIDATED REGRESSION — 2026-10-01

## Ownership
- WORKSTREAM_ID: WS-INTEG-11-CONSOLIDATED-REGRESSION
- mode: WRITE_OWNER
- branch: work/ws-integ-11-consolidated-regression-20261001
- base_ref: ops/ws-ops-preopen-closure-20260930
- base_sha: 220501ce3929897ed59264cd70c2ae216a98b450
- scope: integrated CI only; no product-code mutation intended.
- paths: dedicated workflow/checkpoints only.
- no deploy; no direct production write.
- PAPER/SHADOW ONLY; real_orders_sent=0; real routes NOT_CALLED.
- PPI Watch untouched; FIX-FORWARD ONLY.

## Goal
Run the already-integrated Dashboard, runtime/risk/IOL, history/candle, caución and Zero-Known-Error regressions together on one checkout before a final candidate is frozen.

## Gates
- static PAPER guards GREEN;
- cross-workstream focused suites GREEN;
- full pytest suite attempted in the same checkout;
- failures must be classified, not hidden or retried blindly;
- no runtime mutation.
