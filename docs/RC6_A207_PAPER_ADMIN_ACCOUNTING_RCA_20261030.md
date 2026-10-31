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
- **Adversarial hardening after A207 review:** a generic futures lifecycle
  call can no longer request `ADMIN_CANCEL` or credit arbitrary collateral;
  the future CLOSE uses a private capability and verifies both original OPEN
  and MARGIN_RESERVED event amounts/routes. Caución now validates the original
  placement event and the complete sealed allocation, not a spoofable JSON
  status string. Spot rejects *any* connected fixed-income seed, coupon,
  amortization or FI cash entry, both on cancellation and when reading a
  previously canceled lot; a separate FI economic policy is required.
- **FCI exposure guard:** unsettled FCI lifecycles (`SUBSCRIBE_REQUESTED`,
  `PENDING`, `NAV_APPLIED`, `SETTLED`, `REDEEM_REQUESTED`) are counted and
  classified as **unsupported NAV/cash reconciliation**, not silently
  returned as `FLAT_GREEN`. Host quiescence and pre-suspend fingerprint
  independently reject such obligations; `REDEEMED` is terminal.
- **Failure recovery hardening:** Deploy V2 selects an exclusive A207 recovery
  disposition even after it normalizes the successful promotion mode to
  OPEN/CLOSED. An unvalidated successor can never authorize generic timer
  restore merely because both containers are running/PAPER/0. If restored
  timers must be re-held after a later failure, exact receipt/PAPER/0 proof,
  a durable `A207_REHOLDING` intent, and individual versioned stops precede
  the final all-timers-held receipt. A crash between `admin-begin` and
  `admin-commit` uses an exact frozen staged source manifest, owner/tree
  receipt and old Docker clean stop to replay **only** the idempotent PAPER
  accounting via `admin-resume`; the failed Deploy remains consumed and no
  prior image is restarted. A missing or changed artifact fails closed.
- **Successor adoption guard:** a later candidate cannot adopt the stopped
  containers of a failed A207 attempt from a flat-book observation alone. It
  must independently read the previous reversal journal, verify the exact
  candidate/tree/owner/exposure seal and every three-family cancellation
  receipt, and prove PAPER/0 and all family books flat. A half-written or
  missing batch remains `NO_GO`; the prior image is never restarted.
- **Decimal cash correction:** future administrative neutrality compares
  `Decimal` cash values to numerical zero, not a string literal `"0"`.
  Exact zeros such as `0.00` remain valid; trailing precision is covered by
  regression tests and does not trigger an unnecessary A207 rollback.
- **Read-only consumers:** `/validacion` now accepts A205/A207 receipts only
  after canonical spot partition verification; historical A150 caución fee
  evidence recognizes genuine administrative reversals without inventing a
  maturity yield. The 20-session exporter retains v1 for unchanged cohorts;
  the explicit v2 publishes separately verified administrative voids in
  `voids.jsonl`, with no sale or PnL invented. Recompute independently rejects
  forged void records and missing digests. For the A207 authority the exporter
  additionally verifies membership in the SHA/tree/owner-sealed atomic batch
  journal and its canonical digest; it explicitly marks complete companion
  family reconciliation `NO_VERIFICADO`. Original-source authentication
  remains a separate owner reconciliation, not an implicit CI assertion.
- **TEST:** `tests/test_rc6_a207_paper_admin_reversal.py`; accounting,
  historical cuts, receipts, fee modes, future collateral, double-apply and
  forced failure in the last family. Existing A205 and family regressions must
  remain GREEN. `tests/test_rc6_a207_host_quiescence_handoff.py` covers a
  real-shaped 3-family ledger, CLOSED/OPEN phases and interrupted SQL before/
  after commit; `tests/test_rc6_a207_scheduler_failure_hold.py` proves timer
  hold and re-hold without any `start`; preflight, FI sidecar, FCI, provenance
  `/validacion` and `tests/test_rc6_a207_sanitized_evidence_v2.py` regressions
  are separate. The host handoff suite also
  injects forged owner/tree, missing journal, incomplete SQL and reopened
  position on a successor adoption. CI and artifact SHA must be
  recorded per final candidate.
