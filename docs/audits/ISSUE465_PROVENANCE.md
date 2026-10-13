# Issue #465 — Front E: exact source byte provenance

Scope: `scripts/porota_artifact_provenance.py`, deploy bundle builder, artifact
validator, the existing Predeploy V2 workflow, and exclusive provenance tests.
PAPER/SHADOW only; `real_orders_sent=0`, real routes `NOT_CALLED`.
No provider calls, Docker build from this front, deployment workflow changes,
merge, production runtime/DB operations, or PPI Watch operations.

## Error and root cause

The independent audit of #463 found that the previous artifact validator checked
runtime presence, parseability and import closure, then hashed the artifact's own
files. A modified but parseable `.py` at the correct path could remain GREEN.
The bundle manifest similarly described the bytes encountered by its builder.
Neither check independently tied every required artifact byte to a frozen Git
blob. The image label covered the commit, but lacked tree and source-manifest
identity. A successful build or an artifact's self-authored file list was therefore
insufficient evidence.

`test_legacy_presence_validator_rejects_same_paths_with_wrong_bytes` preserves
the counterexample: change only the bytes of `worker.py`, leave paths/imports
intact, and require FAILED. The new manifest tests also recompute a forged
manifest's own digest; the forged document still fails against the Git authority.

Original RED witness, re-executed offline from the exact Git object of
`caf9bc94b4a1f436ad01a84a9e9e9e7a4a9e9423`:

```json
{
  "after_source_byte_mismatches": [
    "app.py"
  ],
  "after_status": "FAILED",
  "baseline_head": "caf9bc94b4a1f436ad01a84a9e9e9e7a4a9e9423",
  "baseline_validator_sha256": "c0b5c9ff81a2b79bee062216c8d67e0804d7211292bc2eb227991e53ec399322",
  "before_contract_result": "RED",
  "before_missing": [],
  "before_missing_imports": [],
  "before_parse_errors": [],
  "before_status": "GREEN",
  "real_orders_sent": 0,
  "real_routes": "NOT_CALLED",
  "required_status": "FAILED",
  "schema": "issue465.provenance-original-red.v1",
  "source_sha256": "e13df8c44af5dea1e412403910b99cc5a48f2ccbf68a66b3374d6ab9cef9fc65",
  "tampered_artifact_sha256": "3da6c4b355804b265ecef19dfe5945fe2790a8cc38dc9d0547fb1634c94cbbe7"
}
```

Canonical witness JSON SHA256:
`854a0bcd9505e0b97f82a183971ac053ee26908ef16b08015e0e79ba3e3fd1dd`.
The input is exactly `VALUE = 1\n`; the artifact changes its digit to `2`.
This preserves the original RED without committing private runtime data or
mistaking the incorrect validator's GREEN for a successful contract.

Further self-refutation found a gzip transport bug in the first new validator:
`tarfile` stops at tar end markers without necessarily consuming the gzip
footer. Four permanent tests demonstrated accepted missing/corrupt footers and
trailing garbage, including a bundle with an honestly updated outer digest.
All four were RED before the fix. Every accepted gzip artifact now receives a
complete streaming CRC/size/trailer check through actual EOF, capped at 8 GiB
unpacked bytes, before source/archive acceptance. The tests remain in the suite
as `test_bundle_gzip_envelope_corruption_rejected_even_with_updated_outer_digest`
and `test_saved_image_missing_gzip_footer_rejected`.

## Native image export correction

The first complete candidate `d967d570a6d0ea88f119ae2bb81f91ecfcb1bc6d`,
tree `9a04b09912531c981db9f7bee6704d295d4094a2`, passed its 3,583-case root
suite and exact-image source/import checks. Native Predeploy run `37230207170`
then failed export with `IMAGE_LAYER_IDENTITY_MISMATCH`. That failed run and
artifact `11313780992` remain causal evidence, never a GREEN promotion artifact.
The exact three-file repair was reacquired before edits at
https://github.com/mbalbo2023/Porota-trading/issues/465#issuecomment-5983945890.

