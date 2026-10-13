# WS-MOTOR-FUTURES-PAPER-16 — completion for #462

This continues the existing #453 workstream on `fix/rc6-futures-paper-dlr-20261003`,
from `d738db79a452c699b50aed00ac539e57e1cc3b04`. Product base remains
`da697c6e6c2274579f9e4a112fabc4327475dd35`. #461 is preserved frozen.
No independent audit, PR merge, deployment, SSH, production DB/host action,
provider call or PPI Watch change is part of this work.

## ERROR → RCA → FIX → GUARD → TEST → evidence

The unchanged input reproduces exactly four failures among twelve FUTUROS tests:
open/mark/EOD, DailyRisk, exit-contract restoration and reserve/variation/close/cash.
The first durable failure is `no such table: paper_future_positions`.
Canonical `init_schema` created only the lifecycle/event tables and omitted the
position/mark tables used by the executor. After repairing bootstrap, the latent
OPEN INSERT had 25 values for 24 columns. Both are product defects, not fixtures.

`rc6_paper_family_lifecycle.init_schema` now owns the entire atomic, idempotent
FCI/FUTUROS migration. `PaperStore.init_db` invokes it before children start.
`ensure_initialized` caches readiness; a runtime child verifies the parent's
version/table contract read-only. Hot reads/marks do not recreate schema, missing
schema cannot masquerade as an empty portfolio, and unique active-identity and
native-mark constraints serialize duplicate operations.

The existing blanket BINDING-policy rejection also made the original positive
engine cases impossible: equity sector-concentration defaults to BINDING. DLR
now implements its own binding underlying-concentration guard under the opening
lock, retaining the same configured cap. Other unvalidated BINDING learning,
AI or exact-cost paths remain fail-closed. No risk percentage is relaxed.

`PaperBroker._decide_future` is an explicit, versioned DLR PAPER signal over exact
native PPI observations. Generic equity `decide()` cannot authorize FUTUROS.
The signal remains a score rather than a calibrated probability. Admission
requires exact quote/contract identity, current primary PPI catalog evidence,
native fresh quote/book/trade, exact monthly DLR 2026 terms, and the existing
cash, exposure, daily/concurrent risk and supervisor guards.

OPEN captures a complete canonical contract snapshot, digest, native book clock,
fees and full-notional reserve atomically. Every subsequent mark/exit checks that
snapshot. Missing or changed current contract evidence cannot fabricate exit
terms or permit another entry. Reserve, variation and release are separate
durable cash events. Replayed native cuts cannot book a second variation.
Conflicting event/mark payloads, future/stale books and clock rollback are rejected.

The existing runtime clock calls `supervise_futures` for every active future,
independent of scanner selection. The existing exit reader fetches exact active
FUTUROS books and uses the opening contract for exits. EOD/expiry apply an explicit
PAPER book-mark variation before close; this is not a claim of official broker
settlement or broker margin. Close requires fresh executable full-quantity bid
depth; unsupported partial futures fills are never fabricated. Expiry uses the
exact contractual instant, rather than the beginning of its calendar day.

DailyRisk reconstructs as-of realized/unrealized P&L, collateral, cash, exposure
and mark clocks from durable events/marks. Future events do not leak into an
earlier cut. Missing/stale marks and overnight carry block admission; existing
soft/hard stop percentages and hard-loss latch remain authoritative. Equity now
includes active futures unrealized P&L exactly once: cash already includes paid
variation, and collateral is returned as an asset. Spot/FUTUROS share emergency
position counts, total exposure and concurrent stop-risk capacity under the same
opening lock. Closed losing futures cannot be offset by winning closures to
increase the risk allowance. Caucion principal and other currencies stay separate.

## Validation and immutable identity

The original twelve tests are retained and GREEN. Additional acceptance cases
live in `tests/test_rc6_future_programming_complete.py`. Automatic governed scope
remains repository root with only the existing duplicate-module exclusion from
`ops/policy/test-policy.yaml`; no tests are hidden, skipped or marked xfail.
The exact final HEAD/tree, focal/full results, canonical Predeploy V2 run,
artifact digest and build-once image/tar/config identities are attached to #453
after its frozen candidate is tested. Its WRITE_OWNER is released explicitly
in #446 and #453 only after that gate succeeds.

## Preserved boundaries

Only exact standard monthly DLR 2026 is admitted. Spreads, suffix variants,
unproved calendars/products, wrong market/currency/settlement and ambiguous
identity/contract remain closed. PAPER `CONSERVATIVE_NOTIONAL_RATE=1` is separate
from `NO_VERIFICADO` broker margin. No FUTUROS position enters the spot ledger.
No factual equity score, stop, TP, EOD, MaxHold or risk percentage is changed.
PAPER/SHADOW ONLY; PRODUCTION_PAPER/SIMULATION; real_orders_sent=0;
real routes NOT_CALLED; PPI Watch UNTOUCHED; DEPLOY_OWNER NOT_ACQUIRED.
OPEN provider performance and strategy edge/OOS remain external future evidence.
