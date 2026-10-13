# CHECKPOINT — WS-DISK-07 HOUSEKEEPING POLICY — 2026-10-01

## Ownership
- WORKSTREAM_ID: WS-DISK-07-HOUSEKEEPING-POLICY
- mode: WRITE_OWNER
- branch: work/ws-disk-07-housekeeping-policy-20261001
- base product SHA: 5912d02bd8bdf9f0acfbbeaaf0a605fe5b5462e6
- target product branch: deploy/rc6-pr69-isolated-20260915
- no direct write to product branch
- PAPER/SHADOW ONLY
- PPI Watch untouched
- FIX-FORWARD ONLY

## Implemented candidate
- policy: ops/policy/rc6-disk-housekeeping-v1.json
- guarded engine: scripts/rc6_disk_housekeeping.py
- deploy calculator/guard: scripts/rc6_disk_space_guard.py
- canonical Deploy V2 pre-transfer disk gate inserted before first remote mutation/transfer
- Transfer step requires RC6_DISK_PREFLIGHT_GREEN=1
- final/preopen disk floors derive from policy
- PAPER backup retention: newest 3
- historical backup retention: newest 3
- general backups: newest 3 + explicit pinned checkpoint(s)
- tmp: porota*/rc6* older than 7 days
- journald cap: 512 MiB
- APT cache/lists: cleanup permitted
- Docker: OBSERVE_ONLY
- SQLite VACUUM/WAL deletion: forbidden automatically
- containerd manual mutation: forbidden
- swap: untouched

## Disk thresholds
Current admission follows the operator-authorized dynamic policy of 2026-10-01.
The initial 6 GiB floor and 5 GiB reserve were superseded. See
`ops/policy/rc6-disk-housekeeping-v1.json`, aligned
`ops/policy/porota-policy.yaml`, and `docs/runbooks/RC6_CONVERGENCE_SRE.md`.

- GREEN target: 7 GiB
- automatic cleanup below: 7 GiB
- pre-transfer absolute floor: 0; admission uses the artifact-specific formula
- pre-transfer required free: image_tar + exact_image_unpacked + 2*bundle + 2 GiB
- post-cleanup residual minimum: 2 GiB
- CRITICAL: below 1 GiB
- inode free minimum for deploy: 10%

Canonical promotion removes only unreferenced RC6 candidate tags through
`scripts/rc6_deploy_scoped_cleanup.py`. Stable/current images, running and stopped
container references, and explicit image pins are retained. Shared Docker build
caches are untouched; global prune and PPI Watch inspection are absent.

## Regression guards
- exact retention values asserted by tests
- backup pin preservation asserted
- tmp prefix + age selection asserted
- disk threshold bands asserted
- automatic SQLite/containerd mutation forbidden
- unique docker-load deploy route asserted
- pre-transfer disk guard ordering asserted
- transfer cannot run without preflight GREEN asserted
- Predeploy V2 policy gate asserts disk policy

## Remote workflow inventory
Temporary Actions inventory found historical/legacy remote-capable workflows.
The full immutable-image docker-load promotion route remains unique:
.github/workflows/porota-deploy-v2-promote.yml

Legacy one-off workflows are not promoted to canonical status by this change.
New/full docker-load promotion routes are rejected by regression unless canonical and disk-guarded.

## Remaining before merge/deploy
1. open PR to product branch;
2. exact Predeploy V2 GREEN;
3. verify frozen artifact includes policy/scripts;
4. merge only if product HEAD unchanged and no DEPLOY_OWNER exists;
5. let single Deploy V2 run under rc6-unified-paper-deploy;
6. validate pre-transfer disk gate + runtime;
7. add default-branch scheduler that checks out the deployed product branch and runs the exact deployed housekeeping engine.
