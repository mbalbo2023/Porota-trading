# RC6 convergence — budget and EXIT service

Scope: U01, U02, U05, U07 and the EXIT-capacity contract of AUD-468-04.
Seed: `0f810c168b0bfb5362148b804f3e15ffd1855610` (#466 plus preserved #470).
All verification is offline PAPER with ephemeral SQLite and fake SDK wire.
No production database, PPI account, real-order route or PPI Watch is used.

## Root causes and permanent guards

| Finding | Root cause | Corrected contract | Permanent regression |
|---|---|---|---|
| U01 | Arrival-order single-flight made EXIT followers expire at 50 ms behind an inferior owner. | EXIT waits to its monotonic critical cadence, joins the existing exact-identity flight, and records per-identity service. Waiting defers lower admissions. A missed deadline becomes durable DEGRADED pressure and suspends lower until fresh EXIT evidence. A token-checked alarm records an EXIT owner's deadline while its existing HTTP body remains in flight. No duplicate HTTP or pretend preemption. | Actual `ProductionMarketReader` plus `collect_exit_books`, lower 120 ms, three windows and one wire/cycle; frozen source clock with monotonic timeout; restart, recovered fresh cache, actual two-process coalescing, and alarm during an EXIT-owned HTTP body. |
| U02 | Clock/telemetry writes preceded pruning; 3,601 growing JSON seconds filled SQL pages and prevented housekeeping. | Every write transaction prunes before growth. Non-binding telemetry has a quota-derived byte bound and explicit retained-window completeness. Exact receipts, policy envelopes, circuits and leases are preserved. Page count reserves the DELETE-journal header/page receipts in addition to database bytes. | Six scopes/s for 120 seconds at 64 KiB and 3,100 seconds at 8 MiB; genuine legacy SQLITE_FULL at both quotas; restart, live wire debt retained, +4,000 s recovery, and measured DB plus auxiliary peak within quota. |
| U05 | Declared demand was truncated to a smaller reserve and activation remained approved. | Integer-exact demand is checked against every endpoint and the global cap before a dynamic policy or sidecar can be created. Impossible capacity returns `ACTIVATION_BLOCKED_EXIT_CAPACITY` / `PPI_EXIT_CAPACITY_INSUFFICIENT`. No cap is increased. | Actual durable ledger with 30 exits/cap 15, 30/global 10, and 10 positions/cap 5 across three windows: blocked before wire and before sidecar creation. |
| U07 | Both spot and futures tables were queried for OPEN; futures are ACTIVE. | A common verified PAPER snapshot counts spot OPEN and future ACTIVE. Unknown ledger is unknown, never zero. | Five real PAPER spot positions and five independently supported DLR ACTIVE lifecycles; discovery denied against the combined EXIT floor, restart retains count/debt, and insufficient capacity blocks. |
| AUD-468-04 | Baseline demand limits cannot establish complete EXIT service; dynamic recommendations were insufficient proof. | The dynamic envelope must satisfy actual family-complete EXIT demand before lower HOT/WARM/discovery traffic. Catalog/discovery remains owned by the orchestrator; this change does not return to an old batch or claim measured OPEN capability. | U05/U07 capacity and caller tests plus preserved native approved-caller and multiprocess stress suites. Final closure also depends on the root-owned promotion/catalog integration. |

## Shared APIs

- `supervisable_position_count(database, unverified=None)` opens one verified,
  query-only PAPER snapshot. Canonical states are exported as
  `SUPERVISABLE_POSITION_STATES`: `paper_positions/OPEN`,
  `paper_future_positions/ACTIVE`.
- `exit_capacity_contract(state, opened_count=...)` returns demand, endpoint/global
  gaps, deadline and READY or ACTIVATION_BLOCKED_EXIT_CAPACITY. `budget_policy`
  and native policy validation reject impossible declared demand.
- `runtime_budget_snapshot(database, as_of=...)` reads the canonical existing
  sidecar with no bootstrap, mkdir, clock update, pruning, chmod or SQL writes.
  It reports ABSENT, OBSERVED, DEGRADED or UNVERIFIED; global counters,
  `exit_service`, and `telemetry_retention` are fixed sanitized projections.
  Missing data stays unknown. WAL header/auxiliaries and aliases are rejected
  before a SQLite read can create SHM. Source-tree inventories are unchanged
  under normal, missing, corrupted, aliased, WAL and contested-source tests.

Normal WAITING defers lower work and remains OBSERVED. It does not declare a
missed deadline. DEGRADED requires its durable deadline marker. The current
worker also exposes `deadline_alarm_unavailable` if the alarm cannot write;
uncertain IO never releases a wire lease or creates another sender.

`GlobalPPIBudget.metrics()` preserves existing exact rolling receipt/reservation
accounting and adds identity-digest service evidence. Telemetry truncation can
reduce non-binding window counters; `telemetry_retention.window_complete=false`
states that explicitly. Lifetime requested/allowed/used/dropped totals and
actual rolling wire debt remain authoritative and survive recovery.

## Replaced incompatible tests

The previous tests accepting five demanded exits under global cap three,
30 exits under endpoint cap five, or five durable positions under book cap 15
were replaced with rejection assertions. The killed-flight follower now expects
the critical EXIT deadline code, with the durable flight retained until expiry.
Shared recommendation/controller fixtures were not modified.

## Verification and remaining factual limits

The final local receipt is `/tmp/rc6-budget-verified.xml`, generated by:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
/workspace/venv_rc6/bin/python -m pytest -q -p no:cacheprovider \
  tests/test_rc6_ppi_global_budget.py \
  tests/test_issue465_budget_adversarial.py \
  tests/test_rc6_convergence_budget_liveness.py \
  tests/test_rc6_no_budget_permission_probe.py \
  tests/test_rc6_approved_factual_callers.py \
  tests/test_issue465_stress.py \
  --basetemp=/tmp/rc6-budget-verified \
  --junitxml=/tmp/rc6-budget-verified.xml
```

Result: **211 passed, 0 failed, 0 errors, 0 skipped**, 108.904 seconds.
JUnit SHA-256:
`c398b98236be7ef8dabc4bbf48b530bc97aa8da0abbb2273cbcb9431bdbecf8a`.
The final read-only helper exception boundary was then rechecked with the
observation-specific suite; no broader behavior changed after this receipt.

The integrator must rerun these tests on the single final candidate and artifact.
Fake-wire timing does not certify current provider latency, account quota,
production position count or OPEN capacity. An already-emitted synchronous HTTP
body remains owned by the transport; the deadline alarm suspends lower activity
and records degradation without claiming to cancel that body. Capacity approval
still requires factual evidence from the canonical promotion path. No deployment
authorization follows from this document.
