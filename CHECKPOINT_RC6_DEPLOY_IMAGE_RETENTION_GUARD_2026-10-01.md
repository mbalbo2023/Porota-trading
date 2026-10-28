# CHECKPOINT — RC6 DEPLOY IMAGE RETENTION GUARD — 2026-10-01

## Ownership
- WORKSTREAM_ID: WS-DISK-IMAGE-RETENTION-GUARD-20261001
- mode: WRITE_OWNER
- base product SHA: 136453dc5b6d4146057142bcdd76b2f6b8d474e0
- branch: fix/rc6-deploy-image-retention-guard-20261001
- scope: .github/workflows/porota-deploy-v2-promote.yml + tests/test_rc6_disk_deploy_guard.py
- no runtime mutation
- no deploy
- PAPER/SHADOW ONLY
- PPI Watch untouched

## Incident evidence
The post-deploy cleanup used docker image prune without -a, while critical_approval remained pinned to the previous stable image. This preserved two old generations:
1. the previous active image retained by critical_approval;
2. a tagged but unused candidate image.

Manual guarded cleanup on 2026-10-01 recovered 4.375 GB total and restored free space to 8.824 GB.

## Permanent guard
Deploy V2 must:
1. verify the new stable image can import the critical approval runtime;
2. recreate only critical_approval on the already-promoted stable image when its image ID differs;
3. verify critical approval health/issues-only capability;
4. remove only the now-unreferenced previous critical image;
5. remove tagged rc6 candidate images only when no container references their image ID;
6. retain dangling-only prune for all other images;
7. preserve observer/dashboard identity, PAPER invariants and PPI Watch.
