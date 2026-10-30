# A207 — PAPER admin reversal, accounting, governance and deployment status

Date: 2026-10-30 ART. Scope: isolated candidate #774, incident #773; product
branch and PPI Watch are out of scope. The previous #772 exact GO was consumed
by merge `9fa233001b17a2664eb2b38770a93d3f3dbd6c91` and failed Deploy V2
`41116021622` at `QUIESCENCE_OPEN_POSITION`, **before installation**.

## ERROR → RCA → FIX → GUARD → TEST → EVIDENCE

- **ERROR:** a live OPEN PAPER position rejects the deploy during classifier,
  after irreversible merge, although an auditable administrative exit may be
  available. The previous A205 batch recognized only intact spot EOD.
- **RCA:** zero-active-position admission was performed before any automated
  closure; pre-suspend fingerprint and quiescence both fail closed. Caución
  principal/fees and future margin/variation cannot be made flat by a status
  update without invalidating the economic ledger and historical reporting.
- **FIX developed in candidate:** bounded atomic A207 batch across three PAPER
  families. Spot cancellation uses intact EOD-only entry with no partial sale.
  Caución cancellation needs contract integrity, no booked maturity, immutable
  original allocation, an append-only cancellation receipt, and releases only
  modeled principal and upfront fee. Futures cancellation needs a verifiable
  MARGIN_RESERVED lifecycle with **no** daily variation, official settlement
  or terminal price; an append-only compensation event restores precisely
  margin+entry fee; no simulated closing price, income or PnL is invented.
  Historical as-of projections still show the original pre-cancellation state.
- **GUARD:** exact frozen exposure fingerprint (positions, fills, allocations,
  marks and lifecycle events), 32-per-family bound, explicit PAPER/0, stopped
  host receipt, owner comment/branch/tree binding, SQLite BEGIN IMMEDIATE,
  all-or-nothing rollback, zero SELL_SIMULATED synthesis, immutable batch
  journal, idempotent receipt-bound retry, fail closed on contradictory data.
- **TEST:** `tests/test_rc6_a207_paper_admin_reversal.py`; accounting,
  historical cuts, receipts, fee modes, future collateral, double-apply and
  forced failure in the last family. Existing A205 and family regressions must
  remain GREEN. CI and artifact SHA must be recorded per final candidate.
- **EVIDENCE:** GitHub #773, #732, #624, PR #774 and the exact final Actions
  URLs/SHA in the PR after CI finishes. A local PASS is not runtime validation.

## Decision table (administrative versus verified economic exit)

| Exposure | Eligible for A207 without market settlement? | If not eligible |
| --- | --- | --- |
| Spot EOD fully intact, no SELL_SIMULATED | Yes, administrative void | Reconcile fills, fees, receivables and actual exit |
| Spot partially sold | No, its prior realized economics must survive | Close remainder using verifiable simulated exit; no fake price |
| Caución PAPER OPEN, never settled | Yes, negate modeled principal/upfront fee; retain original allocation | A matured/settled contract needs auditable settlement |
| Future PAPER with only OPEN + MARGIN_RESERVED | Yes, compensating margin/entry fee cash event | Settlement marks/daily variation need official reconciliation |

## Deployment contract — strict status boundary

The A207 code is **not by itself an authorized deploy procedure**. The actual
Droplet may only execute the writer through the canonical GitHub Actions
control plane, with #624 exact owner comment for final candidate SHA/tree,
root mutex and attempt barrier, stopped owned containers, exact receipt,
snapshot replay, and post-commit flat/cash proofs. The workflow must integrate
that preparation before declaring the candidate eligible for final promotion.
The prior SUPV one-off authority is unchanged. A read-only probe that finds
`FLAT_GREEN` does not prove future quiescence or new runtime operation.

Unresolved until independently proven via Actions: full workflow wiring and
recovery, safe handling of partially sold spot, source-impossible settlement
cases, repeated run after lost SSH acknowledgement, host-stopped proof on the
real Droplet, complete predeploy suite, exact #624 authorization and
`VALIDADO_RUNTIME`. There is **no** permission to invent a closing fill,
silently rebook realized income, delete history or bypass PPI/0 protections.

## Continuity performance improvements — status (not implied by A207)

Implementations observed in the RC6 code path: immutable build-once digest
artifact, Buildx Actions cache, single deploy mutex, deterministic suite
counting, selective Docker housekeeping. Not independently completed in this
workstream: Python dependency cache, sharded pytest with result quorum,
per-phase timing/RCA receipts, and universal safe cleanup before suspension.
Those are separate infra improvements; they must be regression-tested and
integrated in one candidate, not deployed one-by-one.
