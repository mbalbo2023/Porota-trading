# RC6 A689 — durable slot CAS, checkpoint DRAFT/HOLD

Checkpoint ID: RC6-A689-DURABLE-CAS-20261105
Date: 2026-11-05 ART
Ownership: Bruno Salvatierra (DevOps) + Inés Navarro (Middleware), exclusive
branch `fix/rc6-a689-durable-slot-cas-20261105`.

## Authority and immutable reference

- Repository: `mbalbo2023/Porota-trading`.
- Upstream source/main at authorization: `0d19172068e343360ccb593de955934e37400a2a`.
- Existing A685 DRAFT #990 stacked base: `16f9e4fc52c637f2d7472fa3750a013a5828f541`.
- Product SHA (not modified): `7e1e744d6ca9ab30c51b5747dd4467af5ece0145`.
- New A689 DRAFT #994; no merge/main/product/deploy/dispatch performed.
- Ownership declarations: canonical issue #732 comments `6658098602` (6 code/test/workflow
  paths) and `6658519555` (this seventh documentation path).

## RCA and fix-forward design

The A685 GitHub Actions run/artifact ledger is only a conservative pre-SSH
witness: no atomic compare-and-set (CAS) is provided across REST pagination,
API errors, multiple runs, or artifact visibility windows. It therefore cannot
by itself prevent two runs making the same PPI GETs. It remains fail-closed,
but is **not the authoritative mutex**.

A689 places the **durable CAS directly inside `rc6_a630_host_ppi_current_book.py`**
before importing `ProductionMarketReader`, acquiring a provider lease, or
issuing any Current/Book GET. The new helper has no dependency on PPI Watch,
legacy PAPER databases, and no direct trading effects.

- The only proposed state is the Porota-exclusive bind mount subtree
  `/app/data/a670-slot-claims/claims.sqlite` (host source must prove
  `/opt/porota-trading/data`, not another app's storage).
- `slot_claims` has `PRIMARY KEY (art_date, slot) WITHOUT ROWID`, using
  `BEGIN IMMEDIATE`, exact schema allowlist, `rowcount == 1`, exact row readback
  inside and after COMMIT, `PRAGMA synchronous=FULL`, fsync of DB file and both
  **parent**/child directories. No TTL, DELETE or claim reset is available.
- Process crash/power failure after durable COMMIT consumes the slot even
  if the PPI request was never made; this is an intentional conservative cost.
- Missing/wrong mount, UID, writable group/world parent, symlink, hardlink,
  unsafe schema/trigger, lock/storage error, clock/ref/run ID mismatch, retry,
  duplicate, helper hash mismatch or unavailable runner context => HOLD
  **before GET**.
- The product image does **not** already include the new module. Actions
  streams its exact SHA256-pinned source in memory over the existing authenticated
  SSH route; A630 checks helper SHA256
  `89b3a3c686e5754566f1945033edb1ace5fe34954b1ebf20b19b0790d3744aac`.
  A669 pins the A630 Git blob
  `e703c686084637465f7707afe745c4bc16f74404`.
- The current scheduler on `main` is **unchanged**; no new action, cleanup,
  migration, Docker edit, sidecar/Watch edit or host write was attempted here.

## OFFHOST regressions — no provider, no droplet

- A689 `tests/test_rc6_a689_slot_cas.py`: **24/24 PASS** (6-process race,
  crash/restart, retry, schema/table/trigger tamper, digest substitution,
  symlink/hardlink, private parent root mode, parent fsync failure injection,
  pre-reader/no-GET integration). RED-before-GREEN regressions documented for
  missing parent fsync, group-writable parent, trigger-skip insert.
- A630 existing tests: **37/37 PASS**.
- A669 slot and blob guard: **13/13 PASS**, including initial pin mismatch RED
  and corrected exact-current-Git-blob GREEN.
- A685 pre-SSH guard: **19/19 PASS**.
- Workflow YAML parse and shell `bash -n` each run block: PASS.
- Offhost total: **93/93 PASS**. These tests do not establish runtime mount/UID.

## Operational blockers — NEVER call this deployed/GO without proof

1. **Runtime mount and UID NO_VERIFICADO**. Although the archived product
   launch script proposes `--user botuser -v /opt/porota-trading/data:/app/data`,
   no current host `docker inspect`, file owner, inode or fsync was checked.
   The new guard may legitimately HOLD if container user does not own
   `/app/data`, if its mode is group-writable, or if the bind changed.
2. **Trust boundary**. GitHub Actions runner validates run ID, attempt=1,
   main SHA/ref/event/slot and forwards them via the protected SSH channel.
   Those env values are **not independently GitHub-signed in the container**.
   Do not claim a cryptographic host attestation or external provider signature.
3. **Stacked PR CI**. A670 PR OFFHOST workflow is scoped to `pull_request`
   targeting `main`, not the #990 branch. The A689 exact-head GitHub CI result
   is therefore **NO_VERIFICADO**, even though all local tests pass. The
   generic checkpoint-attestation workflow requires separate literal fields
   in the PR description; a failed run is not a GO.
4. **No historical backfill of CAS**. A689 cannot retroactively protect
   previous A670 attempts before a future deployment. Prior-run evidence
   must be reconciled without making new provider requests.
5. **No authority for operational changes here**. Do not run dispatch,
   predeploy, deploy, host commands, cleanup, DB repair/migration, PPI Watch
   mutation, or real orders. PAPER/SHADOW only; `real_orders_sent=0` runtime
   remains NO_VERIFICADO without separate droplet evidence.

## Required later gates before any merge or production PAPER execution

1. Independent review of exact HEAD in PR #994, all seven scope paths and
   reproducible OFFHOST CI with attested product source.
2. READ_ONLY production preflight of **named Porota container only**: exact
   image, bind type/source/destination/RW, user, UID, owner/modes, inode,
   fsync persistence capability. Never assume sibling apps' mounts are Porota.
3. Resolve incompatible root ownership with a scoped design and additional
   approval, not a blind `chmod`, global `prune`, host migration, or hidden bypass.
4. Validate first claim/second rejection after the approved single governed
   candidate/deploy using a safe test that does **not** issue real PPI GETs.
5. Preserve A685 witness, A669 holiday/slot gate, source pin, PPI shared
   budget and exit reserve unchanged; verify only an immutable artifact that
   passed Predeploy V2, one controlled deploy and runtime PAPER evidence.

State: **DESARROLLADO / EN_GITHUB (DRAFT), not deployed, not GO**.
