# RC6 #685: archived COLD guard at the closed PAPER stop

## Observed failure

Deploy V2 run 39701012110 promoted the exact predeploy candidate from run
39698103925, then stopped at `rc6_failed_cold_fix_forward.py:157` with
`COLD_ARCHIVED_RECOVERY_NOT_PROVEN`. It had installed the image and reconciled
the host control plane, but had **not** completed COLD2, contract V2 or runtime
acceptance. Failure cleanup restored 19 RC6 timers, retained the installed
candidate, recorded `FIX_FORWARD_VALIDATION_REQUIRED`, and reported zero real
orders and no PPI Watch access. The backup timer was `masked|inactive`.

Bounded host read run 39701578883 captured the predicates after failure:
archived failed input present, revoked cut absent, CURRENT on another snapshot,
`real_routes=NOT_CALLED`, `real_orders_sent=0`. At 00:40:56.246642 UTC the
observer wrote `FAIL_CLOSED`, `cold_audit` line 265, while its quiescence
receipt proves `CLEAN_EXIT` at 00:40:58.147470735 UTC (exit 0, no OOM). The
session was closed and flat: no market-open suspension, spot carryover,
caución or postclose pause. The COLD writer's `_frame()` raises the cancellation
at line 265 when a stop interrupts an 8 MiB sync boundary. The guard only
recognized the `stage()` cancellation at line 360 and only for the caución or
postclose paths. It therefore rejected this legitimate closed-session stop.

## Fix and limits

The guard recognizes line 265 solely for the closed, flat PAPER shape. It
still requires the exact `cold_audit` `ValueError`, `FAIL_CLOSED`, a clean
observer Docker stop, the failure timestamp inside that stop and at most 15
seconds before exit. The other archived-input, hash, inode, prior receipt,
CURRENT and real-order checks remain mandatory. The revoked old cut is never
rehabilitated. Unknown modes, altered time, other error lines and unproven
stops remain blocked.

The regression uses the actual receipt times and line from the host, along
with adverse mutations. The existing archive and COLD2 tests still cover the
postclose and intraday caución paths. This local correction is a candidate;
only a fresh Predeploy V2 and successful Deploy V2 with direct runtime proof
can close this incident. No host stress or provider sweep is authorized.
