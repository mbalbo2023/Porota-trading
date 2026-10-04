# Issue #465 — F-01 EXIT budget liveness

Workstream A: `WS-FIX-AUDIT-08-A`, branch `fix/issue465-exit-budget-20261004`.
Base: rejected, frozen #463 at `caf9bc94b4a1f436ad01a84a9e9e9e7a4a9e9423`.
Authority: #465, complete #464 report/comment, #458 A–N, #460 and #462 O–V.
WRITE_OWNER declaration: https://github.com/mbalbo2023/Porota-trading/issues/465#issuecomment-5981112240.
Narrow existing-test reconciliation: https://github.com/mbalbo2023/Porota-trading/issues/465#issuecomment-5981320592.

PAPER/SHADOW only, real_orders_sent=0, real routes NOT_CALLED. No provider,
production runtime, productive DB, PPI Watch, merge, deploy or Predeploy action
was used by this front. The integration owner alone resolves final candidate
SHA/tree, consolidated suite, artifact and the single final Predeploy.

## ERROR and original RED

Executed the actual #463 SQLite implementation, fixed clock, temporary private
sidecar, book limit 5, EXIT floor 5, global limit 15. All five OPENED book reads
were admitted, started and finished. EXIT was then denied:

```json
{"opened_allowed":[true,true,true,true,true],"exit_result":{"allowed":false,"reason":"PPI_BUDGET_EXHAUSTED","lease":null},"book_metrics":{"requested":6,"allowed":5,"used":5,"dropped":1},"provider_calls":0,"real_orders_sent":0}
```

Original RED `/workspace/issue465-evidence/front-a/initial-red.json` SHA256:
`977c44bed27ca5f219715846571e841ebd78bcca02e2127c4cf22ff3da62b106`.
The permanent reproduction is
`tests/test_issue465_budget_adversarial.py::test_auditor_exact_five_opened_cannot_steal_last_exit_capacity`.
It fails against #463's reserve semantics and requires all five exits to pass
after all five lower-priority attempts, without increasing any limit.

## RCA, choice and permanent guards

`acquire()` combined OPENED usage into EXIT usage, and exempted both ranks from
higher-priority reservations. Therefore scanner work discharged EXIT's floor.
Scanner also rejected a stale current after obtaining a fresh book; its skip
did not discharge the independent exit reader's obligation.

Considered A (strict floor), B (hierarchical buckets), C (coalescing) and B+C.
A prevents starvation but can duplicate useful books. C alone cannot reserve
capacity for a cache miss, another full identity, expiry or crash. The selected
combination is a strict indelegable EXIT floor, hierarchical promises and
bounded exact-identity single-flight/coalescing:

* `EXIT_CRITICAL > OPENED_CRITICAL > SCALPING_HOT > STRATEGY_HOT > WARM > DISCOVERY`.
* Usage is counted independently per priority; OPENED never subtracts EXIT
  reserve. Every lower request holds all remaining higher floors by endpoint
  **and** globally before wire admission, including every actual retry.
* Promises cannot be lowered within their established window by another
  process or configuration. Oversubscribed plans allocate by rank and protect
  EXIT book first; they expose demand that cannot be reserved, rather than
  manufacturing capacity or hiding the shortage.
* Only a higher claimant can borrow common/lower capacity; both incoming and
  donated units are reported. Lower work cannot borrow EXIT's minimum.
* Canonical EXIT only requests book, so automatic policy reserves EXIT book
  only and gives OPENED separate current/book/intraday floors. The single old
  policy assertion was reconciled with this actual caller contract; all other
  original guards are unchanged. Factual baseline 20/40 and cadences remain.
* Admission receipts, serial lease, breakers and counters survive restart.
  SQLite never remains locked during network reads or single-flight waiting.

