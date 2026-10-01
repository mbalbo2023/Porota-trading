# CHECKPOINT — RC6 DEEP DISK CLEANUP — 2026-10-01

## Ownership
- WORKSTREAM_ID: WS-DISK-DEEP-CLEANUP-20261001
- mode: DEPLOY_OWNER
- base product SHA: 136453dc5b6d4146057142bcdd76b2f6b8d474e0
- branch: ops/rc6-disk-deep-cleanup-20261001
- scope: runtime disk cleanup only
- PAPER/SHADOW ONLY
- real_orders_sent=0 required
- PPI Watch untouched
- FIX-FORWARD ONLY
- no product code deploy

## Authorized runtime action
1. Verify observer/dashboard use the current stable image.
2. If critical_approval is still pinned to the previous image, restart only porota-critical-approval-rc6.service so it is recreated from the existing stable tag.
3. Verify control-plane health, PAPER invariants, observer/dashboard identity and PPI Watch.
4. Remove only the now-unreferenced previous image; never remove an image used by any container.
5. Run canonical rc6_disk_housekeeping.py cleanup.
6. Prune only safe Docker/build cache and measure disk before/after.
7. Revalidate runtime and report final free space.

## Explicit exclusions
- no SQLite VACUUM/WAL deletion
- no volumes
- no market/history DB deletion beyond canonical backup retention policy
- no observer/dashboard restart
- no PPI Watch mutation
- no real-order capability
- no rollback
