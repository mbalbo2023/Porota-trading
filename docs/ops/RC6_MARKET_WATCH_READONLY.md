# RC6 Market Watch READ_ONLY (2026-10-14)

## Scope and authority

Workstream: WS-RC6-MARKET-MONITOR-READONLY-20261014.

- Control plane: default branch main (GitHub Actions schedules require the default branch).
- Productive source: deploy/rc6-pr69-isolated-20260915, read from GitHub on every run.
- Isolation: no application deploy, no host files altered, no trading SQLite writes,
  no PPI Watch access, no broker/provider requests, no changes to systemd/Docker,
  no REAL orders or authorization, no rollback.
- No membership in rc6-unified-paper-deploy. Separate READ_ONLY concurrency group.

Workflow: .github/workflows/rc6-market-watch-readonly.yml
Python: scripts/rc6_market_watch_readonly.py
Offline regression: tests/test_rc6_market_watch_readonly.py

## Cadence

- Existing dynamic SHADOW: worker attempts a tick every 30 seconds.
- Existing performance capture: tick every 5 seconds; compact report every ~5min.
- Existing fast functional health: every 5 minutes.
- This independent GitHub schedule: minutes 07/17/27/37/47/57 of UTC 13–20,
  on weekdays. Argentina-time gate permits 10:30–17:15 ART.
- GitHub schedules can be delayed or dropped. This is not a real-time safety interlock.
- The original 03/13/23/33/43/53 schedule had zero schedule events despite
  active workflow and successful manual/push runs on 2026-10-14. The offset
  moved further from the hour's highest load; this remains unverified until
  two actual event=schedule artifacts exist. Manual runs do not substitute.
- On pull request: unit tests ONLY, no SSH or secrets.
- On merge/push to main: unit tests plus one READ_ONLY host observation.
- On workflow_dispatch: optional one-shot.
- Existing T-45/T-10 preopen and postclose remain unchanged.

## Read-only acquisition

- Host: strict pinned-key SSH and sudo -n python3 -; the Python source is
  streamed from GitHub Actions stdin without installing code on the Droplet.
- SQLite: URI mode=ro, query_only=ON, 2-second aggregate deadline. No
  quick_check, history migration, historical table scans or provider calls.
- Read observer mode, exact INTEGER zero, heartbeat, market status/data timestamp.
- Read last-row ID (cursor, NOT total) for decisions, gates, fills, market
  snapshots, scalping candidates and events.
- Optional bounded readiness aggregate over candidate_identity_v2.
- Read bounded dynamic SHADOW status.json and verify its envelope digest.
- Read compact performance report, but never infer economic profitability.
- Read frozen CURRENT_STATE_V2 provenance and compare GitHub production SHA
  against three Docker container OCI IDs, without restart.
- Read root disk, inode free and MemAvailable. No dump of host secrets.
- Read the two functional-health timer active states and sanitized observer
  Docker state in the ordinary bounded snapshot, even if the optional manual
  SHADOW container diagnostic times out. These are observations, not restart
  causes or authorization to change systemd.
- PPI Watch never read or modified.

## Severities

P0: invalid/nonzero/unknown real counter, wrong PAPER mode, missing trading DB,
invalid observer clock, missing container/OOM, provenance/SHA/OCI divergence,
free disk below 2 GiB or free inodes below 10%.

P1: market-open PPI data age above 15min, missing/stalled SHADOW or performance
output, explicit SHADOW FAIL_CLOSED even during PREOPEN (with typed error_class),
worker heartbeat older than 5min, free disk below 3 GiB or
MemAvailable below 64MiB. Escalate P1 only after TWO comparable samples with
the same issue code in <=30min on the same SHA. P0 escalates immediately.

P2: below the 8GiB disk target, container restarts, optional SHADOW report
absent outside OPEN, informational source or readiness states. Zero trades
and absence of signals are not automatically incidents.

The market session state reported by the actual observer determines whether
live quotes are expected; a BYMA holiday is not misclassified by weekday alone.

## Alerts and evidence

- Each executing run uploads snapshot.json and decision.json, retention 7 days.
- A manual run also attempts a 12-second, read-only diagnostic in the
  observer container. Its shadow_diagnostic.json contains only the outcomes
  of RUNTIME_INPUT and METADATA, elapsed times, and SQLite class/code/name.
  It does not call ShadowRuntime.tick, change DB contents, or copy SQL values,
  credentials, paths, or market data. A failed remote diagnostic is labeled
  REMOTE_DIAGNOSTIC_UNAVAILABLE; it cannot certify the SHADOW worker. The
  host uses the established sudo Python entry and launches a bounded Docker
  subprocess, returning only a sanitized status if that fails.
  A manual run also records read-only EXPLAIN QUERY PLAN details for five
  fixed SHADOW SQL shapes and the last sixteen observer cycle durations,
  including a bounded planned cadence parsed from each cycle's durable plan. It
  plans
  are diagnostic; they do not execute those SELECT statements or assert a
  database migration occurred.
