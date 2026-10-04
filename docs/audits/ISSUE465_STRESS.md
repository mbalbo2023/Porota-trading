# Issue 465 — offline stress and factual exit isolation

Owner: WS-FIX-AUDIT-08-G. Base: rejected frozen #463
`caf9bc94b4a1f436ad01a84a9e9e9e7a4a9e9423`. No production data,
provider, runtime, SSH, order route, PPI Watch, merge or deployment.

`scripts/rc6_issue465_stress.py` creates native schemas in an empty task
directory. The documented 1,200-identity baseline is multiplied by ten to
12,000 synthetic equity identities. Five native observations per identity
produce 60,000 Intraday rows, five times the declared single-observation
baseline. No claim of representative market activity or provider capacity.

The SHADOW source is immutable during consumption. Its SHA256 is checked
before and after; reads use mode=ro/query_only and conservative row/query
budgets. A separate process runs the actual worker, family reports, entry
lab, exit/economic lab and operational funnel with their existing bounds.
Five opened positions are represented in the observation source. Another
ephemeral factual PAPER ledger opens five positions and the real
PositionExitSupervisor closes all five through PaperBroker, confirms five
SELL_SIMULATED fills, and proves repeat supervision cannot duplicate fills.
Current is stale and the book is fresh. No callback substitutes for a fill.

The large workload reaches the existing 32 MiB funnel checkpoint bound and
explicitly fails closed. That is degraded SHADOW evidence, **not a completed
large cycle**. The harness records this fact and accepts only named resource
bound failures; arbitrary exceptions fail the regression. The small workload
must complete all handlers. This follows #465 F-05: bounded evidence failure
degrades SHADOW while factual exits remain available. Bounds are not raised
to force a success claim. Generation failure telemetry is checked separately
by Front C. The preliminary Python 3.12 measurement was ~20.6 seconds CPU,
~936 MB peak child RSS; actual five exits took ~54 ms. These are descriptive
offline results, not a host SLO. Exact candidate measurements are in the
Predeploy artifact and supersede these preliminary values.

Additional tests hold a real SQLite BEGIN IMMEDIATE writer lock while the
read-only consumer progresses, check catalogue overflow fails closed and
observation truncation remains explicit, block actual fsync in the isolated
SHADOW process until factual exits complete, force a 1 KiB quota, and run
OPENED/Scalping/discovery bursts in independent processes against the shared
SQLite book budget before five EXIT requests. A separate Intraday burst from
three Scalping/strategy/discovery processes stays within its five-call
endpoint cap while preserving the global and book exit floors, then all five
EXIT book calls pass. Each admitted lease is actually started and finished;
wire-count metrics must match. Conservative deadlines and memory,
evidence and query bounds are gates; microsecond thresholds are not.

Initial causal budget RED remains in
`stress-budget-red.xml`/`stress-budget-red.log` under the task evidence:
OPENED admitted five requests and consumed the entire exit floor; other
lower-priority processes were denied. Front A repairs the budget, not the
assertion. Early harness failures (missing native marks, quote identity/clock
construction) were corrected in synthetic fixture wiring; native safety and
ledger guards were preserved. The initial large-cycle success assumption was
replaced by the contract's explicit bounded SHADOW-degradation expectation;
the original measurement and failed test are retained.

Preliminary G results: five native stress/process/lock cases GREEN on Python
3.12 with provisional A `558cf04`, then on Python 3.11.16 with provisional A
`24ca06`. Both A heads were subsequently rejected by additional self-attacks;
these G results are historical measurements, not final dependency evidence.
The launcher binds the selected A budget in parent and spawned processes
without constructing a consolidated candidate. Final focal evidence must
bind the ultimately released A head. The full suite must then exercise all
seven current cases with the released C/D generation/retention implementation. Initial
causal REDs remain intact; final runs use unique evidence filenames.

