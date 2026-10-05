# Tiny codec and archive control on the single RC6 image

This source change adds a bounded native control for the image already built by
Predeploy V2. It grants no runtime, market, deployment, archive horizon, or
browser approval. The source pytest controls and metadata fixtures are not
Docker execution evidence.

The executable is `scripts/rc6_archive_v3_image_smoke.py`. It requires the
canonical V3 public API `EvidenceRetention.restore_generation(generation_id)`
and closes RED if that API, a source component, or an original dependency is
missing. There is no legacy-only fallback. Its imports use only the standard
library and versioned RC6 modules already inside the runtime image.

## Root-owned Predeploy integration contract

Root owns the workflow. Run the CLI after the sole image build and exact Docker
ImageID inspection, before frozen metadata and the immutable primary upload.
Use the observed ImageID both as the Docker target and the CLI argument. The
script cannot authenticate its own ImageID argument.

```bash
docker run --rm --pull never --network none --read-only \
  --user 1000:1000 --cap-drop ALL --security-opt no-new-privileges:true \
  --no-healthcheck \
  --tmpfs /tmp:rw,noexec,nosuid,nodev,size=33554432,uid=1000,gid=1000,mode=1777 \
  --entrypoint python "$ACTUAL_IMAGE_ID" \
  -m scripts.rc6_archive_v3_image_smoke \
  --source-manifest /app/POROTA_SOURCE_PROVENANCE.json \
  --candidate-sha "$CANDIDATE_SHA" --tree-sha "$CANDIDATE_TREE" \
  --image-id "$ACTUAL_IMAGE_ID" > /tmp/porota-runtime-codec-smoke.json
```

No host mount, provider, network, production data, or second image build is
required. The CLI has a 30-second process alarm, a 64-KiB JSON output bound,
16-MiB admitted private scratch, and a Linux 512-MiB RSS bound. The container's
private 32-MiB tmpfs is the external scratch ceiling. Preserve the raw output
as `porota-runtime-codec-smoke.json` in the primary artifact.

Frozen metadata must add exactly:

```json
{
  "runtime_codec_smoke": {
    "schema": "rc6.archive-codec-tiny-image-smoke.v1",
    "receipt_sha256": "SHA256 of the raw primary JSON file",
    "script_sha256": "Git-derived source row SHA256",
    "fixtures_sha256": {
      "scripts/fixtures/rc6_codec_legacy_v1.json": "Git-derived SHA256",
      "scripts/fixtures/rc6_archive_legacy_v2.json": "Git-derived SHA256"
    },
    "image_id": "the exact Docker-observed sha256 ImageID"
  }
}
```

The binder verifies the independently Git-derived source manifest, the source
rows' image/bundle inclusion and mode, the raw receipt digest, all eleven case
IDs, typed safety and custody, and the equality of this ImageID to frozen
metadata. The external GitHub repository/workflow/run/attempt/head/artifact
tuple and upload ZIP digest continue to provide execution/artifact authority;
this JSON does not replace them.

When the approved Git source contains
`docs/audits/convergence/INPUT_MANIFEST_RC6_CONVERGENCIA.json`, the binder also
requires `porota-final-input-provenance.json`, `porota-governed-tests.json` and
`porota-governed-tests.xml`. Frozen metadata must contain the raw FIP SHA256 in
`final_input_provenance_sha256`. FIP schema is `rc6.final-input-provenance.v1`,
with `candidate_sha` and `candidate_tree`. It must be `EXECUTED_NATIVE_GREEN`,
with `final_candidate_eligible is True`, `material_programming_gates_closed is
True` and an actual empty list of pending material gates. Root runs this check
before the sole image build.

Gov binds the same candidate/tree and captured JUnit bytes, with exact integer
counts and zero failures/errors/skips/xfails/pytest exit code. Booleans cannot
stand in for integers. The binder captures each raw file once, loads the native
verifier from candidate source whose Git-derived hash and physical mode were
checked, constructs its immutable `JunitReceipt`, validates actual XML outcomes
and counts, and recomputes the whole FIP. A typed canonical comparison rejects
an otherwise closed-looking self-asserted report. Source verification and
replacement-ref rejection run before and after. Generic source-format fixtures
without this versioned handoff receive `NOT_APPLICABLE`; they never claim final
convergence or governed execution.

