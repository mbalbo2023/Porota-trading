# Deployment failure learning contract

Porota must learn from deployment failures in code and CI, not in chat memory.

For each new deployment failure class, record and automate these fields:

- `failure_signature`: stable machine-readable identifier.
- `first_seen_run`: GitHub Actions run ID.
- `symptom`: concise observed failure.
- `root_cause`: proven cause, not a guess.
- `detection_phase`: where it was discovered.
- `earliest_possible_detection_phase`: where it should be detected.
- `permanent_guard`: test/check/gate that prevents recurrence.
- `fix_forward_change`: the forward correction.
- `runtime_modified_before_failure`: boolean.
- `cleanup_verified`: boolean.

## Mandatory rule

If a failure can be reproduced from source plus the deployable artifact, it belongs in PRE-DEPLOY and must not first appear on the droplet.

## Seed incident: artifact dependency omitted

`failure_signature: ARTIFACT_DEPENDENCY_MISSING`

Observed on 2026-09-24: a runtime dependency existed in Git but was omitted by the deployment package, producing a `ModuleNotFoundError` only after deployment had begun.

Permanent architectural guard:
1. Build one complete deployable artifact.
2. Generate a manifest and hashes from that artifact.
3. Resolve/import-check runtime dependencies inside that exact artifact.
4. Run runtime smoke tests inside that exact artifact.
5. Only after all gates are green may the artifact be promoted.

Adding one forgotten filename to a manual package list is not considered a permanent fix.

## Fix-forward invariant

Runtime rollback is forbidden. A failed candidate must either:
- fail before promotion and leave the healthy runtime untouched; or
- if already promoted, be corrected by a new forward candidate.

No old runtime image becomes the deployment target as a failure response.
