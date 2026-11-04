# A652 — Keep PAPER exits observable when scanner trade signal is stale

**Date:** 04-Nov-2026 ART. **State:** `DESARROLLADO` locally; no production merge,
Predeploy, Deploy, or runtime proof. **Owner:** `A652_PAPER_EXIT_GATING`.

## Exact scope and evidence

- Isolated branch: `fix/a652-paper-exit-book-only-20261104` from PR #929
  HEAD `94c634f295089e066cec7d0c138e72e38af511f9`.
- Exclusive paths: `bf_production_paper_observer.py`,
  `tests/test_rc6_a652_scanner_exit_only_fresh_book.py`, this document.
- No broker implementation, PPI Watch, budget/sidecar, catalog, PIT, worker,
  deployed branch, data, credentials, host, or order-route modifications.
- Source evidence: [A652 READ_ONLY run 41908698382](https://github.com/mbalbo2023/Porota-trading/actions/runs/41908698382),
  artifact 13361632072; latest scanner cycle 04-Nov 16:59:28–16:59:57 ART
  (19:59:28–19:59:57 UTC), 20 selected, 3 useful, 7 `DATA_ERROR`,
  10 `DATA_REJECTED`. The artifact **does not contain per-rejection reason codes**.
  No causal attribution of these ten observations to the bug is allowed.

## ERROR → RCA → FIX → GUARD → TEST

**Code RCA, verified in the branch:** Before the fix, scanner `run()` rejected
`q.time_error(... require_trade=True)` and `continue`d **before** calling
`PaperBroker.on_quote`. However `Quote.time_error(require_trade=False)`
explicitly accepts a contemporaneous, non-future Book for exit with an old
or absent last trade. `PaperBroker.on_quote` supervises a matching SPOT open
position before signal decisions; FUTUROS performs its dedicated exit path
before potential entry. Therefore a Book-only quote was denied this scanner
exit-supervision opportunity even when identity, book timing and broker checks
were otherwise valid.

**Impact bound:** `bv_paper_runtime.py` independently launches `exit_reader`
and `run_clock`. The reader acquires Book for already open positions and the
clock supervises exits. Those paths can compensate for this scanner gate when
healthy. Their actual live health is **NO_VERIFICADO**; this code defect does
**not prove** that live PAPER exits were missed. A previously tested broker
direct exit is not end-to-end evidence for a scanner-driven exit.

**FIX (narrow):** One scanner-local helper now:

1. Keeps the exact original **fresh trade** condition for normal evaluation.
2. On trade rejection, independently verifies fresh receipt and Book timing.
3. If the Book is fresh, invokes the existing broker with
   `allow_new_openings=False`, `evaluate_when_blocked=False`, and an explicit
   rejection reason. Its first operation may supervise an *existing* PAPER
   position; it never evaluates an entry signal or opens a new one.
4. If the Book is stale, missing, future, or timestamp-invalid, it does **not**
   call the broker and records the original pre-signal rejection instead.
5. Preserves the existing `DATA_REJECTED` event and scanner `continue` path,
   so a Book-only observation is never counted as an ordinary useful
   trade-qualified scanner selection.

**GUARDS/TESTS:** Dedicated offline synthetic regressions exercise the actual
helper extracted from the exact production source AST, without importing
the scanner's network-capable SDK:

- SPOT position with fresh Book and no trade can produce a simulated SELL;
  exactly one buy/one sell in isolated PAPER SQLite, no real routes.
- Book-only SPOT with no position never evaluates signal or opens a position.
- FUTUROS active position with Book-only can close via specialized lifecycle;
  FUTUROS without a position never reaches entry strategy.
- Stale, future, missing Book and invalid reception timestamp never reach
  the broker; rejection reason and `DATA_REJECTED` are preserved.
- Fully fresh trade returns to unchanged normal scanner path.
- AST proves scanner `run()` calls the helper and `continue`s on rejection.

The test ledger and timestamps are synthetic and local, not PPI provider or
droplet evidence. No WebSocket, PPI Current/Book GET, leases, orders or host
operations occur. **PAPER readiness remains NO_VERIFICADO.**

## Release condition

Retain this PR in DRAFT stacked on #929 until exact HEAD tests and the unified
candidate review. Integrate only with the complete scope, same immutable
artifact, Predeploy V2, Deploy V2 and host validation when authorized. A
synthetic successful simulated exit is not permission to lift provider,
identity, contract, session or per-instrument admission gates. Do not touch
PPI Watch; preserve FIX-FORWARD and `real_orders_sent=0` as mandatory.
