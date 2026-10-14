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
  fixed SHADOW SQL shapes and the last sixteen observer cycle durations. It
  plans
  are diagnostic; they do not execute those SELECT statements or assert a
  database migration occurred.
- The diagnostic is intentionally absent from scheduled runs to avoid
  additional intraday DB load. A passing stage in a manual probe does not
  establish the causal failure stage of a separate worker tick.
- Successive runs compare against the latest downloadable completed artifact,
  never invent a prior observation if an artifact is missing or expired.
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