`porota_published_artifact_evidence.py` downloads the exact immutable primary,
verifies it, loads its saved image, and rejects any actual loaded-ID mismatch
before executing containers. It runs the same CLI again on that loaded ID with
the flags above and a 30-second subprocess bound. Replay is an independent
execution receipt and may have different synthetic generation IDs and elapsed
time; source, fixture, candidate, tree, ImageID and case closure must agree.
On execution failure, cleanup targets only its random private container name.
The primary raw JSON, original Gov/JUnit XML, and replay receipt are preserved in the bounded,
evidence-only secondary artifact. The primary remains the sole promotable
artifact; no image is rebuilt.

## Exact case closure

| Case ID | Native control |
| --- | --- |
| LEGACY_STORAGE_V1_ORIGINAL_BYTES | Decode the original native V1 bytes and compare exact typed logical bytes. |
| PACKED_STORAGE_V2_EXACT_ROUNDTRIP | Encode/decode genuine >256-KiB V2, preserving types, Unicode, +0.0/-0.0, max finite/subnormal floats and independent expanded rows. |
| PACKED_STORAGE_V2_RESEALED_CRC_REJECTED | Alter gzip CRC and reseal public packet/storage hashes; reject the actual wire defect. |
| UNKNOWN_STORAGE_SCHEMA_REJECTED | Reject an unsupported version rather than treating the envelope as a plain value. |
| PAGE_PACK_FULL_AND_DELTA_EXACT | Restore a full pack and a two-page delta to exact original bytes without encoders. |
| PAGE_PACK_WRONG_BASE_REJECTED | Reject an incorrect previous page base by its actual SHA. |
| LEGACY_ARCHIVE_V2_ORIGINAL_BYTES | Restore original V2 tar receipt plus five original members through the current public API. |
| NATIVE_ARCHIVE_V3_FIVE_MEMBERS_EXACT | Publish and archive two native generations, then repeatedly restore all five exact members. |
| NATIVE_ARCHIVE_V3_CORRUPT_PACK_REJECTED | Reject a mutated original CAS dependency in restore and ACK retry, without a new ACK or source mutation. |
| NATIVE_ARCHIVE_V3_MISSING_PACK_REJECTED | Reject the actual absent dependency in both callers; do not regenerate or remove the origin. |
| ISOLATED_STDLIB_NAMESPACE_LOADER | Load the exact inspector source under Python `-I -S`; preserve its metadata-only verification level. |

Original source bytes and all observable source stats are compared with
NoAtime file/directory reads. Restore encoders are forbidden. A negative case
only passes with its named error signature or the exact missing dependency;
an unrelated error never counts as successful rejection. Directory traversal
rejects aliases without requesting NoAtime privileges on root-owned ancestors.
Each corrupted/missing dependency case first restores successfully through the
same reader object, then mutates its private dependency and rejects the next
call with encoders disabled. A stale result cache cannot satisfy that control.

## Legacy fixture custody

The two fixtures were generated once with native original code from commit
`6ed979877b2bee8b210940d9601d3f92513f2f15`, tree
`141f3dd847f32684512f536f0004b944a44d705b`, in a complete private Git archive.
The original storage, retention, persistence and projection blobs were checked
against the SHA256 values embedded in the CLI. The generator ran with network
calls blocked and only private synthetic data; 0.168896 seconds elapsed.
The original logical codec payload is 337817 bytes, above the actual 256-KiB
encoding threshold. The fixtures preserve original bytes/base64/size/hash,
native V1 storage, native V2 receipt, archive control files and all five
generation members. They are checked into Git and are not generated during CI.

| Asset | Bytes | SHA256 |
| --- | ---: | --- |
| scripts/fixtures/rc6_codec_legacy_v1.json | 3725 | 8d21e988caee08b405725a2f8b73e8b87e0d90c3e21db0a8cc11a715dbe0966c |
| scripts/fixtures/rc6_archive_legacy_v2.json | 555190 | 1e8d0b1150909eda33ccd168e3f37eba3345ba2aaad0837a340e9daa684fc3be |

The generation tool output is session evidence, not an externally signed
receipt. Git-derived fixture hashes bind the subsequent image and bundle.
Source pytest executes the actual CLI in a fresh native Git clone with a
private committed candidate and an explicitly untrusted ImageID argument.
Binder/Docker orchestration unit fixtures are labeled
`EXPLICIT_SYNTHETIC_METADATA_ONLY_NOT_IMAGE_EXECUTION`.

The producer's local `6ed` commit is retained as generation metadata. CI does
not require that unpublished ref: reachable product ancestor
`67e2b010cf5a209acf272caca42ab138499a4dd0` contains all four identical source
blobs with exact mode100644. Native `rev-list --objects --missing=print` on
root `dc59c2fb` with those four paths confirms their reachability without lazy
fetching. The source test checks ancestry, modes, Git OIDs and SHA256 against
that reachable anchor, preserving both fixture assets unchanged.

