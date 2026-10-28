# CHECKPOINT 00 — WS-DASH-07 HISTORY TRUTH

- base: integration branch after PR #389/#390.
- mode: WRITE_OWNER, Dashboard Históricos only.
- no deploy.
- requirement: do not show daily-history freshness as if it were candle freshness.
- evidence source: WS-HISTORY-08 run 36797940329.
- verified runtime before this code change: 95.015 candle versions; 111.092 samples; latest closed bar 2026-09-30 17:00 ART; candle worker RUNNING.
- UI change: cards split daily history, latest intraday bar/sample, worker state, focus feasibility and global rotation feasibility.
- rotation limit remains unchanged; current configured 20 already exceeds empirical recommended 10–15 and global ~6.945-symbol rotation is not feasible at 6 samples/90m.
