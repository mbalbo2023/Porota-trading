# Deploy V2 — progressive architecture plan

Baseline: successful PAPER deploy run `36076749369`, SHA `c81f8d7c089433e89f3d664b57d3a9b915565419`.

This document defines a progressive migration. It does not change runtime by itself.

## Target pipeline

```
SOURCE
  -> POLICY
  -> COMPILE / STATIC CHECKS
  -> DEPENDENCY + IMPORT CLOSURE
  -> BUILD ONCE
  -> ARTIFACT MANIFEST + DIGEST
  -> TEST EXACT ARTIFACT
  -> RUNTIME SMOKE IN CI
  -> PAPER SAFETY
  =========================
         PROMOTION GATE
  =========================
  -> TRANSFER/PULL EXACT ARTIFACT
  -> START CANDIDATE
  -> HEALTH
  -> PROMOTE
  -> VERIFY STATE MANIFEST
  -> SAFE CLEANUP
```

## Design requirements

1. The deployable artifact is the only promoted unit.
2. The exact artifact tested is the exact artifact deployed.
3. No second `docker build --no-cache` is permitted merely to verify provenance.
4. No manually curated partial Python module list may define runtime completeness.
5. Observer and dashboard must identify the same Git SHA and image digest.
6. A candidate that fails before promotion must not disturb the healthy runtime.
7. Runtime rollback is forbidden; corrections are fix-forward.
8. Safe Docker/cache/temp cleanup is mandatory after every attempt.
9. Deployment output must end with one unambiguous state:
   - `SUCCESS`
   - `FAILED_PREDEPLOY_NO_RUNTIME_CHANGE`
   - `FAILED_FIX_FORWARD_REQUIRED`
10. Every failure class gets a permanent regression guard.

## Progressive phases

### Phase 0 — frozen baseline
Do not refactor production runtime while governance is introduced.

### Phase 1 — governance and observability
Add policy, bootstrap instructions, failure taxonomy, state schema, and read-only audits.

### Phase 2 — pre-deploy gate
Create an isolated CI gate that builds the candidate and validates dependency/import/runtime closure without deployment.

### Phase 3 — build once
Produce one immutable candidate image/artifact with digest and manifest. Test it completely in CI.

### Phase 4 — promotion
Change the production deploy to consume the already-tested artifact. Remove rebuild-on-droplet verification.

### Phase 5 — consolidation
Classify existing workflows and branches as KEEP / CONSOLIDATE / ARCHIVE / DELETE before removing anything.

## Parallel work policy

Parallel development is the default when independent workstreams do not share mutable runtime resources.

Convergence is mandatory:

`N PARALLEL WORKSTREAMS -> INDIVIDUAL VALIDATION -> RECONCILIATION -> ONE FROZEN CANDIDATE -> ONE FINAL PREDEPLOY V2 -> BUILD ONCE -> ONE IMMUTABLE DIGEST -> ONE CONSOLIDATED DEPLOY -> CLEANUP -> CURRENT_STATE`

After candidate freeze, unrelated new changes move to the next batch. Runtime/systemd/DB/promotion remain serialized.

Safe to parallelize:
- read-only workflow inventory
- branch inventory
- failure taxonomy
- dependency analysis
- security/secret scanning design
- artifact-manifest design
- documentation/governance

Must remain serialized:
- changes to the canonical production deploy workflow
- promotion to the protected deploy branch
- Docker cleanup that can affect shared runtime resources
- systemd/runtime mutation
- database migrations
- any operation involving PPI Watch (which remains out of scope)
