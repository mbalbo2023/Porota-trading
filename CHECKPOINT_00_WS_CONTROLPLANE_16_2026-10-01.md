# CHECKPOINT 00 — WS-CONTROLPLANE-16 — BOUNDED REMOTE OBSERVABILITY

Date: 2026-10-01
Mode: WRITE_OWNER
Base SHA: cd2f908a8a79be032279967855be58008aa62e90
Branch: work/ws-controlplane-16-bounded-observer-20261001

## Scope

Control-plane resilience only. No motor changes, no dashboard changes, no PPI Watch mutation, no production deploy, no runtime restart, no DB mutation.

## Incident class

Long-lived remote work must not keep a GitHub Actions SSH transport or a ChatGPT session waiting for completion.

Observed failure mode:
- a GitHub Action can lose SSH transport while the remote process continues;
- the remote process is then healthy/running but the caller no longer has a reliable synchronous channel;
- repeated polling or re-launching risks duplicate work and can leave an interactive chat blocked.

## Permanent operating rule

1. Long remote work is launched at most once.
2. Observation is performed by a separate READ_ONLY bounded probe.
3. Every probe has:
   - job-level timeout;
   - SSH ConnectTimeout;
   - bounded connection attempts;
   - ServerAliveInterval / ServerAliveCountMax;
   - an outer GNU timeout;
   - artifact evidence on every run.
4. A transport failure never authorizes a duplicate remote operation.
5. Before any retry, prove that the previous remote PID/process is absent.
6. Chat orchestration never waits indefinitely on Actions. It records the run/PR/SHA and continues independent work; later it performs one bounded status read.
7. GitHub comments/checkpoints/artifacts carry resumable state so a new chat can continue without reconstructing an in-memory wait loop.

## Initial implementation

Added:
- .github/workflows/rc6-ws-controlplane-16-bounded-probe.yml

The workflow is READ_ONLY and bounded to 5 minutes at job level and 90 seconds for the SSH probe. It reads:
- active history-repair process;
- observer PAPER invariants;
- history repair counters;
- canonical history row/latest date;
- PPI Watch status without mutation.

## Safety

Required:
- PRODUCTION_PAPER
- real_orders_sent=0
- real routes NOT_CALLED
- PPI Watch read-only / untouched
- no deploy
- no restart
- no cleanup
- no second history repair
