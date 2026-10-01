# CHECKPOINT — AUDIT WS-HISTORY-10 POST BROKEN PIPE — 2026-10-01

- mode: READ_ONLY
- branch: audit/ws-history-10-post-broken-pipe-20261001
- base_sha: f8a1db5d3f008d2c5c619869704126edbd39a6a1
- trigger: repair run 36804718603 lost SSH transport with "Broken pipe" after REPAIR_BEGIN.
- purpose: determine whether rc6_history_cutoff_repair_once.py is still active and measure DB progress before any retry.
- no DB write, no Docker restart, no systemd change, no deploy, no cleanup.
- PAPER/SHADOW ONLY; real_orders_sent=0; PPI Watch untouched.

## Audit 1 — run 36805213510 — GREEN
- repair process still active: PID 1861766 / rc6_history_cutoff_repair_once.py.
- repair run row: state=RUNNING; targets=1,094; complete=899; failed=195; ppi_queries=726.
- live state distribution at 02:18:40Z:
  - ALREADY_COVERED 183
  - ARCHIVE_PARTIAL_COVERAGE 495
  - BLOCKED_NO_CANONICAL_BASELINE 130
  - COMPLETE 237
  - NO_NEW_VALID_ROWS 59
  - PPI_QUERY_FAILED 1
- history_canonical_v2 rows: 439,833; canonical_latest=2026-09-28.
- observer: PRODUCTION_PAPER / WAITING_MARKET / MARKET_CLOSED / PPI auth OK.
- real_orders_sent=0; real routes NOT_CALLED; PPI Watch untouched.

Interpretation: the GitHub Action failed because SSH transport broke, but the remote repair itself continued. A second READ_ONLY audit is intentionally triggered by this checkpoint update; no retry/mutation is authorized.

## Audit 2 — run 36805375380 — GREEN
- same repair PID 1861766 remains active.
- repair row remains RUNNING.
- traversal advanced materially:
  - ARCHIVE_PARTIAL_COVERAGE 495 -> 702
  - BLOCKED_NO_CANONICAL_BASELINE 130 -> 178
  - current COMPLETE bucket 33; NO_NEW_VALID_ROWS 9
- canonical store remains 439,833 rows / latest 2026-09-28 while traversal is in progress.
- observer remains PRODUCTION_PAPER / WAITING_MARKET / MARKET_CLOSED / PPI auth OK.
- real_orders_sent=0; real routes NOT_CALLED; PPI Watch untouched.

Decision remains: do not retry; allow the single remote process to finish. This update intentionally triggers Audit 3 READ_ONLY.

## Audit 3 — run 36805515443 — GREEN
- same repair PID 1861766 still active.
- repair script entered live PPI phase; run counters are per-current-execution and therefore not cumulative with the previous run.
- current run row: complete=203; failed=187; ppi_queries=30; state=RUNNING.
- identity states at sample:
  - ALREADY_COVERED 183
  - ARCHIVE_PARTIAL_COVERAGE 688
  - BLOCKED_NO_CANONICAL_BASELINE 172
  - COMPLETE 47
  - NO_NEW_VALID_ROWS 15
- canonical store: 439,833 rows / latest 2026-09-28.
- PRODUCTION_PAPER / MARKET_CLOSED / real_orders_sent=0 / PPI Watch untouched.

Code review confirms run counters are rewritten every 20 symbols from current execution state and are not cumulative across resumptions. This update triggers Audit 4 READ_ONLY.
