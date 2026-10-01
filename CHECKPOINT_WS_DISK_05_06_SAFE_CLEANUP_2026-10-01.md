# CHECKPOINT — WS-DISK-05/06 SAFE CLEANUP — 2026-10-01

## Ownership
- WORKSTREAM_ID: WS-DISK-05-SAFE-CLEANUP / WS-DISK-06-BACKUP-RETENTION
- branch: cleanup/ws-disk-05-safe-cleanup-20261001
- base product SHA: 5912d02bd8bdf9f0acfbbeaaf0a605fe5b5462e6
- mode during runtime mutation: DEPLOY_OWNER
- DEPLOY_OWNER: RELEASED
- PAPER/SHADOW ONLY
- PPI Watch untouched
- no product branch mutation
- no deploy/restart/systemd/DB/Docker/containerd/swap mutation

## Audit evidence
Read-only capacity runs:
- 36865081924 GREEN
- 36865512369 GREEN
- 36865736493 GREEN
- 36865984123 GREEN

Entry state:
- free bytes: 5,280,256,000
- filesystem: ~79% used
- PRODUCTION_PAPER
- real_orders_sent=0
- active image sha256:18c9b101665f8a685eeffca4291b5adb220cd22cee0492205760e9406621f3e0

## Phase 1
Run 36867726331 performed the intended cleanup but concluded FAILURE only because a post-cleanup free-space assertion required 6 GiB exact.

Effective cleanup:
- deleted 824 /tmp entries with names starting porota/rc6 older than 7 days;
- /tmp: 670,400,512 -> 149,102,592 bytes;
- apt lists: 210,399,232 -> 12,288 bytes;
- apt cache: 123,576,320 -> 49,152 bytes;
- journald: 736.1M -> 494.7M;
- free disk: 5,280,256,000 -> 6,388,596,736 bytes;
- recovered: 1,108,340,736 bytes;
- runtime/PAPER/image/restart/PPI Watch guards all passed.

RCA:
- cleanup itself was correct;
- the 6 GiB exact assertion was stricter than the established operational floor and failed by ~54 MB.

FIX/GUARD:
- corrected post-cleanup guard to a conservative 5 GiB floor;
- validation-only run 36867957071 GREEN;
- validated free bytes 6,388,498,432, PRODUCTION_PAPER, real_orders_sent=0, three critical containers running on exact image, PPI Watch unchanged.

## Phase 2 — conservative backup retention
Run 36868157147 GREEN.

Policy:
- general backups: keep newest 3 + oldest checkpoint pinned;
- PAPER backups: keep newest 7;
- history backups: keep newest 7;
- Wave4 closure backup untouched.

Deleted:
- 13 backup files;
- candidate bytes: 1,728,035,956;
- actual recovered bytes: 1,728,102,400.

Preserved general backups:
- 2026-10-01
- 2026-09-29
- 2026-09-28
- pinned 2026-09-12 checkpoint

Preserved PAPER backups:
- 2026-09-30 through 2026-09-24 (7 newest).

Preserved history backups:
- 2026-09-30 through 2026-09-24 (7 newest).

Final runtime state:
- free bytes: 8,116,584,448
- PRODUCTION_PAPER
- real_orders_sent=0
- real routes NOT_CALLED
- observer running, OOM=false, restart count unchanged at 1
- dashboard running, OOM=false, restart count 0
- critical approval running, OOM=false, restart count 0
- exact active image unchanged
- PPI Watch untouched
- Wave4 evidence backup untouched

## Net result
From entry free bytes 5,280,256,000 to 8,116,584,448:
- net recovered: 2,836,328,448 bytes by endpoint measurement.
- sum of per-phase recovered deltas: 2,836,443,136 bytes.
The small difference is expected live filesystem drift during normal runtime writes.

## Deferred / not touched
- Docker image/containerd content
- BuildKit cache marked reclaimable=0B
- active SQLite DBs
- SQLite VACUUM (freelist=0)
- swapfile
- PPI Watch
- evidence stores
- Wave4 closure backup
- current operational data

## Follow-up
Recommended permanent prevention, not yet merged to product:
- formalize retention policy;
- bounded /tmp stale cleanup;
- journald size cap;
- alert before disk crosses agreed reserve floor.
