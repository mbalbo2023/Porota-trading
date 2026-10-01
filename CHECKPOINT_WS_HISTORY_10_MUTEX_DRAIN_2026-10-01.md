# CHECKPOINT — WS-HISTORY-10 MUTEX DRAIN — 2026-10-01

Purpose: replace an accidentally queued duplicate history-repair run without interrupting the repair already running.

- mode: DEPLOY_OWNER coordination only
- branch: work/ws-history-10-mutex-drain-20261001
- base_sha: 220501ce3929897ed59264cd70c2ae216a98b450
- no runtime mutation
- no deploy
- concurrency group: rc6-unified-paper-deploy
- cancel-in-progress: false
- expected behavior: GitHub keeps the current running repair, cancels/replaces the older pending duplicate with this no-op sentinel, then runs the sentinel after the repair.
- PAPER/SHADOW ONLY; PPI Watch untouched.