- The diagnostic is intentionally absent from scheduled runs to avoid
  additional intraday DB load. A passing stage in a manual probe does not
  establish the causal failure stage of a separate worker tick.
- During the 15 October systemic RCA, a main push also runs one bounded
  read-only diagnostic. It measures row counts, exact query plans, elapsed
  time and remaining shared 2-second budget for catalogue, open positions,
  contracts, intraday and snapshots. The cursor-only timing omits Python
  transformation costs, so it is a lower bound for the worker. WAL size is
  observed via file stat; no checkpoint or schema change is performed. The
  push diagnostic must be returned to manual-only after evidence capture.
- Manual runs separately test the public GitHub workflow schedule API from
  inside the existing observer container, with a five-second network timeout,
  a bounded response and only sanitized count/identity fields. This proves
  connectivity for a proposed independent silence timer; it does not install
  that timer or turn missing `event=schedule` executions into a success.
- Manual runs also read at most 90 minutes of Docker lifecycle event metadata
  for the observer alone. For its latest nonzero exit, they inspect at most
  300 log lines within 90 seconds before and 10 seconds after that exit.
  The artifact contains only action, epoch second, validated numeric exit
  code or signal, bounded exception class names, categorical SQLite
  OperationalError reason hints, last application frame (module basename,
  line and function) and traceback count. Raw
  logs, messages, attributes, arguments, environment and other containers
  never enter the artifact; unavailable probes stay explicitly unverified.
- Successive observations first inspect prior attempts of the *same* run ID,
  then completed runs. Each ZIP must match GitHub's SHA256 digest and contain
  matching snapshot/decision timestamps, schema and productive SHA. Its
  sample must precede the current sample by no more than 30 minutes. The
  source receipt records run, artifact, digest, product SHA and timestamp;
  missing, expired, damaged or incompatible evidence leaves P1 pending.
- The new confirmed P0/P1 code and recoveries are de-duplicated in decision.json.
- GitHub Actions fails on confirmed incidents and prints a short annotation.
- A confirmed transition is delivered through the existing PAPER container's
  TelegramTransport. Its token/chat remain inside that container; the runner
  sends only a bounded incident body. The read-only monitor does not enqueue
  in SQLite. The receipt records SENT only after Telegram returns a valid
  message_id; failure/timeout stays UNVERIFIED and the next sample retries
  the same incident ID. A missing schedule delays retries. No delivery is
  claimed until a GitHub Actions artifact contains the ACK. The local outbox
  status alone is not evidence of monitor delivery.
- A remote SSH/probe failure produces P0 REMOTE_PROBE_UNAVAILABLE and does
  not pretend to observe real_orders_sent=0.
- API/checkout failures leave workflow failure, not invented runtime evidence.

## 15 October retry incident

Run 38694565268 attempts 2/3/4 produced artifacts 11981619235,
11983080832 and 11985131978. All three had DYNAMIC_SHADOW_FAIL_CLOSED P1
on the exact productive SHA 64583571846addb85038554dc56230bafbc7e6b4.
The previous-artifact step excluded its own GITHUB_RUN_ID, so all three
decisions said previous_comparable=false, confirmed=[] and NO_TRANSITION.
This is a control-plane confirmation bug; SUCCESS for the watch job did not
mean the engine was healthy. The retry regression checks same-run selection,
digest failure, SHA/time incompatibility and the Telegram ACK requirement.
Delivery for this incident remains NO_VERIFICADO until a fresh artifact
contains a verified message_id.

The first live main runs after #569 were 38770675417 and 38771011936.
Their artifacts 11992150480 and 11991751440 were independently digest
verified, 2m26s apart on the same product SHA and both P1; the second still
said previous_comparable=false. The first implementation eagerly fetched up
to eight older runs before testing the nearest artifact. The READ_ONLY step
took about 17 seconds and returned UNAVAILABLE. The corrected lookup stops
at the first verified comparable artifact, including a prior attempt of the
same run. A regression fails if it fetches an older run after a valid match.
No delivery is claimed from these two artifacts.