Cache/single-flight resides in the existing private budget sidecar, never the
trading DB. Key is the hash of all five identity fields, never ticker alone.
Reuse is approved-only, at most one exit cadence and no more than 5 seconds,
requiring fresh native provider and receipt clocks and matching configuration
and recommendation fingerprints. Only bounded canonical public depth is
stored (64 identities, 16 KiB each, at most 20 levels per side); credentials,
account fields and arbitrary broker payload are discarded. Empty, malformed,
crossed, stale or future books are not cached. Follower waiting is bounded to
50 ms; a kill leaves a durable flight until its conservative lease expires.
Expiry, rollback, alias/quota/DB failure and breakers fail closed. A cache hit
retains provider time and does not spend or claim a new provider call.

## Real wiring

`bf_production_paper_observer.main` derives the exact opened identity when its
SDK request is unambiguous and invokes `_read_scanner_quote`; this uses the
existing SDK facade for current/book and leaves the original stale-data gate
in place. `bv_paper_runtime.collect_exit_books` already provides EXIT_CRITICAL
and the full position identity; its specialized book-only path is unchanged.
`ProductionMarketReader.book` coalesces only opened/exit reads with a matching
SDK request. All misses still use `_read()` and the actual HTTP transport guard
with `acquire/start/finish`. Unknown/ambiguous identity is not collapsed.

The native integration opens five reconciled PAPER positions through
`PaperBroker._open`, uses the real SDK on an explicit fake HTTP adapter, runs
the actual scanner helper and `collect_exit_books`, rejects TRADE_STALE while
BOOK is fresh, verifies exactly five provider book attempts for five positions
in both orders, and closes a PAPER position through the native participation,
liquidity, ledger and cash guards. No new entry or exit authority is granted.

## Metrics

Existing lifetime `global/by_endpoint/by_scope` remain compatible and reflect
wire admission receipts. `metrics()["window"]` adds endpoint/scope counters:
requested, admitted, used, dropped, reserved_total, reserved_remaining,
borrowed_in, borrowed_out, priority, consumer, denial_reason,
open_positions_count, exit_demand, plus coalesced reads and unreserved demand.
Admission protection uses exact rolling receipt timestamps. Counter telemetry
is explicitly aggregated at one-second resolution with reported start/end;
denial bursts have fixed scope cardinality. Older telemetry is pruned after
one hour, within the sidecar's existing disk/page/receipt quota.

## Twenty required adversarial cases

All symbols below refer to `tests/test_issue465_budget_adversarial.py` unless
an existing module is named.

| # | Requirement | Programmatic evidence |
|---|---|---|
| 1 | Exact auditor reproduction | `test_auditor_exact_five_opened_cannot_steal_last_exit_capacity` |
| 2 | Five simultaneous real positions | `test_five_real_positions_stale_scanner_current_fresh_exit_book_independent`, both orders |
| 3 | Scanner current stale + book fresh | Same native integration, TRADE_STALE with valid book |
| 4 | Specialized exit receives book after scanner skip | Same integration plus `test_exact_native_exit_caller_survives_all_five_opened_book_denials` |
| 5 | Scanner/exit/Scalping concurrent | `test_three_real_processes_exit_scanner_scalping_burst_cannot_steal_floor` |
| 6 | Actual multiprocess SQLite | Same test plus real-process single-flight |
| 7 | Reverse order | `test_reverse_order_burst_endpoint_and_global_remain_coherent`, both orders |
| 8 | Burst exceeds limit | Same 50-request burst and three-process 40-lower-request burst |
| 9 | Lower 429 | `test_429_preserves_semantics_and_durable_floor_across_restart[OPENED_CRITICAL-SCANNER]` |
| 10 | Exit 429 | Same test `[EXIT_CRITICAL-EXIT_READER]` |
| 11 | DB locked | `test_db_locked_is_bounded_and_no_wire_while_uncertain`, cache lock test |
| 12 | Abandoned lease | Original `test_rc6_ppi_global_budget.py` restart test, expired-start and process-kill tests |
| 13 | Scanner retry | `test_actual_http_scanner_retry_cannot_evade_exit_floor` |
| 14 | Expired policy | `test_expiry_between_admission_and_actual_send_is_fail_closed`, cache expiry test |
| 15 | Missing policy | `test_missing_policy_is_rejected_and_fresh_off_budget_is_original_baseline`, original fallback tests |
| 16 | Endpoint below global total | Exact reproduction, all-endpoint floors and tighter-global demand test |
| 17 | Same ticker, different full identity | `test_full_identity_native_book_cache_does_not_collapse_same_ticker` |
| 18 | No duplicate call when applicable | Native caller integration, native cache test and real-process single-flight |
| 19 | Factual OFF equivalence | `test_off_reader_repeats_original_wire_reads_without_coalescing`, original approved factual caller OFF/SHADOW tests |
| 20 | Approved reserve/PAPER authority | `test_approved_dynamic_reserves_real_durable_positions_without_paper_authority_change` |

