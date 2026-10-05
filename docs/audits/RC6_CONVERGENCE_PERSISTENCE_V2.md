# RC6 convergence — committed evidence, sources and retention V2

Scope: U14–U21/U26 and F03/F04/F05, PAPER/SHADOW offline fixtures only.
This change does not enable trading, provider calls, production migration,
deployment, rollback or PPI Watch access.

## Shared cut and root

`shadow_evidence_root(database, environ=None)` is the shared resolver. Its
default is `cg_paper_workspace.artifact_root(database) / 'dynamic-shadow'`.
`POROTA_DYNAMIC_SHADOW_ROOT` and the compatibility alias
`POROTA_SHADOW_RUNTIME_ROOT` are accepted together only when both resolve to the
same path. Aliases, conflicting roots and protected-input overlap fail closed;
there is no mtime-based search or independent-file fallback.

`read_committed_generation(root)` remains bounded, nonblocking and read-only.
It returns `report`, `checkpoint`, `status`, `manifest`, `pointer` and
`export_contract`. The producer schema is
`rc6.shadow-evidence-generation.v2`; the export is
`rc6.shadow-committed-cut.v2`. All roles carry the same generation ID, integer
sequence, source watermark, configuration fingerprint and SHADOW safety fields.
The reader verifies all members, cross hashes, clock links, canonical source
audit and safety before returning any role.

`ShadowRuntime.from_environment(database)` constructs the same worker used by
`run_worker`, without creating an evidence directory or writing a database.
`configuration_fingerprint(as_of)` computes the same current configuration
identity used by `tick`, for health/predeploy consumers.

V1 is an explicit **writer-only forward bootstrap**. Consumers reject it until
the first V2 cut exists. Bootstrap preserves old evidence, verifies the old
generation, then publishes a new V2 generation with a larger sequence. A later
restore of V1 CURRENT cannot reopen bootstrap after the authority advances.

## Safety, source semantics and confidentiality

Every role requires SHADOW mode, strict integer zero real orders and provider
requests, routes/factual execution `NOT_CALLED`, source DB effect `READ_ONLY` and
PPI Watch `UNTOUCHED`. Supplied contradictions are rejected before fixed fields
are copied to roles. Source reports also cannot grant live/entry authority.
Optional real-money/live-authority declarations must be false, and any declared
additional provider budget must contain strict integer zeros for current, book
and intraday. Production-limit modification declarations may reflect approved
PAPER policy but must be booleans that agree across roles. Nested safety vectors
also preserve JSON types; boolean `false` cannot stand in for integer zero.

The writer and reader recompute `audit_sources(reports=..., as_of=...)`, including
zero reports. Canonical digests enforce both values and JSON types: boolean
counts are not integer counts. Schema, cutoff, status, counts, pointers, report
digests and snapshot references cannot disagree with their source reports.
Duplicate report digests, conflicting `(source, cutoff, native-input)` report
identities and exact duplicate observation rows are rejected.

Error text is replaced by a closed reason vocabulary before IOL cache/checkpoint
writes, when resumed IOL caches are read and at the worker's shared source
ingestion boundary. All downstream consumers, including family evidence, receive
the sanitized cache. The committed source-report boundary checks that its error
taxonomy is already canonical. Exception classes/status codes may select a
reason code; arbitrary messages, bearer headers, signed URLs, account text and
response bodies never become error labels. Tests use synthetic markers and scan
decompressed report/checkpoint/status members.

## Lineage and publication recovery

The bounded authority is `<evidence-root>.authority/HEAD.json`, outside the root
restored by a CURRENT-only snapshot. It records the allocated high-water, last
sealed pointer and prepared pointer. Reservations occur before sequence-bearing
generation files exist; interrupted preparation may create a sequence gap, never
reuse a sequence.

The writer transitions through `PREPARED → PUBLISHING → COMMITTED`:

1. Fsync members/manifest, rename the generation and fsync its directory/root.
2. Fsync the pointer temporary and the independent publication intent.
3. Replace CURRENT, fsync the root and seal the exact pointer in the authority.

Readers accept only sealed publication. A crash between publication intent and
seal returns `SHADOW_PUBLICATION_RECOVERY_REQUIRED`; it cannot authenticate an
old or new CURRENT. The next exclusive writer validates the immutable prepared
cut and publishes/seals that exact cut **forward**, even if CURRENT was restored
to the prior pointer. It then resumes normal commits. Readers never repair or
write control data. A rollback of a completed CURRENT fails with
`SHADOW_CURRENT_ROLLBACK`; a fully rehashed cut inconsistent with its separate
authority fails with `SHADOW_LINEAGE_FORK_OR_REHASH`.

**Trust boundary:** this is local durable custody, not WORM, a signature service
or authentication against the same UID rewriting both evidence and authority.
A full restoration/compromise of both custody roots needs an independently
retained authority or external anchor and is not certified here. Freshness is a
separate consumer requirement; a coherent cut is not evidence of current data or
OPEN capacity.