First frozen G dependency evidence binds released A
`ade87a8325195c91249c947c9b5af4b2f7c11a93`: six stress/process/lock cases
GREEN on Python3.11.16, failures/errors/skipped/xfail0. Unique JUnit
`front-g-ade87-final-stress-311.xml` SHA256
`a24e435aef23ce09b15b016ed25fb68d358a0ba092d38b6829dd096efd6fa2eb`.
Large-workload CPU was ~24.93 seconds, peak child RSS 974,745,600 bytes;
five factual closes/fills took ~56 ms. The named funnel-bound degradation
remains explicit. Intraday admitted five within its cap; book lower priorities
admitted zero; each case then admitted all five EXIT requests. These are
component/dependency results. Consolidated source and the exact image still
require the full governed suite and final Predeploy.

The first complete integration suites executed all3,529 cases on both pinned
interpreters and found an additional harness defect: a valid50ms SQLite
acquisition backpressure escaped the burst child, instead of becoming an
explicit denial. Both original full RED JUnits are preserved. The reserve/cap
assertions did not change. The harness now catches only the exact expected
native acquisition uncertainty, records exact reason counts, and keeps
unexpected lifecycle errors as failures. A new real writer-lock child test
was RED before repair (`front-g-child-lock-causal-red.xml`), then GREEN; after
the lock is released all five EXIT starts/finishes pass and usage is exactly5.
The seven stress cases then passed against the same released A. No A source,
limits, timeouts, priority or risk parameter changed.

The subsequent exact consolidated `9ac` full suite executed 3,551 cases:
Python3.12 was GREEN, while Python3.11 exposed a native `start` writer lock
after an allowed Intraday acquisition, before any modeled wire. The original
full XML/log/binding and conservative unused claim are retained. An unchanged
offline real-lock witness reproduced the same failure on frozen G `f91f020a`
and A `c3a73a0e`; its first start returned typed STATE with native SQLITE_BUSY=5,
zero modeled wires and unchanged receipt/inflight/counters. This is a fixture
lifecycle defect, not permission to borrow the EXIT floor.

