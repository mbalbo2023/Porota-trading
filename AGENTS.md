# POROTA TRADING — CODEX / AGENT ENTRYPOINT

Version: `2026-10-03/1`

This file is the repository entrypoint for agents that start from the default branch. **`main` is NOT the operational truth for RC6.** Do not derive the current production baseline, runtime state, active workstreams, READY universe, or deploy status from `main`.

## 1. Mandatory RC6 handoff before any write

For any RC6 analysis, development, audit, CI, deploy, dashboard, broker, market-data, strategy, or runtime task:

1. Resolve the current canonical RC6 product branch from fresh GitHub/release evidence.
2. Latest verified pointer at this documentation cut:
   - product branch: `deploy/rc6-pr69-isolated-20260915`
   - GitHub product HEAD: `da697c6e6c2274579f9e4a112fabc4327475dd35`
   - this pointer is a bootstrap only; **revalidate before use**.
3. Read the `AGENTS.md` from that product branch.
4. Read that branch's `ops/policy/porota-policy.yaml`.
5. Read the latest release/handoff and machine-generated runtime evidence available on that branch / GitHub Actions.
6. Inspect current PRs/workstreams and ownership before choosing a writable scope.
7. Branch from the verified current product SHA for RC6 work unless a newer canonical integration base is explicitly proven.
8. Never continue from chat memory, an old checkpoint, or this file alone.

If the current product branch or SHA differs from the pointer above, the fresh GitHub/runtime evidence wins. Do not "correct" current reality back to this document.

## 2. Non-negotiable safety

- PAPER/SHADOW ONLY.
- Required runtime mode: `PRODUCTION_PAPER / SIMULATION`.
- `real_orders_sent=0`.
- Real order routes must remain blocked / `NOT_CALLED`.
- PPI Watch: **DO NOT TOUCH** unless the user gives explicit scoped authorization.
- FIX-FORWARD ONLY. No runtime rollback.
- GitHub + GitHub Actions are the normal control plane.
- Interactive SSH/manual terminal is BREAK-GLASS only.
- Never use Codespaces as the normal operating environment.
- No direct development writes to `main` or the protected product branch. Use isolated branches + PRs.
- A GREEN workflow is not, by itself, runtime validation.

## 3. Evidence contract

Use these states when describing implementation/operation:

- `PROPUESTO`
- `DESARROLLADO`
- `COMMITTEADO`
- `EN_GITHUB`
- `ARTEFACTO_VALIDADO`
- `DESPLEGADO`
- `VALIDADO_RUNTIME`
- `NO_VERIFICADO`
- `BLOQUEADO`

Truth hierarchy:

1. GitHub/SHA = code.
2. GitHub Actions = executed pipeline.
3. Artifact + manifest + digest/hash = tested package.
4. Droplet/runtime = deployment.
5. DB/logs/APIs = actual behavior.
6. Chats are NOT operational truth.

Separate explicitly when relevant:
- HECHO VERIFICADO
- HIPÓTESIS
- INFERENCIA
- RECOMENDACIÓN

If evidence is missing, use `NO_VERIFICADO`. Never fill operational gaps by inference.

## 4. Concurrency / ownership

Every RC6 task must operate as one of:

- `READ_ONLY`
- `WRITE_OWNER`
- `DEPLOY_OWNER`

`READ_ONLY` never blocks.

A `WRITE_OWNER` must declare:
- WORKSTREAM_ID
- branch
- base SHA
- scope
- paths/components
- status

Do not write into another active owner's overlapping scope.

`DEPLOY_OWNER` is globally exclusive for the productive runtime. While it exists, other work may read/audit or develop on isolated non-overlapping branches, but must not deploy, restart runtime, mutate systemd/DB, clean shared Docker state, or otherwise interfere with production.

Canonical deploy concurrency must remain:
- `group: rc6-unified-paper-deploy`
- `cancel-in-progress: false`

Independent workstreams converge into one reconciled candidate and one coherent final deploy.

## 5. Deploy discipline

Canonical model:

`source SHA → exact predeploy → build once → immutable artifact → manifest/digest → tests/gates → transfer → promotion → runtime validation → safe cleanup`

Requirements:
- build once;
- no rebuild on the Droplet;
- test and deploy the same immutable artifact;
- manifest derived from checkout;
- blocking preflight;
- direct runtime validation;
- cleanup safe and measured;
- no blind merge of old/broad PRs;
- no branch deletion by age/name;
- preserve open PR heads and REVIEW/evidence branches.

Significant failures follow:

`ERROR → RCA → FIX → GUARD → TEST/CI/PREFLIGHT → EVIDENCIA`

A repeatable error is not fully closed if it can recur silently and no permanent guard/regression is left when technically possible.

## 6. Market-data / instruments

- PPI is the primary identity/contract authority.
- IOL is complementary.
- BYMA/A3/ROFEX are complementary when supported by valid evidence.
- Complementary sources must not overwrite PPI identity.
- Never resolve ambiguous identities arbitrarily.
- Readiness is per instrument and fail-closed.
- `OPERATIONAL_SCOPE=ALL_CONTRACT_FAMILIES` does not mean every instrument is READY.
- Keep contractual readiness separate from strategy validation and demonstrated economic edge.
- Unknown broker commercial terms remain `NO_VERIFICADO`; PAPER simulation policy is a separate concept.

## 7. Financial/performance work

Do not interpret:
- more READY instruments,
- more trades,
- a higher score,
- a GREEN CI run,
- or a profitable in-sample replay

as proof of economic edge.

For strategy/performance changes:
- preserve a factual BASELINE;
- evaluate candidates in SHADOW first;
- use point-in-time data only;
- prevent look-ahead bias;
- include transaction friction/costs;
- distinguish currencies;
- keep signal, decision, intent, fill, exit and P&L lineage auditable;
- do not tune Stop/TP/EOD/MaxHold only to improve a retrospective sample;
- do not relax risk/freshness/depth controls merely to increase activity.

## 8. Accessibility invariant

The user operates primarily by voice and must not be made to perform normal repository/runtime operations manually.

- Normal operations must be executable through GitHub/GitHub Actions/connectors.
- Do not require terminal/SSH/copy-paste for routine work.
- If user-facing text must be copied, keep it within practical tablet clipboard limits.
- For long reusable instructions, prefer a downloadable file over a huge copy block.
- Group actions and minimize repetitive manual steps.

## 9. Stale legacy context

Older checkpoints/handoffs may remain in `main` for history. They are evidence of their own time only. They MUST NOT override:
- current product branch HEAD,
- current Actions,
- current artifact identity,
- current runtime,
- current DB/log/API evidence,
- or newer reconciled handoffs.

In particular, do not treat the old September continuity checkpoint referenced by previous versions of this file as the current RC6 operational baseline.

## 10. Starting a new Codex workstream

A new Codex task should normally need only:
1. this bootstrap;
2. the current product branch `AGENTS.md` + policy/current evidence;
3. the task-specific order/specification and attached evidence;
4. fresh GitHub state.

The task-specific order is authoritative for scope. This bootstrap is authoritative for safety/governance. When they conflict, stop the conflicting action, preserve safety, and mark the issue `BLOQUEADO` or `NO_VERIFICADO` rather than inventing a resolution.


## 11. Legacy main-branch PR attestation

The default branch still has a legacy PR check that asks for September checkpoint-attestation fields. Supply truthful historical values when that check applies, but treat them only as compatibility metadata for the guard. They do **not** restore the old checkpoint as current RC6 truth; Sections 1 and 9 above still control continuity.
