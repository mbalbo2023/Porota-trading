# Workflow Usage Audit — 2026-09-24

## Method

Read-only sample of the most recent 1,000 GitHub Actions runs.

Sample window:
- newest: 2026-09-24T15:40:58Z
- oldest: 2026-09-16T23:16:52Z

The deployed operational branch currently contains **170 workflow files**. The 1,000-run sample contains **114 distinct workflow paths across all branches**, but only **7 of those paths intersect the 170 workflows currently present in the deployed branch**.

Therefore **163 of the 170 workflows present in the deployed branch have no execution evidence inside this 1,000-run / ~8-day sample**. This is NOT permission to delete them; it is a strong classification signal for the next inventory stage. The earlier 56-workflow estimate was incorrect because it did not intersect sampled workflow paths with the current deployed workflow tree.

## Concentration

The three highest-volume workflows account for **652 / 1,000 sampled runs (65.2%)**:

| Workflow | Runs | Success | Failure | Cancelled |
|---|---:|---:|---:|---:|
| RC6 unified candidate tests | 242 | 211 | 30 | 0 |
| RC6 PR candidate preflight | 218 | 206 | 12 | 0 |
| RC6 unified Paper deploy | 192 | 67 | 121 | 3 |

The canonical Paper deploy failed **121 of 192 sampled executions (~63.0%)**. This is far above an acceptable steady-state deployment failure rate and confirms that architectural correction is required rather than more one-off patches.

## Other high-activity workflows

- Alpha Tuning Readonly Trade Audit: 33 runs
- Candle intramarket evidence tests: 27
- Live Decision Cockpit tests: 23
- IOL shadow observation tests: 17
- Temporary history writer identity audit: 15
- GDELT market relevance checks: 14
- IOL runtime read-only audit: 13
- GDELT runtime read-only: 12
- CI: 10

## Signals of workflow proliferation

The current workflow directory includes:
- 170 workflow files total
- 164 RC6-named
- 32 filenames containing `deploy`
- many date-stamped, `temp`, `audit`, `diagnostic`, `proof`, `hotfix` and one-purpose workflows

High historical run count alone does not imply KEEP. A workflow may have been intensely used during one incident and now be obsolete.

## Classification model

Every workflow must eventually receive exactly one classification:

### KEEP
Permanent responsibility with current evidence and no overlap.

### CONSOLIDATE
Useful responsibility, but should become a mode/parameter in a permanent control-plane workflow.

### ARCHIVE
Historical evidence worth retaining outside the active Actions surface.

### DELETE
No active responsibility, no unique evidence requirement, and superseded by a canonical workflow/script.

No workflow is deleted until:
1. trigger dependencies are checked;
2. references from other workflows/scripts/docs are checked;
3. last execution and caller are identified;
4. replacement path is documented;
5. protected production behavior is unaffected.

## Immediate recommendations

1. Keep the existing production workflow unchanged until Predeploy V2 is proven.
2. Treat `Porota Predeploy V2` as the candidate permanent pre-promotion gate.
3. Replace manual test lists with automatic discovery.
4. Move reusable logic from YAML heredocs to tested scripts.
5. Consolidate read-only diagnostics behind one parameterized control-plane workflow.
6. Consolidate storage diagnostics/cleanup behind one safe allowlisted workflow/script.
7. Stop creating new date-stamped workflows for incidents unless no permanent control-plane operation can express the task.

## Current status

- Production changes from this audit: **NONE**
- Workflow deletions: **NONE**
- Workflow disables: **NONE**
- Branch deletions: **NONE**
- PPI Watch changes: **NONE**


## Exact current-branch intersection

Only these 7 workflows currently present in the deployed branch appear in the sampled 1,000 runs:

| Workflow | Runs | Success | Failure | Cancelled |
|---|---:|---:|---:|---:|
| RC6 unified candidate tests | 245 | 215 | 30 | 0 |
| RC6 PR candidate preflight | 212 | 200 | 12 | 0 |
| RC6 unified Paper deploy | 192 | 67 | 121 | 3 |
| RC6 candle intramarket evidence tests | 27 | 17 | 10 | 0 |
| RC6 IOL shadow observation tests | 17 | 11 | 6 | 0 |
| CI — Porota Trading | 10 | 10 | 0 | 0 |
| RC6 IOL shadow runtime tests | 2 | 0 | 2 | 0 |

This does not prove the other 163 are safe to delete. It proves they need explicit dependency/reference classification before remaining in the active workflow tree.

## Default-branch divergence

The default branch `main` currently contains only **6 workflow files**, while the deployed operational branch contains **170**.

Two main-branch workflows, `deploy.yml` and `promote-to-production.yml`, are legacy manual production mechanisms. The former still checks `ENVIRONMENT=PRODUCTION`, uses the old `/home/tradingbot/app` path and rebuilds on the server. The latter can merge `testing/develop` into `main` and create production tags. Neither appeared in the sampled 1,000 recent runs.

Two additional main-branch web probe workflows are hard-coded to execute only on **2026-09-14**, making them historical/no-op candidates as of 2026-09-24.

These four workflows are strong ARCHIVE/DELETE candidates after reference/dependency verification. No removal has been performed.

## Dependency reproducibility debt

The deployed `requirements.txt` contains 24 dependency declarations:
- 4 exact pins using `==`
- 3 bounded ranges
- 17 minimum-only / compatible lower-bound declarations

The Docker base is `python:3.11-slim` without a source digest pin.

Therefore two Docker builds of the same Porota Git SHA can resolve different dependency/base-image bytes at different times. Deploy V2 should eventually separate production and development dependencies and introduce a reproducible lock/digest strategy. This is P1 after the predeploy architecture is stable.
