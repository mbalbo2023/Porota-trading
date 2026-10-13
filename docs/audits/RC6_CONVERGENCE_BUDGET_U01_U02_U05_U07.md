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
| U05 | Declared demand was truncated to a smaller reserve. Cold BLOCKED/unknown readers then returned unleased permission; START did not revalidate the original scope. | Check exact endpoint/global demand and the 64-identity tracking limit before activation. A cold failed activation blocks every market send. A previously committed budget can retain EXIT under its exact caps/cadence/expiry/debt; LOWER never bypasses or relabels its original priority at START. | 30 exits/cap15, 30/global10, and ten positions/cap5 over three windows; real controller→SDK rejection; ledger/approval/expiry changed after acquire; exact token/used receipt preservation. |
| U07 | Both spot and futures tables were queried for OPEN; futures are ACTIVE. | A common verified PAPER snapshot counts spot OPEN and future ACTIVE, including pending expired contracts. Unknown ledger is unknown, never zero. | Five native PAPER spot positions plus five distinct synthetic DLR ACTIVE lifecycles. Two expired contracts remain supervised. Native reader visits all ten; real financial supervisor visits/closes all five futures; restart and insufficient-capacity guards preserve the conservative floor. |
| AUD-468-04 | Baseline demand limits cannot establish complete EXIT service; dynamic recommendations were insufficient proof. | The dynamic envelope must satisfy actual family-complete EXIT demand before lower HOT/WARM/discovery traffic. Catalog/discovery remains owned by the orchestrator; this change does not return to an old batch or claim measured OPEN capability. | U05/U07 capacity and caller tests plus preserved native approved-caller and multiprocess stress suites. Final closure also depends on the root-owned promotion/catalog integration. |

## Shared APIs

- `supervisable_position_count(database, unverified=None)` opens one verified,
  query-only PAPER snapshot. Canonical states are exported as
  `SUPERVISABLE_POSITION_STATES`: `paper_positions/OPEN`,
  `paper_future_positions/ACTIVE`.
- `exit_capacity_contract(state, opened_count=...)` returns demand, endpoint/global
  gaps, identity-tracking limit/gap, deadline and READY or
  ACTIVATION_BLOCKED_EXIT_CAPACITY. `budget_policy` and native policy validation
  reject impossible declared demand. READY is a software feasibility condition;
  it does not certify current provider OPEN capacity.
- `RuntimePPIBudget.observe_exit_round(*, elapsed_seconds, deadline_seconds,
  failures=0)` is an explicit producer write, exposed by the native reader.
  COMPLETE requires a verified exact five-key PAPER scope, fresh service of
  every current identity, no unresolved current critical pressure, zero read
  failures and elapsed time within the exact policy deadline. An isolated book
  does not release whole-round debt. Unknown/stale/incomplete/late evidence
  returns DEGRADED and suspends LOWER in both acquire and START.
- If SQLite cannot commit round pressure, a fixed-size private sidecar marker
  persists LOWER suspension across independent budget instances. Aliases and
  invalid measurements fail closed. Marker writes/deletes sync their directory;
  failure while clearing restores the barrier. SQL/IO uncertainty returns a
  fixed DEGRADED diagnosis without terminating the EXIT producer or editing
  an existing wire lease, circuit, request receipt or capacity envelope.
- `runtime_budget_snapshot(database, as_of=...)` reads the canonical existing
  sidecar with no bootstrap, mkdir, clock update, pruning, chmod or SQL writes.
  It reports ABSENT, OBSERVED, DEGRADED or UNVERIFIED; global counters,
  `exit_service`, and `telemetry_retention` are fixed sanitized projections.
  Missing data stays unknown. WAL header/auxiliaries and aliases are rejected
  before a SQLite read can create SHM. Source-tree inventories are unchanged
  under normal, missing, corrupted, aliased, WAL and contested-source tests.

Normal WAITING defers lower work and remains OBSERVED. It does not declare a
missed deadline. DEGRADED reflects unresolved critical/round pressure or the
round barrier. The current
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

The versioned register [RC6_BUDGET_F01_CONVERGENCE.json](convergence/RC6_BUDGET_F01_CONVERGENCE.json)
links U01/U02/U05/U07/U27, all **35 semantic F01 clauses**, quota extensions
X1/X2 and R29–R36/R45/R64–R66 to exact executed test nodes and receipt hashes.
It distinguishes SDK fake-wire, native SQL with modeled sends and actual
process interruption; parametrizations never become independent attack counts.

