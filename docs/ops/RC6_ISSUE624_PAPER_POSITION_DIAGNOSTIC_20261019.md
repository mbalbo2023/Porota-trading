# RC6 #624 — PAPER position read-only diagnosis

Checkpoint ID: RC6-624-PAPER-POSITION-READONLY-20261019

RC6 product baseline: `d52e45fe85ed184a76d35120ca543f5d63f38b2c`. Diagnostic branch `diagnostic/rc6-624-paper-position-20261019` is based on `main` solely to run a PR-scoped GitHub Actions probe. It is DRAFT/NO_MERGE and not a deployment source.

At 2026-10-19 22:32:53Z, run 39335705836 found one `paper_positions.status='OPEN'` row, `PRODUCTION_PAPER`, `real_orders_sent=0`, `WAITING_MARKET/MARKET_CLOSED`. That count alone cannot identify the opening or closing cause. The new one-shot workflow reads the actual position's exit intent, simulated fills, latest stored quote, supervisor and reader heartbeat under 128 MiB RAM/PSI admission and a 2-second SQLite progress bound. It performs no DB writes, Docker actions, disk walk, PPI Watch access, order routing, deploy or quarantine. A red resource gate yields NO_EVIDENCE, not a false root cause.

Decision remains NO_GO_HOST and SHADOW FAIL_CLOSED. The candidate PR646 SHA `133dde467c3e47f6598d42fe0222b0448089e353`, tree `49813af45c3efbf6782c85b72168f3db93f9c5c3`, Predeploy V2 run 39330909711 is certified OFFHOST only. Any actual host deploy/COLD2 quarantine requires fresh admission and explicit later authorization; do not fabricate position closure. This diagnostic checkpoint does not change product or runtime.
