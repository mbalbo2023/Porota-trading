# CHECKPOINT 01 — WS-CAUCION-11 GREEN — 2026-10-01

## Evidence
- base integration SHA: 220501ce3929897ed59264cd70c2ae216a98b450
- PR: #395
- final validated run before this checkpoint: 36804438657 — GREEN
- regression job: GREEN; all caución tests including auto-recovery regression passed.
- runtime-readonly job: GREEN.
- PRODUCTION_PAPER; real_orders_sent=0; real routes NOT_CALLED; PPI Watch untouched.

## Runtime snapshot
- worker: HOLD
- code: EXACT_FEE_OR_VERSIONED_PAPER_TARIFF_MISSING
- offers=0; allocations=0; paper_cauciones=0
- catalog CAUCIONES: 10 STALE
- candidate_identity_v2 CAUCIONES: 10 STALE / can_simulate=0
- current Evidence v2 rows: 2, both old (PPI_AUTHENTICATED_WEB/XHR)
- observer: WAITING_MARKET / MARKET_CLOSED

## RCA / FIX / GUARD
1. CI secret name:
   - ERROR: workflow used secrets.SSH_KEY.
   - RCA: canonical repository secret is secrets.DO_SSH_KEY.
   - FIX: use DO_SSH_KEY.
2. Auto-placement chronology:
   - ERROR: a fresh in-window candidate reached CaucionPolicy with frozen_at=now, while policy requires frozen_at <= session_open_at.
   - RCA: runtime wiring froze policy at evaluation time instead of the internal PAPER window start.
   - FIX: freeze the PAPER sweep policy at sweep_start_at.
   - GUARD: test_off_market_empty_hold_does_not_prevent_later_fresh_paper_candidate proves empty HOLD can be followed by a fresh simulated placement path.
3. Probe schema:
   - ERROR: runtime probe queried candidate_identity_v2.last_checked_at.
   - FIX: use canonical checked_at.

## State
- code/test: EN_GITHUB + GREEN CI.
- runtime with live fresh caución evidence: NO_VERIFICADO until market session supplies current evidence.
- real-order capability remains BLOCKED.