Additional self-attacks cover all lower priorities on all endpoints, same-window
reserve reduction by another policy, upward borrowing visibility, cache errors,
TTL, native clocks, fingerprint change, mutation isolation, sensitive canaries,
cardinality, process kill, circuit/rollback/expiry cache bypass, DB contention
and native SDK HTTP429/401/403 behavior.

## Additional failures found and fixed

`start()` previously allowed an expired abandoned lease if no other acquisition
had observed it. Offline #463 accepted start at +61 seconds for a 60-second
lease (`used=1`). RED SHA256:
`4fab6476efbb46a5bed94ada32dfaa31d22db851d5b008e22361d9b56e1172ef`.
New start guards reject lease expiry, capacity expiry and an intervening circuit
before any send; regression retains the no-new-owner counterexample.

The real SDK strips HTTP status on errors and may refresh/retry internally on
401. Added public `last_read_error_code`: sanitized, thread-local, reset for
each native read; preserves the originating HTTP429/401/403 through that same
read's internal retry, while a subsequent read reports its current circuit
denial and a successful probe resets it. Front B consumes this public contract
without private guard introspection. No parser, provider transport, credential
or response-body logging is added. Three actual SDK fake-wire regressions
exercise reset, internal retry, global breaker and zero additional off-wire
sends.

## Local evidence and handoff boundary

Focused validation: **406 passed**, failures=errors=skipped=xfail=0,
`/workspace/issue465-evidence/front-a/final-focal.xml`, SHA256
`99d92d84005c1ca6248d1b52de528bb16dc3a587a9f5fef1bd3cbc66976ecab0`.
Includes all 44 new cases, original budget/approved callers, exit supervision,
exit ledger isolation, capacity promotion, intraday freshness, documented SDK
contract and production PAPER scenarios. Compile AST and diff whitespace pass.
Environment: pinned dependencies, local Python 3.12; final Actions Python 3.11
and consolidated exact artifact remain the integration owner's authority.

Reproduction command:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q tests/test_issue465_budget_adversarial.py tests/test_rc6_ppi_global_budget.py tests/test_rc6_approved_factual_callers.py tests/test_exit_supervision_v17.py tests/test_exit_ledger_isolation_v17.py tests/test_rc6_capacity_promotion.py tests/test_rc6_intraday_freshness.py tests/test_ppi_documented_contract_v17.py tests/test_production_paper_v1634.py
```

This proves floor safety under explicit finite capacity, rather than provider
availability: an already-emitted read cannot be preempted, a crashed worker
holds its conservative lease, and 429/session/locked state still fail closed.
Actual OPEN capacity/latency, account fees, executable live fills, OOS edge and
runtime of the new candidate remain external NO_VERIFICADO. No capacity,
score, TP/SL/EOD/MaxHold or position-limit promotion occurs here.

Front A code and all acquired hunks are released upon its frozen tested commit
handoff. DEPLOY_OWNER remains NOT_ACQUIRED. This front does not create a PR or
trigger its own gate; the final candidate must supersede #463 for reauditing.
