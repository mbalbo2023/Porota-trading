---
name: porota-docker-image-retention
description: Prevent and safely clean repeated RC6 candidate and rollback Docker image accumulation on the Porota Trading Droplet. Use when inspecting, changing, deploying, or cleaning Docker images for this project.
---

# Porota RC6 Docker image retention

Use this skill only for the Porota Trading RC6 production Docker workflow and its image storage. Start from the latest RC6 checkpoint and verify the current canonical branch, deployed SHA, latest workflow run, and host image inventory before any cleanup.

## Required retention policy

- Production stays on `porota-trading-bot:17.0.0-rc6`; keep that stable tag and every image ID referenced by any running or stopped container.
- The user's standing preference rejects a per-deployment Docker rollback image. Do not create a new `rollback-<SHA>` image tag unless the user explicitly changes that preference.
- Treat `candidate-<SHA>` as a temporary build alias, not as a retained production image or rollback point. After promotion, remove the candidate tag when it resolves to the stable image ID; otherwise remove it only when no running or stopped container references its image ID. If a container references a distinct candidate image ID, keep it and report the exact container reference. Remove older RC6 candidate and legacy rollback tags only when their image IDs are not referenced by containers.
- Scope deletion to exact refs matching `porota-trading-bot:17.0.0-rc6-candidate-<40 lowercase hex>` and `porota-trading-bot:17.0.0-rc6-rollback-<40 lowercase hex>`. Do not broaden the pattern or use `force`.
- Never run global `docker system prune`, `docker image prune`, or `docker builder prune` as part of this policy. Do not touch build cache, volumes, containers, databases, backups, ChromaDB, PPI Watch, or the private cockpit on port 8766.

## Deployment workflow guard

The only RC6 production path is the canonical workflow on `deploy/rc6-pr69-isolated-20260915`:

`.github/workflows/rc6-pr69-isolated-transactional-deploy-20260915.yml`

When editing it:

1. Keep candidate cleanup inside the remote deployment script as an EXIT trap. It must run after a Docker build has started even if the later deployment or verification fails; a red overall Actions result can still have created and tagged an image.
2. Before removing a tag, inventory all running and stopped containers and protect every referenced image ID. Inventory failure must fail closed and remove nothing.
3. Preserve the stable production tag and every image ID referenced by a running or stopped container. The candidate tag is temporary: remove it after promotion when it aliases the stable image ID, or when no container references its image ID. Retain a distinct candidate image only while a container references that image ID. Remove only unused allowlisted candidate/rollback tag refs. Do not delete containers to make their images reclaimable.
4. Emit before/after `docker system df`, each removed ref, each protected ref, and a clear cleanup result. A cleanup failure after an otherwise successful deployment must make the workflow visibly fail.
5. Keep the existing PAPER checks intact: `PRODUCTION_PAPER`, `SIMULATED`, `real_orders_sent=0`, and no real order route.
6. Use an isolated branch and narrow PR. A change merged to the canonical branch triggers production deployment. Keep skills and checkpoints out of that branch; store them in Library or a non-deployment documentation branch.

## Cleanup procedure

- Prefer the canonical workflow's allowlisted cleanup trap for live host changes. Do not create a second ad hoc cleanup path when the canonical one can do the work.
- Compare the fresh pre-cleanup inventory with the post-cleanup inventory from the same run. Confirm the stable image and all container-referenced IDs remain; confirm only allowlisted unused tags disappeared.
- Report actual Docker accounting before and after. Explain that Docker's logical image sizes share layers and are not a simple sum of physical disk use.
- If a candidate/rollback image is still container-referenced, leave it in place and report the exact reason. Do not stop or remove its container as part of image cleanup.
- If the inventory is stale or the host changed since it was collected, repeat the read-only inventory before deletion.

## Continuity record

After a material image cleanup or workflow change, update the latest RC6 checkpoint with the branch/commit, PR, workflow run, before/after image totals and disk use, cleanup result, active image identity, container-reference exceptions, PAPER safety state, and remaining unknowns. Verify the checkpoint artifact after saving it.


## Explicit legacy RC6 tag allowlist

The canonical workflow may also remove these known pre-SHA legacy refs when no container references their image ID:

- `porota-trading-bot:17.0.0-rc6-pr69-<12 lowercase hex>`
- `porota-trading-bot:17.0.0-rc6-strategy-<12 lowercase hex>`
- `porota-trading-bot:17.0.0-rc6-eod-only-<12 lowercase hex>`
- `porota-trading-bot:17.0.0-rc6-p0-eod-<12 lowercase hex>`
- `porota-trading-bot:17.0.0-rc6-f01-<12 lowercase hex>`
- `porota-trading-bot:17.0.0-rc6-eod-ui-<12 lowercase hex>`
- `porota-trading-bot:17.0.0-rc6-scalping-<7 lowercase hex>`
- Exact tag `porota-trading-bot:17.0.0-rc6-candidate-e47eeffc`.

These are allowlist patterns based on the observed legacy naming scheme, not permission to broaden deletion to other repositories, tags, containers, or Docker data. Preserve every image ID referenced by any container, including exited/created containers. The workflow must enumerate and protect those IDs before each removal and fail closed if inventory fails. Do not remove build cache: in the 2026-09-19 recheck it occupied 2.882 GB logically while only 8.188 MB was reclaimable.

The 2026-09-19 canonical cleanup removed eight unused refs and reduced Docker's reported reclaimable image space from 117 MB to 0 B. Exact disk free after cleanup was 6,785,351,680 bytes on `/dev/vda1`. These figures are a historical checkpoint; always take a fresh read-only measurement before deciding on future cleanup.