- **EVIDENCE:** GitHub #773, #732, #624, PR #774 and the exact final Actions
  URLs/SHA in the PR after CI finishes. A local PASS is not runtime validation.

## Decision table (administrative versus verified economic exit)

| Exposure | Eligible for A207 without market settlement? | If not eligible |
| --- | --- | --- |
| Spot EOD fully intact, no SELL_SIMULATED | Yes, administrative void | Reconcile fills, fees, receivables and actual exit |
| Spot partially sold | No, its prior realized economics must survive | Close remainder using verifiable simulated exit; no fake price |
| Caución PAPER OPEN, never settled | Yes, negate modeled principal/upfront fee; retain original allocation | A matured/settled contract needs auditable settlement |
| Future PAPER with only OPEN + MARGIN_RESERVED | Yes, compensating margin/entry fee cash event | Settlement marks/daily variation need official reconciliation |
| Bonos/letras/ON with FI economic sidecar | **No**: canceling only spot would orphan coupon/amortization accounting | Separate sealed FI reversal or verifiable settlement |
| FCI with NAV/subscription/redemption outstanding | **No**: economic value/settlement cannot be inferred | Independently reconcile actual FCI ledger and NAV |

## Deployment contract — strict status boundary

The A207 code is **not by itself an authorized deploy procedure**. The actual
Droplet may only execute the writer through the canonical GitHub Actions
control plane, with #624 exact owner comment for final candidate SHA/tree,
root mutex and attempt barrier, stopped owned containers, exact receipt,
snapshot replay, and post-commit flat/cash proofs. The workflow must integrate
that preparation before declaring the candidate eligible for final promotion.
The prior SUPV one-off authority is unchanged. A read-only probe that finds
`FLAT_GREEN` does not prove future quiescence or new runtime operation.

Unresolved until independently proven via Actions: complete new-SHA Predeploy,
fully exercised host recovery, safe handling of partially sold spot and plain
OPEN spot without EOD intent, FI sidecar reconciliation and fund NAV lifecycle,
safe handling of source-impossible settlement cases, exact #624 authorization
of this *new* SHA/tree and `VALIDADO_RUNTIME`. The historical exporter v2 is
locally developed but has no new-SHA CI or independent owner source attestation
yet. There is **no** permission to invent a closing fill,
silently rebook realized income, delete history or bypass PPI/0 protections.

## Continuity performance improvements — status (not implied by A207)

Implementations observed in the RC6 code path: immutable build-once digest
artifact, Buildx Actions cache, single deploy mutex, deterministic suite
counting, selective Docker housekeeping. Not independently completed in this
workstream: Python dependency cache, sharded pytest with result quorum,
per-phase timing/RCA receipts, and universal safe cleanup before suspension.
Those are separate infra improvements; they must be regression-tested and
integrated in one candidate, not deployed one-by-one.

## A209 — CI source-schema fixture drift (31-Oct-2026 UTC)

- **ERROR/EVIDENCE:** new-SHA Predeploy V2 [#41151470191](https://github.com/mbalbo2023/Porota-trading/actions/runs/41151470191) failed at `Fast host-transition incident replay`, before the governed suite/build/artifact gate. Its final test summary was RED as a downstream effect. There was **no** host promotion, Docker/runtime mutation or validated artifact from that run.
- **RCA:** the archived `test_observed_stopped_host_waiting_market_after_admin_cancel` fixture in `tests/test_rc6_cold2_apply_guard.py` constructed spot/caución/future tables but omitted `paper_family_lifecycle`. The new safety guard deliberately refuses missing FCI family schema (`QUIESCENCE_FUND_LEDGER_SCHEMA_UNVERIFIED`) rather than treating it as zero obligations; its old synthetic fixture was no longer a valid flat PAPER book.
- **FIX:** add the minimal `paper_family_lifecycle(family,state)` table, with no active FCI row, **only inside that synthetic database setup**. Do not weaken the production fail-closed schema guard. The existing OPEN-position negative case remains intentionally blocked.
- **GUARD/TEST:** execute the exact nine-suite `Fast host-transition incident replay` list locally before the next remote Predeploy attempt (all tests GREEN), in addition to the broader A207/FCI suite. Final new-SHA GitHub Actions remains required; previous failure cannot certify the revised tree.