## Retention and verified archive

The canonical writer's default live quota is 8,192 recursive entries and
128 MiB. The strict generic `RetentionPolicy` default remains 512 entries for
explicit small-budget consumers/tests. The writer reports 30-second cadence,
a nine-hour contracted horizon, remaining file/byte horizon estimates and archive
configuration. Bytes, recursive entries, directory metadata and staging peak
remain bounded. Estimates depend on cut size; they do not certify arbitrary
catalogue/payload workloads.

CURRENT and supplied pins are protected. The canonical writer supplies its prior
generation pin. Unarchived evidence, freezes and unknown files are never removed
to satisfy pressure. Exhaustion fails SHADOW closed and preserves factual paths.

An archive is used only when an explicit private `archive_root` is configured
(`POROTA_DYNAMIC_SHADOW_ARCHIVE_ROOT` for the worker). Its independent defaults
are 512 MiB and 32,768 files; the archive has its own exclusive lock and quota.
No external service or production destination is inferred. The local archiver:

1. Produces a private deterministic gzip/tar of the four exact generation
   members, fsyncs it and verifies actual object bytes, members, manifest and
   bounded decompression, including gzip CRC/trailer.
2. Stores a V2 receipt with allowlisted local archiver ID, generation/manifest/
   object hashes, monotonically increasing receipt sequence and previous-receipt
   digest. It fsyncs receipt/checkpoint before installing the matching local ACK.
3. Refuses V1 self-asserted ACKs, URI/hash inventions, wrong identity, replay,
   absent/unreadable/corrupt objects or mismatched external receipts.

Local archive custody is `LOCAL_PRIVATE_FSYNC_NOT_WORM`. An independently trusted
production archive/identity remains an external operational prerequisite if that
stronger model is required. Offline byte verification is implemented and tested;
it is not replaced by this external limitation.

Rotation writes a durable delete intent, atomically renames `gen-ID` to
`.deleting-ID`, fsyncs the namespace and cleans only the narrow regular-member
set. Restart resumes partial deletion using the independently verified archive
and durable intent; it does not need the partially deleted local manifest.
Unknown members or aliases are preserved and fail closed. ACK compaction occurs
only after archive verification and a durable bounded checkpoint; external
receipts remain with their objects. Receipt high-water recovers a checkpoint
interruption/restoration along the exact immutable digest chain and cannot reuse
receipt sequences. Missing/conflicting receipts stop rotation.
If a process dies after publishing the external object/receipt but before its
local ACK, later archives may advance the receipt high-water. Retry validates
the older receipt's membership in the complete sealed digest chain and restores
its exact ACK without rewinding the checkpoint. Rewritten older receipts cannot
authorize rotation, even when their object and both receipt copies agree.

Archive exhaustion stops further archival/rotation and preserves the latest
committed cut and unarchived generations. Archive objects/receipts are not deleted
automatically. A finite quota intentionally requires lifecycle planning; growing
a second ledger without a bound is not accepted.

## Validation and limits

The new suite `tests/test_rc6_convergence_persistence.py` executes real writer,
worker, reader and capacity-consumer paths on synthetic SQLite/caches. It covers
full rehash, cross-role safety/types, CURRENT rollback/deletion, actual V1
bootstrap, false/ghost source audits, duplicates, secret markers, receipt attacks,
EIO/SIGKILL around every rotation boundary and SIGKILL around publication sealing.
The rotation tests also inject EIO and real SIGKILL directly at every fsync in
the deletion transaction, both control-file replacements, tombstone rmdir and
delete-intent unlink; this includes the durable-intent and compaction windows.

Two sustained tests write machine evidence into
`docs/audits/rc6-convergence-persistence-evidence/` when
`POROTA_RC6_TEST_EVIDENCE_DIR` explicitly selects that destination:

- Reproduce old soft pressure at 40.5 minutes and hard pressure at 50.5 minutes,
  then execute 1,201 real full ticks over ten runtime hours with two restarts,
  five catalogue identities and static observations. Verify no pressure, zero
  provider/real orders and unchanged source-database bytes. Synthetic fixture
  writers are closed/checkpointed before the byte baseline.
- Execute 2,000 real commits with 1,999 writer restarts and a 32-entry live quota.
  Verify more than 1,900 immutable receipts, continuous receipt sequence/digest
  chain, zero retained local ACK files, a bounded checkpoint and archive quotas.

The full-tick test found and guarded an additional exact-close defect: at session
close the next session's cutoff equals `as_of`. The worker now publishes
`PREOPEN_SNAPSHOT_PENDING_AFTER_SESSION_CUTOFF` instead of freezing a cut whose
availability is not later than cutoff. The next genuinely later tick may build
the next preopen snapshot. The strict preopen temporal validation remains intact.

These are offline liveness/integrity proofs for their declared fixtures, not
production capacity, volume-contract, profitability or external-authenticity
certification. The final integrated SHA/tree still requires its consolidated
suite, artifact gate and operator authorization before deployment.