The next main cut, run 38771696065 / artifact 11992266292, still returned
UNAVAILABLE after 13.5 seconds in the Python urllib GitHub API path. The old
`gh api` step completed in about 1.6 seconds on the same runner/workflow.
The lookup now uses that existing `gh api` transport for bounded JSON and
ZIP retrieval, keeps GH_TOKEN out of argv/output, and verifies the returned
ZIP digest before trusting either JSON member. A fresh live artifact must
confirm the result; this change alone does not claim Telegram delivery.

The push diagnostic in run 38771011936 returned CONTAINER_PROBE_TIMEOUT.
Its implementation performed a full `read_runtime` probe before a second
per-query timing pass. The manual diagnostic now executes only the latter
bounded read-only pass, explicitly marks the full runtime read as NOT_RUN,
and remains absent from normal push/schedule runs. An actual manual artifact
is required before treating any timing or plan as measured live evidence.

Monitor confirmation is now VALIDADO: run 38772145395, artifact 11991412859
SHA256 5b9cc43952922e52c924af1e6b790fe93ec77f04c4feaa7c000e75bccf1b482a
compared prior artifact 11992266292 (same product SHA, 3m21s earlier),
confirmed DYNAMIC_SHADOW_FAIL_CLOSED P1 and recorded Telegram SENT with
message_id 928. The Action correctly failed its incident gate. This proves
alert delivery, not SHADOW recovery.

The manual READ_ONLY diagnostic run 38772952791 on main
`194407f6e1bc9f299167e48f842b5b6d1ac07c7f` produced artifact
11991149714 (ZIP SHA256
`b6b050507c9cd47008b13c4b69504c0da5e832125daddce49becc6b059f3cbf7`).
At 16:55 UTC during MARKET_OPEN, its isolated SQL pass measured catalogue
14,592 rows/121 ms, positions 2/1 ms, contracts 2,404/138 ms, intraday
11,896/311 ms, snapshots 2,244/189 ms, with 1,238 ms remaining from the
2-second shared budget. The respective plans used the catalogue covering
index, intraday expression index, and snapshot rowid lower bound plus
expression-index subquery. WAL was 31,987,712 bytes; no OOM or restart.
This single sample does not establish a worst-case margin. Observer cycles
lasted 135–311 seconds, and the precise source of competing load remains
NO_VERIFICADO. Neither SQLITE_BUSY nor physical corruption was observed.
SHADOW remained FAIL_CLOSED/SQLITE_INTERRUPT. The same artifact confirmed
that P1 plus a stale intraday worker heartbeat, with Telegram SENT message
ID 929, PAPER/0 and routes NOT_CALLED.

Product candidate PR #574, base `64583571846addb85038554dc56230bafbc7e6b4`,
keeps the SQLite deadline at 2 seconds, extracts unchanged SQL shapes from
one WAL snapshot and releases that snapshot before bounded Python conversion.
It records safe stage and plan evidence on both success and failure. The
concurrent-writer and production-scale fixtures passed locally; Predeploy V2
and OPEN runtime equivalence remain separate gates. Until a final exact
Deploy V2 and three independent OPEN cuts, the SHADOW repair is
NO_VERIFICADO and fail-closed.

## Controls and continuity

- All changes via isolated branch and PR into main; no direct writes.
- Merge into main does NOT trigger the productive Deploy V2 branch.
- Verify the first main push Action and scheduled Action before marking active.
- PPI Watch untouched, PAPER/SHADOW only, FIX-FORWARD only.
- Open separate technical debt: the trader terminal's committed-generation
  adapter lacks the requested canonical reader in currently deployed
  SHADOW persistence; historical currency lineage remains BLOCKED; full
  economic out-of-sample proof remains NO_VERIFICADO.

## RCA -> FIX -> GUARD -> TEST -> EVIDENCE

Cause: overnight runtime checks verified containers and PAPER safety but
did not directly sample the new dynamic SHADOW and performance artifacts.
Fix: independent bounded READ_ONLY samples of durable output with exact
runtime provenance.
Guard: exact integer zero, signed heartbeat, SHA and OCI identity, verified
SHADOW envelope, bounded resource use and no fill assumption.
Test: healthy idle market, NULL/bool counters, future heartbeat, missing
or corrupted SHADOW, stale reports/quotes, SSH loss, inconsistent SHA, OOM,
alert confirmation and de-duplication.
Evidence: actual per-run GitHub Actions artifact. Until such a run finishes,
the monitor is EN_GITHUB/NO_VERIFICADO, not VALIDADO_RUNTIME.

## Checkpoint lineage

The main-branch PR is verified against its final exact commit SHA, not a
previous CI attempt's frozen webhook payload. GitHub CHECKPOINT attestation
must see the current PR body on a new synchronize event. This is a control
plane-only commit; no runtime installation or deployment is authorized.