## Intermediate RED retained

`/tmp/porota-sre-v3-smoke-first-native.xml` retains 114 passing cases and one
failed actual CLI case: importing an encoder alias while the harness patched
its definition retained the forbidden function after context exit. Loading
the module before patches fixes that harness defect without relaxing restore.

`/tmp/porota-sre-v3-smoke-corrected-native.xml` retains the next native RED on
canonical V3 commit `7e9425e202b9e67e87aad67dd59218c509a01721`: negative ACK
retry rejected the corrupted pack but changed the archive root directory's
atime. Live source members, CURRENT, authority, ACK absence and bytes remained
intact. The owner confirmed the writer's plain `archive_root.iterdir()`
inventory. Canonical fix `0dc8a520d8af7cbdc9485eaceb3b037d897eeb7e` preserves
those stats; restore alone had already preserved them. The exact all-stats
guard remains active. Signed-zero fix `107a8319` was then integrated and
exercised by the additional V2 float control; legacy assets remain fixed.

`/tmp/porota-sre-v3-smoke-final-binding-native.xml` retains two extraction-only
test-fixture failures after FIP became mandatory: the old fixture inserted a
duplicate required FIP and its supposed missing variant actually included one.
Preparing the omission before inserting the unique/duplicate variant corrected
the fixture. No extractor or provenance guard was relaxed.

Pre-seam owned source focal: 140/140 pass, zero skips/errors/failures, 27.996 seconds,
`/tmp/porota-sre-v3-smoke-complete-native.xml`, SHA256
`4c935ad22a42f352cf79684a87ffb1d98b36d79fecba93b37ae1beafb8f85b0d`.
The source CLI executed all eleven cases in 0.558996 seconds with UID1000,
84,447,232-byte RSS peak and 3,100,672-byte private allocated scratch. Its
ImageID was an explicitly untrusted caller argument; Docker was never executed.
The separately recorded JSON distinguishes this source proof from image proof.

That receipt tested the original worktree workflow from root `003dccb5`; it
does not claim execution of root's subsequent `b92dfe98` workflow. Root owns
the workflow change and repeats the integrated source checks after the own
commit is applied. No workflow was copied into this worktree.

## Live namespace admission seam

The standalone live inspector, exact policy validator and disk policy now use
512 live entries, matching the producer's canonical file limit. All other
policy fields remain unchanged: live 128 MiB, archive 512 MiB with 32768 members,
scratch 512 MiB, and one 2 GiB filesystem reserve. Admission is closed at 512;
the positive control contains 511 entries. Its empty, owned ACK-shaped files
exercise namespace custody and occupancy without asserting receipt semantics
or capacity to prepare a producer publication. Both observations preserve all
source metadata, including atime.

The standalone combined probe binds independently hashed source bytes for
scratch, archive, live and admission modules, including the changed live and
admission source. The native archive inspector remains metadata-only. Logical
and physically allocated occupancy remain distinct; source binding and the
unchanged byte ceilings do not close the full archive horizon gate.

After that seam, the complete own focal plus archive admission and disk policy
controls passed 202/202 cases in 29.402 seconds, with zero skips, errors or
failures: `/tmp/porota-sre-v3-smoke-live512-native.xml`, SHA256
`80345063817c33f1b3aeb13be4d50a902fa95f1b75473f74203e22960a882428`.
The additional native CLI receipt executed all eleven cases in 0.659842
seconds, UID1000, RSS peak 90640384 bytes and private allocated scratch 3100672
bytes. Its raw SHA256 is
`4168874f532e0c259df18bf49ec3ff01ad9f43db4bddae0c526692168faf5918`.
`RUNTIME_CODEC_IMAGE_SMOKE.json` preserves the exact parsed native report,
current source hashes, test receipt summaries and scopes. It does not create
execution authority beyond those private source controls.

The 202-case receipt precedes the final source-only workflow assertion
adaptation. The secondary-upload test now requires exactly the JSON and XML
patterns committed in root `b92dfe98`, with no generic wildcard. Its native
execution against the integrated root workflow remains pending; this worktree
retains its old workflow and does not claim that adapted assertion passed.

Final integrated source validation, execution inside the final image, archive512/full
horizon capacity, fresh canonical large-producer health and browser gates
remain separate obligations. No production change is authorized by this file.