The newly downloaded ZIP SHA256 is
`e736dfa5ddf8dbe5709eb4a928dfeb3ba34f3bd07d18d68a1546657de066a31a`;
its actual exported image tar SHA256 is
`851ce39b64f2dc936953c03782506fd9c9c3b451f55f6cfb29edf083011d6e7a`.
The raw config hashes to image ID
`sha256:1fc8304f7731d358512bd24591f49f68b8277440a55a118df830d6d9caa333d7`.
Its 23 ordered `rootfs.diff_ids` agree with the uploaded image inspection.
Docker stored only 11 physical layer blobs: the empty 1,024-byte blob
`sha256:5f70bf18a086007016e948b04aed3b82103a36bea41755b6cddfaf10ace3c6ef`
is referenced 13 times, each with its matching DiffID. The bytes were raw tar
layers; compression was not this failure's cause.

The original `57a5370926bd41bca778734c4bcd42789a8f39e4` script, SHA256
`067b87a089dea5922de17a212300a0f643156387a2b27001676c1d2a7981757f`,
mistook repeated logical references for duplicated physical archive members.
Replaying its actual CLI against the unchanged native tar reproduced RED,
exit 1 and the same signature. Its canonical JSON SHA256 is
`3fba92753ca8b39e50f4619568f7a3b8decd13bbaf370cc9e72b8377e2aefc85`;
the independent source/input baseline binding SHA256 is
`d483d1e319f86a98d03f75bb88489b73dd98c32e4d2cb7c04a3b085b0ef44799`.

The corrected parser hashes each unique physical blob once, but verifies every
ordered reference against the corresponding immutable config DiffID. It never
uses `dict(zip(Layers, diff_ids))`, which could hide an incorrect earlier DiffID
behind a correct last occurrence. Physical duplicate paths, links, missing
blobs and unsafe paths still fail. Reference and DiffID arrays have strict
types, and logical reference count remains bounded by 50,000.

Content-addressed `blobs/sha256/...` names now match the SHA256 of actual stored
config/layer bytes. Compressed-layer raw bytes and uncompressed DiffIDs are
hashed separately in the same streaming read; re-compressing a blob while
retaining its old content address fails even when its DiffID is unchanged.
Nested gzip decoding consumes actual EOF and checks CRC/footer. Unique decoded
bytes and logical decoded bytes, including repeated references, are separately
capped at 8 GiB. The original outer gzip EOF/CRC/8 GiB and exact config-ID/label
checks remain mandatory.

Read-only peer attacks on the initial corrected draft, SHA256
`bcc9586b2258f82ba1534e2f646da6042190c701226fdaae715cd62c078dfb07`,
found two additional acceptance defects. Optional `LayerSources` descriptors
could disagree with verified stored blobs; this did not forge the trusted
filesystem bytes or prove a native-loader failure. Separately, Python accepted
`NaN`, `Infinity` and `-Infinity`, which are invalid JSON tokens. The exact draft
source and unchanged peer witness are preserved alongside the original REDs.

