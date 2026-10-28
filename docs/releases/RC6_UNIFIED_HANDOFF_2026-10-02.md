# RC6 unified candidate — 2026-10-02

WORKSTREAM_ID: WS-INTEG-RC6-20261002. Scope: isolated integration only.
Exact inputs and exclusions: RC6_UNIFIED_INPUTS_2026-10-02.json.
PAPER/SHADOW ONLY. PPI Watch UNTOUCHED. FIX-FORWARD ONLY.

## Reconciliation and permanent guards
- Nine frozen PR heads are integrated against the live product base. Historical PRs #426–#431 are already absorbed and are not reapplied.
- rc6_contract_bridge.py has disjoint source hunks. Conflicting dashboard test additions are combined; obsolete macro tests are replaced by retirement tests while retaining the dashboard-absence guard.
- RCA: GDELT tombstones left active dashboard cards and an engine call. Fix: no active cards or imports; static retirement marker only. Guard: cross-layer regression; historical data remains intact.
- RCA: caucion live-book reader removed the direct pending-change check. Fix: material-aware append-only evidence recheck even while catalog still says READY; strict exact PPI book source. Guard: material/provenance race and unknown-source regressions.
- RCA: the sweep fee filter accepted only the old notional policy, rejecting the new live-bid policy after successful book wiring. Fix: accept both explicitly authorized ARS PAPER policy names, preserving fee authority, depth participation and freshness. Guard: synthetic book-to-sweep-to-real-PAPER-ledger regression, with durable daily recovery.
- RCA: zero-MAE rendering could match the zero prefix of a nonzero value; repeated STOP normalization duplicated explanations. Fix: numeric boundary and idempotent translation. Guard: explicit nonzero and repeat-normalization cases.

## Release gates
Builder focused tests are preliminary evidence only. The exact final PR HEAD must pass canonical full Predeploy V2 and freeze its artifact before promotion. Deployment must use a two-parent merge preserving candidate tree identity. The builder cannot access the droplet and cannot deploy.
One-off input workflows and these integration tools stay on the audit branch; the existing canonical Deploy V2 guard from #435 is preserved.

## Runtime still pending
No new deployment or live operational validation is asserted here. Check immediate and delayed runtime, mode, zero real orders, PPI Watch, services, images, disk, dashboard, and retired sources. Caucion/scalping market-window behavior remains NO_VERIFICADO until valid fresh market evidence; do not fabricate fills, extend liquidity windows or relax safeguards to produce activity.
Ownership/evidence tracker: issue #441. DEPLOY_OWNER must be claimed separately after exact Predeploy GREEN and fresh concurrency verification.