Reacquisition was declared before these two-path edits in
[native G WRITE_OWNER](https://github.com/mbalbo2023/Porota-trading/issues/465#issuecomment-5983420657).
The burst now holds the native OS wire scope, validates its exact readonly
receipt and inflight lease, and retries only the same `start` when the exact
public STATE error has native SQLITE_BUSY=5 and its unstarted claim remains
unchanged. After exactly one modeled read and verified completion, it can
confirm only that same lease's `finish`, preserving its immutable used debt.
No lease is acquired again, canceled, or sent twice. Every known fault records
its phase, native code and exact lease identity. Readonly verification or
wire-scope-entry failure remains fatal even for SQLite5; no unverifiable state
is labeled recovered. Unknown IO/SQLite codes, missing causes, invalid leases,
changed debt or inflight ownership and unverified completion remain fatal.

The offline confirmation deadline is checked before and after every snapshot,
start, modeled read and finish, and after entering and leaving the actual wire
scope. A peer caught a missing check after scope exit: the actual used receipt
remained safe, but a late exit could acknowledge success after its deadline.
That RED is preserved in the external peer receipt, and its permanent guard
now fails explicitly. Tests cover already-expired available state, deadlines
after readonly state and successful mutations, and delayed native scope/body
boundaries. A mutation that finished too late is still a failure; its original
used receipt is retained rather than canceled.

The deterministic real BEGIN IMMEDIATE fixture injects both a start lock and
a post-completion finish lock, checks exact conserved receipt/inflight/counters
under each denial, then requires five unique modeled wires and five EXIT used
receipts. Ten negative start/finish cases use actual native IO, SQLITE_LOCKED6,
SQLITE_CANTOPEN14, raw BUSY5 and missing-cause faults. Additional guards prove
unknown acquisition errors cannot become denials; EXCLUSIVE readonly failures
cannot be retried; marker/deadline/lease proof cannot be fabricated; and an
apparently BUSY response after mutated state cannot start or send again.

The original 30 attempts per lower child, denial-reason sums, lower-book=0,
Intraday in [1,5], total used=5+lower and all five EXIT admissions remain
unchanged. Added assertions equate every used receipt with a unique modeled
wire and bind parent/spawned source path/SHA to the frozen A dependency.
Constructor ACKs precede the same concurrent admission burst; the separate A
EXCLUSIVE/flapping startup guards remain active. Fixture waits and cleanup
are bounded. Native SQLite50ms, leases, caps, reserves and runtime code are
unchanged. This confirms finite recovery in an offline model; it does not add
automatic productive recovery or promise provider/SQL availability.

The successor adds 32 permanent lifecycle guards, bringing the exact stress
module to 39 executed cases. Final frozen JUnit, logs, synthetic metrics,
parent/spawn import bindings and retained causal REDs use unique paths under
`/workspace/issue465-evidence/front-g/`; the external handoff records exact
head/tree/digests without a source/evidence identity cycle. Both pinned
Python3.11.16 and Python3.12.14 must be GREEN with zero failure/error/skip/xfail.
The unchanged 39-case matrix gate remains a separate integration authority.

The third full-suite RED was an unchanged legacy directory fixture creating
0750 under this managed shell's0077 umask, producing0700. Its production guard
correctly rejected that mode. The local checkout runner now gives the pytest
child the standard GitHub0022 umask. No original test or guard was changed;
the original environmental RED is retained, with the final full-root and
Predeploy evidence required to establish closure.

The matrix gate adds 39 adversarial evidence cases. It binds the required
finding and clause IDs, minimum executed parameter counts, original audit
digest, all frozen front source blobs, test governance and zero nonpasses.
Omitted clauses, fabricated external closure, unexecuted regressions,
duplicate JUnit/JSON authority, altered frozen files, wrong tree, unresolved
ownership and non-root/manual test scope fail closed. These tests use private
synthetic Git/JUnit fixtures and cannot certify their own fictional data as
actual product evidence.

Peer self-review found two further matrix defects before release: comparison
of Git objects alone did not detect dirty disk bytes, and Python bool/int
equality accepted malformed safety types. Five causal RED cases are retained
in `front-g-peer-red.xml`. The guard now checks each actual disk blob/mode and
rejects aliases, while safety booleans and the integer order count must have
their exact types. The same tests then passed unchanged.

A further self-attack replaced the report and rebound its digest, or changed
the product/rejected authority, and malformed governed numeric fields were
accepted through Python equality/coercion. Eleven causal REDs are retained
in `front-g-authority-types-red.xml`. The actual CLI now pins the independent
report digest and source references; every governed count and exit code must
be a nonnegative integer. Synthetic test authority is scoped to the private
fixture and cannot change the CLI's actual authority.
Final 39-case gate JUnits are GREEN on both Python3.11.16 and3.12:
`front-g-final-authority-gate-311.xml` SHA256
`216de119a954d08906bc883e58a492e234776e4f1d16c23d704658eac87c9559`,
and `front-g-final-authority-gate-312.xml` SHA256
`725315fb26f3007a8ec9e47bacd862a5da4ba885322da986f26ab5fc42ca2718`.

In GitHub Actions, synthetic metadata-only results automatically enter
`runner.temp/porota-predeploy-evidence/issue465-stress/`: elapsed/CPU/peak
RSS, file bytes/counts, handler invocations, cycle completion vs degradation,
factual fills, source hash, requested/admitted/used/dropped budget and fixed
SQLite wait policy. No database, catalogue rows, account data, secrets or
source payloads enter these reports. RUNNER_TEMP is absent during ordinary
offline tests unless an explicit task evidence directory is supplied.
The committed synthetic causal RED compendium and frozen focal evidence are
copied into the same private artifact evidence directory when present.

Reproduce with the pinned lock and:

```sh
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q tests/test_issue465_stress.py
python scripts/rc6_issue465_stress.py --root /tmp/issue465-new-load --out /tmp/issue465-load.json
```

Host resources/latency/freshness, OPEN capacity and OOS financial performance
remain NO_VERIFICADO. All real orders in this harness are blocked;
`real_orders_sent=0`, real routes NOT_CALLED, provider_requests=0.