JSON parsing now rejects those nonstandard constants in every metadata document.
`LayerSources` may be absent, null (Go's nil map), empty or partial. Any supplied
descriptor must name a known DiffID, have a SHA256 digest and a nonnegative exact
integer size (never bool/float), and match the verified stored digest, byte count
and supported raw/gzip media type. Each corresponding ordered reference is
checked, including the case where two gzip encodings share one decoded DiffID.
Optional descriptors never replace the config's ordered DiffID authority.

The repaired CLI accepts the same unchanged native tar with 23 references,
11 verified blobs, 11 consistent descriptors and 1,183,733,248 logical decoded
layer bytes. This diagnostic
replay neither rebuilds nor loads Docker, and does not make the failed native
run GREEN. The resulting new candidate still requires its own complete root
suite, exact Predeploy and newly downloaded artifact verification.

Fifty-seven permanent regressions extend the original 129 focal cases to 186.
They cover the native 23/11/13 shape, legacy and content-addressed raw/gzip
references, conflicting earlier/later repeated DiffIDs, reordered/wrong refs,
physical duplicate/link/directory/missing-member attacks, strict metadata types,
stored-byte addresses, nested gzip damage, decoded/reference bounds, optional
descriptor consistency and strict JSON syntax. An
11-case causal run against the old script retained six valid-format rejection
REDs and five false-GREEN hardening counterexamples. All 33 are also replayed
against the exact old Git blob: 20 FAIL and 13 passing controls. The 24 additional
descriptor/JSON cases were replayed against the exact initial corrected draft:
19 FAIL and five passing controls. All raw XML/log/source bindings are retained
separately; provisional zero-test invocation errors are not classified as code
defects. Final frozen 186-case JUnits and native CLI source bindings are emitted outside Git
on both pinned Python 3.11.16 and 3.12, without source/output circularity.

## Fix and authority chain

1. Immediately after the exact candidate checkout, Predeploy automatically walks
   **every regular blob of `git ls-tree -rz --full-tree HEAD`**. It captures the
   candidate commit/tree, path, Git blob OID and mode, SHA256, byte count and
   representation roles. Dirty, staged different bytes, missing source,
   symlinks/hardlinks, executable-mode drift and submodules fail closed. SHA256
   and raw Git blob identity are calculated from the same opened byte stream;
   Git filters cannot substitute different bytes.
2. `POROTA_SOURCE_PROVENANCE.json` is the reserved generated pre-build manifest.
   It is canonical JSON with no wall-clock timestamps. A validator regenerates
   the entire manifest from exact Git authority, compares canonical bytes, and
   rechecks the checkout before use. Editing a row, removing a row, recomputing
   its own digest or changing SHA/tree cannot self-certify a replacement.
3. The existing single Docker build carries `porota.commit`, `porota.tree`,
   `porota.predeploy=v2` and `porota.source-manifest-sha256`. The manifest is
   included by the normal `COPY . .`; it is validated again inside the extracted
   `/app` filesystem. Every tracked image source is compared byte for byte and
   mode for mode. Missing files and **any unexpected file**, including executable
   paths hidden under `tests/`, `.github/`, bytecode or an unknown suffix, fail.
4. The deploy source bundle is now selected from the full Git tree by repository
   roles, excluding only `tests`, `docs`, `.github` and `.agents`. There is no
   suffix-based partial runtime file list. New assets, configuration and
   executables are selected automatically. Every selected row has exact Git
   bytes/mode. Both the source manifest and bundle manifest are included inside
   the bundle. Before export is accepted, the strict validator checks its member
   set, paths, regular-file types, duplicates, content, metadata, modes and outer
   digest against the frozen checkout.
5. The exported `docker save` tar is independently parsed without loading or
   rebuilding. SHA256 of its raw config must equal the image ID captured from
   the already validated image. Config labels are rechecked; every exported
   physical layer blob is streamed once, and every ordered reference is checked
   against the corresponding config `rootfs.diff_ids` entry, including repeats.
   Compressed OCI layer representations are hashed after decompression. The
   raw config/image identity and verified layers therefore bind the exported
   tar to the same image whose `/app` source bytes passed validation. Changing
   layers and recomputing a new config/ID cannot match the original image ID.

The SHA256 label and `source_manifest_sha256` fields mean the SHA256 of the
**complete canonical manifest file**, including its own digest field.
`manifest_sha256` inside that file is the SHA256 of the canonical document
**without that field**. Both relationships are deterministic and checked against
Git, rather than trusted solely from the document.

## Representation exceptions and build hygiene

Image exclusions are derived from the **versioned `.dockerignore`**, including
directory descendants, wildcards and negations; the responsible rule is recorded
for each excluded tracked path. Runtime Python/shell/service/config paths cannot
be excluded this way: an ignored runtime source makes provenance RED before the
build. Current excluded documentation remains Git-hashed evidence, but is not
represented as an image source. Historical audit documents are hashed as bytes;
their earlier SHA claims are preserved as history, not reinterpreted as claims
about this new candidate.

Generated metadata is limited to the reserved manifest files in the appropriate
artifact. It must have exact expected bytes and a non-executable mode. The image
contains the source manifest; the bundle contains source plus bundle manifests.
These names are forbidden as tracked source to avoid circular/self-authored
authority. Arbitrary generated files cannot become exceptions.

Application bytecode is disallowed in `/app`. Base-image/installed-dependency
bytecode outside `/app` is covered by layer digests, not compared to Git source.
Predeploy now compiles tracked Python in memory and sets
`PYTHONDONTWRITEBYTECODE=1`, so syntax checks/imports do not generate application
bytecode for `COPY`. The pre-build context guard permits only tracked source,
the exact reserved source manifest, and files excluded by the versioned Docker
policy; unexpected untracked input fails before consuming the single build.
Docker-created application data directories are legitimate empty directories,
not source-file exceptions.

## Permanent regressions and programable evidence

`tests/test_issue465_provenance.py` creates real Git repositories and linked Git
worktrees, and real tar/gzip archives. It invokes the actual CLIs. No mocks stand
in for Git, source-byte hashing, archive parsing or the byte provenance gates.

Negative cases include checkout byte/mode/staging/missing/link mutations;
manifest row removal, valid JSON tampering, duplicate keys and recomputed forged
digests; one-byte image mutations in code/assets/policy/CI sources; missing paths
and embedded manifest; extra hidden/runtime/executable/bytecode files; wrong or
missing commit/tree/content labels; executable metadata; bundle byte/manifest
tampering even after honestly updating the outer tar digest; missing, duplicate,
extra, absolute and traversal paths; archive symlink/hardlink attacks; exported
config-ID/label/layer mismatch, duplicate physical members and truncated gzip. Positive
cases cover deterministic reproduction, legitimate versioned exclusions,
automatic new-asset selection, linked-worktree `.git` files, raw and compressed
layer formats, and the complete offline CLI chain.

Predeploy uploads the pre-build source manifest, image inspection, image-source
verification in the artifact manifest, bundle verification, exported-layer
verification, frozen candidate identity and the exact artifacts. Every gate is
mandatory and fail-closed. The freeze record links the source manifest SHA256.
This front does not change the deployment workflow; existing consumers still
receive its compatible `files`, `file_count`, status and `bundle_sha256` fields.

The final integrated candidate's single exact Predeploy and downloaded artifact
rehash are authoritative. This software guard improves reproducibility and
detects byte drift; it does not replace the independent auditor's subsequent
rehash, certify the productive host, prove provider capacity or establish edge.

## Offline verification without a build

On the clean frozen candidate, generate the manifest from exact Git authority,
then generate and verify the deploy source bundle. All outputs must remain in a
temporary evidence directory except the reserved build-context manifest:

```text
python scripts/porota_artifact_provenance.py create --repo-root . --source-manifest POROTA_SOURCE_PROVENANCE.json --candidate-sha <exact-head> --tree-sha <exact-tree>
python scripts/porota_artifact_provenance.py checkout --repo-root . --source-manifest POROTA_SOURCE_PROVENANCE.json --verify-context
python scripts/porota_build_deploy_bundle_v2.py --repo-root . --source-manifest POROTA_SOURCE_PROVENANCE.json --output <temporary-dir>/bundle.tgz --manifest-out <temporary-dir>/bundle-manifest.json
python scripts/porota_artifact_provenance.py bundle --repo-root . --source-manifest POROTA_SOURCE_PROVENANCE.json --bundle <temporary-dir>/bundle.tgz --bundle-manifest <temporary-dir>/bundle-manifest.json
```

These CLIs perform no network, provider, trading DB or Docker operations. Final
HEAD/tree, suite counts, exact Predeploy and artifact digests are recorded after
freeze by the integration owner, avoiding a self-referential commit SHA.
