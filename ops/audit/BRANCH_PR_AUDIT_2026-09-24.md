# Branch / Pull Request Audit — 2026-09-24

## Current snapshot

- Branches: **589**
- Protected branches: **1**
  - `deploy/rc6-pr69-isolated-20260915`
- Open pull requests: **12**
- Branches represented by an open PR: **12**

Largest branch families:

| Prefix | Count |
|---|---:|
| fix | 179 |
| audit | 62 |
| feature | 42 |
| work | 35 |
| diag | 33 |
| ops | 28 |
| hotfix | 21 |
| feat | 18 |
| checkpoint | 16 |
| candidate | 15 |
| deploy-prep | 14 |
| docs | 14 |
| integration | 14 |
| deploy | 12 |
| codex | 12 |

There are many additional low-volume historical families, including release, release-candidate, wave branches and one-off diagnostic/control branches.

## Open PR signal

Only **12 / 589 branches (~2.0%)** are currently represented by an open PR.

Open PRs span several generations of architecture:
- current governance / Deploy V2 work
- current RC6 work
- older Wave A work
- legacy main-based cleanup
- RC5 dispatcher work

The existence of an open PR is not proof that the work is still active. Several PRs are dated 2026-09-05 through 2026-09-15 and should be reviewed as historical/superseded before any close/delete action.

## Cleanup rules

No bulk branch deletion is allowed.

Each branch must eventually be classified:

### KEEP_ACTIVE
Current source of ongoing work or operational dependency.

### KEEP_REFERENCE
Historical evidence or recovery/reference branch worth retaining but not active development.

### CLOSE_PR_ARCHIVE_BRANCH
Open PR is superseded; close PR first, retain branch temporarily for archival review.

### DELETE_AFTER_EVIDENCE
No open PR, no workflow dependency, no unique commit needed for current runtime/history, and replacement is documented.

## Safety checks before branch deletion

A branch may be deleted only after proving:
1. it is not the protected deploy branch;
2. it is not the source of the currently deployed SHA;
3. it is not referenced by an active workflow trigger;
4. it is not an open PR head/base still considered active;
5. unique commits are either merged, tagged, released, or intentionally discarded;
6. no current checkpoint/automation depends on it.

## Immediate recommendation

Freeze branch proliferation while Deploy V2 is introduced:
- one isolated branch per coherent change;
- no new branch solely to diagnose one failure when the failure can be represented as a regression test or control-plane operation;
- close/merge/archive the branch before opening a replacement for the same failure class whenever practical.

## Production impact of this audit

- Branch deletions: **NONE**
- PR closures: **NONE**
- PR merges: **NONE**
- Protected branch changes: **NONE**
- Runtime changes: **NONE**
