# Workflow Usage Audit — 2026-09-24

## Method

Read-only sample of the most recent 1,000 GitHub Actions runs.

Sample window:
- newest: 2026-09-24T15:40:58Z
- oldest: 2026-09-16T23:16:52Z

Repository currently contains **170 workflow files**. The 1,000-run sample contains evidence for **114 distinct workflow paths**.

Therefore **56 workflow files have no execution evidence inside this 1,000-run / ~8-day sample**. This is NOT permission to delete them; it is a classification signal for the next inventory stage.

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
