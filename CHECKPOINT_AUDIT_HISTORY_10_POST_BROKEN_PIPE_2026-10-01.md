# CHECKPOINT — AUDIT WS-HISTORY-10 POST BROKEN PIPE — 2026-10-01

- mode: READ_ONLY
- branch: audit/ws-history-10-post-broken-pipe-20261001
- base_sha: f8a1db5d3f008d2c5c619869704126edbd39a6a1
- trigger: repair run 36804718603 lost SSH transport with "Broken pipe" after REPAIR_BEGIN.
- purpose: determine whether rc6_history_cutoff_repair_once.py is still active and measure DB progress before any retry.
- no DB write, no Docker restart, no systemd change, no deploy, no cleanup.
- PAPER/SHADOW ONLY; real_orders_sent=0; PPI Watch untouched.
