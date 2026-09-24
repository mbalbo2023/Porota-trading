# Artifact promotion / registry design — 2026-09-24

Status: DESIGN_ONLY — no production deployment changes.

## Decision

Porota Deploy V2 should reuse the already-proven GitHub Container Registry concept as the immutable transport for the exact image tested by CI.

Target flow:

```
SOURCE
  -> BUILD ONCE IN CI
  -> TEST EXACT LOCAL IMAGE
  -> POLICY / SECRET / ARTIFACT / IMPORT / FULL TEST GATES
  -> TAG SAME IMAGE FOR GHCR
  -> PUSH SAME IMAGE
  -> CAPTURE sha256 DIGEST
  -> RE-PULL/VERIFY DIGEST IN CI
  ================== PROMOTION GATE ==================
  -> DROPLET PULLS ghcr.io/...@sha256:<digest>
  -> START CANDIDATE FROM THAT DIGEST
  -> HEALTH / PRODUCTION_PAPER / REAL_ORDERS_SENT=0
  -> PROMOTE
  -> CURRENT_STATE
  -> SAFE CLEANUP
```

There must be **no second Docker build** on the droplet.

## Historical proof available in the repository

The historical workflow `.github/workflows/rc6-ghcr-archive-proof.yml` already demonstrated the useful part of this design:
- GHCR login with scoped GitHub token;
- immutable SHA tag;
- push to `ghcr.io/mbalbo2023/porota-trading`;
- capture `sha256` digest;
- remove local tag;
- re-pull exact digest;
- verify the `porota.commit` label and PAPER safety identity;
- publish manifest evidence.

That concept is reusable.

## Explicitly rejected historical behavior

The older `rc6-final-transactional-deploy.yml` also contains:
- host-side Docker rebuild;
- rollback functions;
- rollback rehearsal;
- pre-deploy rollback-oriented state preservation.

Those parts are **NOT** reusable in Deploy V2.

Current Porota policy is:
- `FIX_FORWARD_ONLY = true`
- `ROLLBACK_ALLOWED = false`

A candidate that fails before promotion leaves the healthy runtime untouched. A defect found after promotion is corrected by a new forward candidate.

## Promotion identity

The production identity must be the image digest, not only a mutable tag:

```
git_sha       = <40 hex>
image_digest  = sha256:<64 hex>
mode          = PRODUCTION_PAPER
real_orders   = 0
```

Observer and dashboard must report the same digest.

## Cleanup

After each attempt, safely remove:
- unused candidate tags/images;
- safe BuildKit/build cache;
- staging/temp artifacts;
- stopped deployment containers.

Never remove:
- active containers;
- referenced runtime image/digest;
- databases;
- data volumes/bind mounts;
- PPI Watch assets.

## AWS clarification

AWS is **not part of Porota's architecture** and is not being proposed.

The string `AWS_ACCESS_KEY` appears only as one generic credential signature in the public-repository secret scanner, alongside GitHub, Google, Slack, Telegram and private-key signatures. Its purpose is defensive: if any such credential were accidentally pasted into tracked source, CI should reject it. It creates no AWS dependency, account, service or deployment path.

## Security follow-up

Before calling public-repository secret hygiene complete:
1. extend the current-tree scanner with Porota-specific sensitive fields such as dashboard token, Telegram chat identifier and IOL credential/session fields;
2. perform a dedicated Git-history secret scan;
3. if historical live credentials are discovered, rotate them rather than relying on deletion from the current tree.
