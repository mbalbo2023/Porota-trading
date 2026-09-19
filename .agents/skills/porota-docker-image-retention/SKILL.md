---
name: porota-docker-image-retention
description: Prevent and safely clean repeated RC6 candidate and rollback Docker image accumulation on the Porota Trading Droplet. Use when inspecting, changing, deploying, or cleaning Docker images for this project.
---

# Porota RC6 Docker image retention

Use this skill only for the Porota Trading RC6 production Docker workflow and its image storage. Start from the latest RC6 checkpoint and verify the current canonical branch, deployed SHA, latest workflow run, and host image inventory before any cleanup.

## Required retention policy

- Production stays on `porota-trading-bot:17.0.0-rc6`; keep that stable tag and every image ID referenced by any running or stopped container.
- The user's standing preference rejects a per-deployment Docker rollback image. Do not create a new `rollback-<SHA>` image tag unless the user explicitly changes that preference.
- Keep only the current deployment's `candidate-<SHA>` tag for traceability. Remove older RC6 candidate and legacy rollback tags only when their image IDs are not referenced by containers.
- Scope deletion to exact refs matching `porota-trading-bot:17.0.0-rc6-candidate-<40 lowercase hex>` and `porota-trading-bot:17.0.0-rc6-rollback-<40 lowercase hex>`. Do not broaden the pattern or use `force`.
- Never run global `docker system prune`, `docker image prune`, or `docker builder prune` as part of this policy. Do not touch build cache, volumes, containers, databases, backups, ChromaDB, PPI Watch, or the private cockpit on port 8766.

## Deployment workflow guard

The only RC6 production path is the canonical workflow on `deploy/rc6-pr69-isolated-20260915`:

`.github/workflows/rc6-pr69-isolated-transactional-deploy-20260915.yml`

When editing it:

1. Keep candidate cleanup inside the remote deployment script as an EXIT trap. It must run after a Docker build has started even if the later deployment or verification fails; a red overall Actions result can still have created and tagged an image.
2. Before removing a tag, inventory all running and stopped containers and protect every referenced image ID. Inventory failure must fail closed and remove nothing.
3. Preserve the stable production tag and the current SHA candidate. Remove only unused allowlisted candidate/rollback tag refs. Do not delete containers to make their images reclaimable.
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