The combined run preceding the retained-capacity followup used:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
/workspace/venv_rc6/bin/python -m pytest -q -p no:cacheprovider \
  tests/test_rc6_ppi_global_budget.py \
  tests/test_issue465_budget_adversarial.py \
  tests/test_rc6_convergence_budget_liveness.py \
  tests/test_rc6_exit_reader_cadence.py \
  tests/test_rc6_no_budget_permission_probe.py \
  tests/test_rc6_approved_factual_callers.py \
  tests/test_issue465_stress.py \
  tests/test_rc6_source_consolidation.py \
  tests/test_rc6_ppi_iol_reconciliation_rc6.py \
  tests/test_rc6_final_family_source_policy.py \
  tests/test_rc6_byma_morning_pipeline.py \
  tests/test_rc6_cauciones_shadow_evidence.py \
  --basetemp=/tmp/rc6-budget-source-final-20261005 \
  --junitxml=/tmp/rc6-budget-source-final.xml
```

Result: **334 passed, 0 failed, 0 errors, 0 skipped**, 201.676 seconds, 195
distinct function families. [Versioned JUnit](convergence/evidence/rc6-budget-source-final.xml)
SHA-256 `1d373c796d933109ba3c67014230bcda0ae91b8f6eade7bd31f1b2b45f28b9a7`.
A [post-commit fault receipt](convergence/evidence/rc6-budget-postcommit.xml)
checks directory-sync recovery, BUSY, aliases, invalid measurements and the
tracking boundary: **12 passed**, zero nonpasses, SHA-256
`abf4b6b5543b9a9b297fe851ea2357d62934ad1c50fc3ce981a716f35201412c`.
Across both receipts there are **335 distinct executed nodes / 196 function
families**; overlapping tests are not added as new scenarios.

The prior 240-node revalidation produced three REDs. One assert wrongly
expected carry as the final futures cause; native daily-risk stale/latch has
precedence. Two caller fixtures lacked an explicit interval-volume contract;
the root dependency `d5b2a0a7` supplies its dated offline contract. The full
[RED receipt](convergence/evidence/rc6-budget-revalidation-red.xml) is preserved,
SHA-256 `c76a76239dc61c6ae8897204503e0b77ecd98d11aed136fc566e5c8911ec3740`.
Both causes were corrected without relaxing production guards.

At the 5-Oct cutoff the mixed ledger has five spots and AGO/SEP/OCT/NOV/DIC
futures. AGO/SEP are expired pending positions; only three futures series are
current. Thus ten positions need conservative supervision although eight are
currently viable. August entries use an explicitly dated
`OFFLINE_SYNTHETIC_PENDING_LEDGER_ONLY` execution-grid fixture and make no
historical PPI/A3 claim. The dated real standard-grid evidence remains known
only from its actual October retrieval. The native financial supervisor
evaluates carry/expiry and preserves daily-risk priority; its PAPER closures
do not prove availability of expired contracts' provider books.

The producer monotonic clock is controlled at its public module seam for
deterministic schedule arithmetic. Single-flight deadlines use actual
monotonic thread/process waits. Neither fixture measures production-host IO,
provider latency, current quota or a factual ten-position five-second SLA.

The integrator must rerun these tests on the single final candidate and artifact.
Fake-wire timing does not certify current provider latency, account quota,
production position count or OPEN capacity. An already-emitted synchronous HTTP
body remains owned by the transport; the deadline alarm suspends lower activity
and records degradation without claiming to cancel that body. Capacity approval
still requires factual evidence from the canonical promotion path. No deployment
authorization follows from this document.

## Retained receipt capacity followup: U02/U05

The additional native SQL reproduction found a material gap after the earlier
green runs. Twenty thousand synthetic, already-used LOWER receipts remained
within the one-hour binding horizon. None belonged to the current30s rate
window. The75-per-endpoint/global225 envelope reserved EXITbook30 but rejected
EXIT with `PPI_BUDGET_ROW_LIMIT`. Rate reserves alone did not protect the
retained receipt table. The earlier six-scopes/second load generated denials,
so it did not exercise this admitted-wire growth.

The [versioned RED probe](convergence/evidence/rc6-budget-retention-red-probe.json)
records the exact SQL counts/denial and explicitly identifies its synthetic
historic debt and parameter-only position count. The new native regression
independently obtains75/225 through the actual offline SDK benchmark,
recommendation/approval/controller and a real five-position PAPER ledger.
Its HTTP adapter remains fake; this is no evidence of actual PPI quota.

Commit `1637289f61d15a5ae6c6eb2e62ad6cbd289a6885` reserves future retained
EXIT growth before LOWER acquire and START. It compares future critical sweeps
with every historic receipt's exact legal expiration, rather than treating
previously used EXIT rows as spare capacity. The same checks reserve physical
pages for fixed-size receipts, permitted critical book caches, binding state
and bounded telemetry under the existing DB+DELETE-journal quota. Static
activation rejects a critical stream that cannot fit20k retained rows or the
configured physical budget. Twenty-eight positions at5s need20,188 retained
receipts at the strict boundary; that cannot become READY even with otherwise
sufficient endpoint/global rate limits. No cap, quota or20k ceiling is raised.

The old tracking fixture claiming64 identities/5s READY was incompatible with
the retained table. It now isolates the tracking limit with an explicit60s
cadence and32MiB quota; the separate retained-capacity tests reject the
impossible5s configuration. Position/intent data and valid old authority are
preserved. Warm EXIT can use only its existing policy, debt, expiry and cadence.

The public API is
`exit_retention_preflight(database, state, *, opened_count, as_of)`. It reads
the canonical sidecar with `mode=ro`/`query_only`, performs no bootstrap,
maintenance, clock update or permission change, and returns sanitized
`READY`/`ACTIVATION_BLOCKED_EXIT_CAPACITY` plus source and space evidence.
The root controller consumes this as `exit_receipt_capacity`, outside the
static `exit_capacity` fingerprint; merely fluctuating occupancy must not
change a healthy configuration fingerprint.

Commit `51318d8ab1a2ec22c01ed51820f5abbeb623d1ab` distinguishes binding
`retained_receipts`, `physically_stored_receipts` and
`legally_expired_receipts`. A readonly probe may credit only the legal pruning
that native admission executes before all growth. It never credits hypothetical
free pages or any receipt held by an inflight token, including an old used
token with an uncertain outcome. A physically full sidecar remains blocked.

The independent review found one further edge: a missing main file was
reported ABSENT before checking orphan companion files. Commit
`9bbc0a9f5303e3752d8025755a97da957cc0e46b` checks companions first in both
readonly APIs. Missing main plus journal/WAL/SHM, a degradation marker or known
lock companions is UNVERIFIED/BLOCKED. None are deleted or repaired. A truly
empty cold allocation is the positive control and produces exactly one native
SDK market send; all seven orphan conditions produce zero market sends and
leave bytes/permissions unchanged.

The new guards are in
[test_rc6_budget_receipt_retention.py](../../tests/test_rc6_budget_receipt_retention.py).
They cover native20k rejection before wire,90 actual uncoalesced SDK EXIT sends
plus actual LOWER probes over18 manually scheduled rounds/three30s windows and
two restarts, real binding-table SQLITE_FULL, preserved live debt, legal aging,
START revalidation, recent bunched EXIT debt, small quota, readonly unknown
state and orphan companions. These manual rounds verify retained capacity;
the existing native EXIT-worker tests separately verify producer cadence and
coalescing. Synthetic history does not become historical provider evidence.

The [213-case compatibility receipt](convergence/evidence/rc6-budget-retention-native.xml),
[98-case interop receipt](convergence/evidence/rc6-budget-retention-interop.xml),
[55-case aging/producer/promotion receipt](convergence/evidence/rc6-budget-retention-aging.xml)
and [final15-case receipt guards](convergence/evidence/rc6-budget-retention-orphan.xml)
all have zero failures, errors and skips. Each receipt retains its source-state
boundary in the JSON register. The15-case final guard SHA-256 is
`cd16dcb7f30b4c864b0058422c4824861ac501abbe3db50b0c047978314cdec8`.
The integrator still owns the complete frozen candidate/artifact execution;
these intersecting receipts are never summed as independent scenarios.
