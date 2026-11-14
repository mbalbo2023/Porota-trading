# POROTA TRADING — CODEX / AGENT ENTRYPOINT

Version: `2026-11-13/R335`

**Autonomous night-shift / Deploy V2 operations (R349):** [RC6_NIGHT_AUTONOMOUS_EXECUTION_20261113.md](docs/governance/RC6_NIGHT_AUTONOMOUS_EXECUTION_20261113.md) is the detailed step-by-step entrypoint for blocker triage, owner-controlled implementation, CI/CD simplification, source/PR hygiene and immutable release to physical droplet. It includes a dated snapshot **not** a live authority. Before acting, recheck the latest #1093, #980, HEADs, Actions, artifact manifests and host receipts. No repeated ordinary authorization requests; technical exact-identity GO and host/security guards still apply. Safety remains PAPER/SHADOW ONLY, PPI Watch untouched, no rollback, Actions first and one deploy owner.

This file is the repository entrypoint for agents that start from the default branch. **`main` is NOT the operational truth for RC6.** Do not derive the current production baseline, runtime state, active workstreams, READY universe, or deploy status from `main`.

## 1. Mandatory RC6 handoff before any write

For any RC6 analysis, development, audit, CI, deploy, dashboard, broker, market-data, strategy, or runtime task:

1. Resolve the current canonical RC6 product branch from fresh GitHub/release evidence.
2. Governance bootstrap (verify all pointers from fresh GitHub):
   - product branch: `deploy/rc6-pr69-isolated-20260915` (verify the current canonical branch);
   - **do not pin a product SHA from main**; verify current HEAD/PRs/Actions live;
   - open [#1093 continuity](https://github.com/mbalbo2023/Porota-trading/issues/1093), successor to saturated [#732](https://github.com/mbalbo2023/Porota-trading/issues/732); read its newest corrections and follow any explicitly verified newer successor;
   - apply [RC6 Deploy V2 / Continuity A149](docs/governance/RC6_DEPLOY_V2_CONTINUITY_20261028.md) before claiming ownership or deployment.
3. Read the `AGENTS.md` from that product branch.
4. Read that branch's `ops/policy/porota-policy.yaml`.
5. Read the latest release/handoff and machine-generated runtime evidence available on that branch / GitHub Actions.
6. Inspect current PRs/workstreams and ownership before choosing a writable scope.
7. Branch from the verified current product SHA for RC6 work unless a newer canonical integration base is explicitly proven.
8. Never continue from chat memory, an old checkpoint, or this file alone.

Before any new code **or tests**, complete a historical applicability checklist:
search current code, commits, open/closed PRs, issues, tests and Actions for an
equivalent implementation; record exact queries/links, SHA and findings. Classify
each requested item as implemented, partial, missing, superseded or not applicable,
using the evidence states below separately. Reuse verified work rather than
reimplementing it. Record why each proposed test applies to current behavior and
what real regression it detects. Absence from chat memory proves nothing.

Obtain and re-read fresh scope-specific WRITE_OWNER approval in
[#980 ownership ledger](https://github.com/mbalbo2023/Porota-trading/issues/980)
before writes, including tests; reconcile later releases/supersessions, overlapping
owners, exact branch/base SHA and paths. For main CI, use explicit single-line
`Workstream ID`, `Ownership status: ACTIVE`, `Work branch`, `Base SHA`, and
`Scope paths` fields in the owner-approved comment, plus `WRITE_OWNER`. A later
structured owner decision with the exact Workstream ID and explicit Ownership
status blocks that lease until reconciled; ordinary progress notes do not.
READ_ONLY remains nonblocking. Never claim perfect agent memory: fresh evidence
and fail-closed checks reduce omissions; unknowns stay NO_VERIFICADO.

Keep the current SHA/PR, Actions run, artifact/manifest/digest and runtime receipt
separate, timestamped and linked; say NO_VERIFICADO for unavailable layers. A
historical checklist, prose rule or PR-template checkbox is not technical CI.

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


## 12. Continuity and 100%-electronic validation update (A149, 28-Oct-2026 ART)

- Treat the A149 governance runbook as **historical process**, not as runtime truth; reconcile it with newest #1093 (or verified successor)/#624, protected product workflow and machine receipts before every action.
- Every meaningful incident must close the loop `ERROR → RCA → FIX → GUARD → TEST/CI/PREFLIGHT → EVIDENCE → DOC UPDATE`; if a permanent automated guard is impossible, document why and retain `NO_VERIFICADO`/`BLOQUEADO` as appropriate.
- The operator's objective is **100% electronic evidence coverage of the applicable acceptance criteria**, including SHA/tree/artifact/digest, code/tests, host/runtime, PAPER/0, DB, timers/workers, family readiness and UI when in scope. Never call unmeasured or impossible-to-observe criteria GREEN; market-OPEN events cannot be fabricated outside the session.
- Parallelize independent `READ_ONLY` evidence lanes (PAPER/SHADOW/scalping architecture, readiness by ten families, scheduler/IOL guardian, SQLite/cauciones/disk, QA/PR governance) without overlapping the single integration `WRITE_OWNER` or global `DEPLOY_OWNER`.
- A125 date-bound permission for 27-Oct cannot automatically authorize 28-Oct timer rearming; E09 host stress is cancelled / E10 not applicable. Exact #624 comments are per candidate SHA/tree. Preserve SHADOW dependencies and source freshness; do not conflate cauciones-accounting NO_GO with a scalping-algorithm failure.
- Docs-only governance changes belong on a non-deploy branch and must not promote, restart, or mutate the Droplet. Do not add these documentary changes to the live #752 candidate or its frozen artifact.

## 13. Technical enforcement boundary (R335)

The existing main checkpoint-attestation workflow validates declared metadata,
exact changed paths, pinned base/head and an owner-approved #980 lease against
the paginated ledger, rejecting overlapping structured ACTIVE leases of other
workstreams after reconciling their latest RELEASED/SUPERSEDED decisions.
It fails closed on later exact structured owner decisions or API/budget errors.
The authored workflow executes tests and its validator from the pinned trusted
base, not proposed source. The PR workflow itself remains modifiable; this is
not immutable workflow enforcement. Initial adoption uses the previous base
validator; new rules only execute after integration. Real Actions enforcement is
NO_VERIFICADO until the exact run and negative acceptance evidence are recorded.
Required-check branch protection and protection of the workflow itself require
separate verification; this PR does not establish them. Metadata cannot prove a
human performed the historical search, completeness, runtime correctness or
absence of all duplicate work. This is a timestamped ownership snapshot, not a
permanent lease lock: later #980 comments do not rerun the PR check. A human must
recheck ownership and overlapping scopes immediately before merge. Automatic
merge-time enforcement remains NO_VERIFICADO. Narrative policy and template
remain review duties.
