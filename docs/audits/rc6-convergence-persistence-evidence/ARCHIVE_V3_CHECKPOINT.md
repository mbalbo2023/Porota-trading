# Exact component archive V3 checkpoint

This checkpoint implements the private, local archive. It does not certify the
large producer, 512 MiB sustained horizon, UI deadline or production activation.
The preceding source commit is `32da316afd94ad9016e2cb5c3e6ef3e94c49e2fd`.
Storage V1/V2 and existing TAR V2 receipts retain explicit dispatch.

The canonical `ShadowRuntime.from_environment` emits `COMPONENT_V3` and binds
the effective archive format, root and quota in its configuration fingerprint.
Explicit offline constructors continue to default to `TAR_V2`. No operational
path, provider, source database, live runtime or PPI Watch was accessed.

`EvidenceRetention.archive_generation(path)` captures the four original members
and optional `projection.sqlite`. Source gzip framing components remain their
original compressed bytes. SQLite uses Finance's exact-page storage, binding
original source SHA and a verified previous-image dependency; actual depth is
derived from its receipt chain, capped at 32, with full native sequence anchors.
It never equates a shared compressed body with a shared logical financial row.

Immutable flat packs use `RC6CASP3` and SHA-bound fixed CID/offset/length indices.
The per-cut object is `generation_id.recipe.gz`, schema
`RC6_SHADOW_ARCHIVE_COMPONENT_RECIPE_V3`. Its original member lengths/SHA,
complete references, manifest links, source audit/report digests and role headers
remain bound. Receipts use `RC6_SHADOW_ARCHIVE_ACK_V3`, archiver
`RC6_LOCAL_PRIVATE_COMPONENT_ARCHIVER_V3` and a distinct recipe URI.
Old receipt bytes and sequence digests are not rewritten or relabeled.

Publication persists `BUILD.json`, fsyncs original components and the recipe,
restores all original member bytes and validates their hashes, CRC, manifest,
sealed role-wire proofs, typed safety and complete projection index, then
publishes receipt/head and local ACK. An interrupted build resumes only against
the same source manifest. Local source bytes, file metadata and directory atime
are preserved with `O_NOATIME`, `NOFOLLOW` and identity revalidation.

The public read-only API is:

```python
EvidenceRetention(root, archive_root=archive).restore_generation(generation_id)
# {"receipt": dict, "manifest": dict, "members": {name: original_bytes},
#  "verification_level": "ALL_ORIGINAL_MEMBER_BYTES_MANIFEST_CRC_AND_ROLE_WIRE"}
```

It acquires shared existing locks, verifies receipt-chain membership and uses a
fresh verification context per call. It never creates a lock/directory, repairs
an input, changes CURRENT or calls a recovery encoder. V2 TAR restoration has
level `LEGACY_V2_ALL_ORIGINAL_MEMBER_HASHES_AND_MANIFEST` and binds each fresh
read again to the original receipt SHA. A missing or corrupt dependency rejects
the whole recovery before ACK or source rotation. Custody remains local private
fsync, explicitly **not WORM or external authentication**.

The retention clock is the greatest verified archived cut `as_of`, rather than
the archiver wall clock. The checkpoint retains nine hours plus a one-hour
recovery margin and complete transitive page bases. All extant local generations,
delete intents, ACKs, CURRENT and declared pins prevent expiry. Expiry affects
only a contiguous receipt prefix; its terminal count/digest remains in the
durable checkpoint. Expired ACK retries fail closed instead of rewinding or
recreating archive authority.

`GC.json` persists an exact owned deletion plan before advancing the compacted
boundary. Recovery re-derives reachability from the committed chain, checks pins,
hashes and page dependencies, then resumes idempotent deletions and directory
fsync. Unreachable whole packs can be collected; partial-pack reclamation and
the long-run physical cost still require the native horizon measurement.
Every new component admission includes logical bytes, allocated blocks, root
directory residence, simultaneous object/control temporaries and a bounded GC
control reserve. Neither the 512 MiB archive cap nor 32768-file cap increases.
The autonomous stdlib inspector remains an honest custody/namespace inventory;
`BUILD.json` and `GC.json` explicitly require writer recovery.

Native focal validation:

- First run: 27 passed, 9 failed. The fixture's automatic GC had already removed
  the eligible prefix before the explicit fault window. Raw XML and source
  hashes remain preserved; these were not labeled as successful SIGKILL tests.
- The fixture now holds a native pin during 50 real publications/archives,
  releases it and uses verified ACK rotation before injecting GC faults.
- Corrected run: 37/37 passed in 9.030 s, including seven real SIGKILL stages,
  six interrupted BUILD stages, corrupt/missing actual packs and page bases,
  resealed native recipe attacks, exact original five-member restoration,
  read-only bytes/custody, ACK retry with a newer head, quota/fingerprint and
  unchanged legacy TAR/receipt dispatch.
- Associated legacy/native focal: 215/215 passed in 16.088 s. The two long
  1201-tick/2000-generation tests were explicitly excluded and remain pending.
- Final native run after adding fresh-hash legacy substitution protection:
  38/38 passed in 8.914 s. Its source guard replaces a real old TAR between
  verification and extraction; restoration rejects the changed original SHA.

The attached JSON records exact JUnit and source hashes. These small native
guards are not a substitute for the complete canonical-factory 12000/60000
producer, physical 1201-tick/2000-generation horizon, UI one-second/four-MiB or
SRE two-second measurements. Those requirements remain active software gates.

An independent SRE negative subsequently exposed a directory-atime gap in the
writer retry on `7e9425e2`: checkpoint repair enumerated the archive through
`Path.iterdir` before rejecting a corrupt pack. A separate exact-source probe
confirmed that restore alone preserved custody, while the archive retry changed
only the archive root's atime. The earlier child-only snapshots did not include
the root directory itself. This is a verified product failure, preserved in
`archive_v3_atime_7e9425e2_red.json`.

Retention inventories, checkpoint repair, CAS catalog, GC and live namespace
enumeration now use the bounded, identity-checked NoAtime directory descriptor.
The new native guard snapshots each protected root itself, all descendants,
source authority and original bytes; corrupt packs, missing packs and missing
base receipts reject without changing any of those values. Final source focal:
41/41 passed in 10.557 s, with exact XML/source hashes recorded in
`archive_v3_atime_fix_evidence.json`. Horizon and large-render gates remain open.
