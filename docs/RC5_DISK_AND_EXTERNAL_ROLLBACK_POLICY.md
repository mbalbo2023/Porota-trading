# POROTA TRADING — RC5 DISK CAPACITY AND EXTERNAL ROLLBACK POLICY

Date: 2026-09-05
Base release: `17.0.0-rc5`
Base commit: `852b812d610860b57b579212978443f7450e8855`

## Objective

Keep the Droplet lean and reproducible without sacrificing operational safety.

The Droplet is not the long-term store for previous application images. Rollback application images must live in GitHub-hosted immutable storage and be recoverable by exact commit/digest.

## Non-negotiable safety invariants

- `MODE=PRODUCTION_PAPER`
- `EXECUTION=SIMULATED`
- `REAL_ORDER_CAPABILITY=BLOCKED`
- `PPI_ORDERS=BLOCKED`
- `REAL_ORDERS_SENT=0`
- No network order tests.
- No `docker system prune`.
- No deletion of active data, Contract Evidence, history, audit evidence or the active release image.
- No cleanup may be treated as GREEN without preflight/postflight evidence.

## Droplet storage policy

Keep locally:

1. The single active POROTA application image.
2. Active runtime data under `/opt/porota-trading/data`.
3. The canonical predeploy SQLite backup required for transactional recovery of the active rollout.
4. PAPER daily backups according to the approved retention policy.
5. Contract Evidence, history, audit trail and operational reports according to their own retention contracts.
6. Required browser/runtime assets that are still referenced by Contract Evidence or authenticated research.

Do not retain merely for rollback:

- old POROTA application images;
- old release staging trees;
- failed-release build contexts;
- obsolete forensic copies after their evidence is captured;
- package-manager/pip caches when they are no longer required.

## Disk thresholds

- Hard predeploy minimum free space: **8 GiB**. Existing RC5 deploy gate remains fail-closed below this value.
- Operational warning target: **10 GiB free**. Falling below this value requires capacity review before the next release/build.
- Critical: **8 GiB free or lower**, or evidence that projected growth can cross the hard gate before the next business session.

These thresholds are host-capacity controls. They do not authorize destructive cleanup.

## Cleanup rules

Cleanup must be targeted and evidence-driven.

Allowed only after identity/reference checks:

- an old application image not referenced by any container;
- obsolete staging directories not referenced by Docker, systemd, mounts, symlinks or open processes;
- explicitly identified redundant predeploy backups after a canonical backup is selected and verified;
- obsolete forensic copies that are integrity-checked and unreferenced;
- regenerable apt/pip caches;
- build cache only when Docker reports it reclaimable and cleanup cannot remove the active image.

Never use `docker system prune`.

## External application rollback

POROTA rollback images must be published to GitHub Container Registry (GHCR) from an exact audited source commit.

Required identity for every archived rollback image:

- exact Git commit SHA;
- exact POROTA version;
- immutable GHCR digest;
- `porota.commit=<sha>` image label;
- release manifest containing image reference/digest and source commit;
- checksum/evidence produced by the publishing workflow.

A rollback must never rely on `docker run --pull never` finding an old image that happens to remain on the Droplet.

## External rollback flow

1. GitHub Actions selects an explicitly supplied audited target SHA/version.
2. The target source is checked out exactly.
3. Static release identity and safety invariants are validated.
4. The image is pulled from GHCR **by digest**, not by mutable tag.
5. GitHub Actions transfers the verified image to the Droplet over the existing strict SSH channel.
6. The Droplet loads and tags the image with the exact local version expected by that source tree.
7. Before activation, the runtime validates current PAPER safety and creates/validates the required SQLite backup.
8. Activation is transactional.
9. Postflight requires healthy observer/dashboard, DB quick checks, correct mode and `real_orders_sent=0`.
10. Failure is fail-closed. No real-money capability is introduced.

## Important database rule

An application-image rollback and a database rollback are separate concerns.

The external image archive does not by itself prove that an older application is schema-compatible with the current database. Before activating an older application, the rollback workflow must either:

- prove schema compatibility; or
- restore the appropriate canonical predeploy database backup transactionally.

No automatic downgrade of a live SQLite schema is permitted without evidence.

## Release lifecycle

For each future release:

1. validate candidate;
2. publish exact rollback/current image to GHCR and capture digest;
3. confirm external artifact exists before activating the candidate;
4. perform deploy with normal preflight/backup/postflight/rollback gates;
5. after successful activation and soak, remove the previous local application image when it is unreferenced;
6. retain rollback capability externally by digest.

## RC5 measured baseline after cleanup

Measured 2026-09-05 after targeted cleanup:

- root filesystem: ext4, ~24.88 GB total;
- used: ~12.18 GB;
- free: ~12.69 GB (~11.82 GiB);
- utilization: 49%;
- active image: `porota-trading-bot:17.0.0-rc5`;
- observer/dashboard running with restart count 0;
- observer and history DB quick checks `ok`;
- `REAL_ORDERS_SENT=0`.

This baseline is evidence, not a future-state assumption. Runtime must always be re-verified before changes.
