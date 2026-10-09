# Packed V2 capture allocation workstream

State at the initial source checkpoint `0b380438`: `DESARROLLADO`; native guards
and resource measurements had **not run**. The later 0b380438 focal ran 162/162
cases successfully in 23.64 s (JUnit), including the initial 30 new cases, on
the exact whole source and frozen157 interpreter. It is not acceptance of the
subsequent literal-limit correction or a resource gate. The corrected whole
source `934894569810b1bbc176ce93ba95cc1ee51cfb0c` subsequently passed its
35 allocation cases in 4.41 s (JUnit), including the five reduced-capacity
cases. That execution used the same literal frozen interpreter, exact 157
names/versions and preserved all 1,650 source SHA/modes/Git blobs, with zero
overlays, alien imports or network attempts. Raw evidence remains
under `/tmp/rc6-packed-capture-0b380438-157-raw`; source SHA/modes/Git blobs were
unchanged for all 1,650 files, with no overlays, alien imports or network calls.
This document does not grant pipeline, archive, UI, image, deploy or runtime
acceptance.

- Workstream: `RC6_PACK_CAPTURE_ALLOCATIONS` / `WRITE_OWNER`.
- Isolated branch: `work/rc6-packed-capture-allocations-20261005`.
- Base: `a6d622d784c03707001859d87688ddb9ba5fc4b6`, tree
  `814ecd05363f3b0fbeeaf1a6b7f56d4da7d72d92`.
- Original packed producer Git blob:
  `212b839c443b37bf5a8f9a860ff3736e81ed6f1b`.
- Scope: `rc6_shadow_runtime/packed_storage.py`, the new capture-allocation
  regression module and this workstream's evidence. No Root harness writes.
- Authorization: Root's scoped instruction following the d9 native BIG
  diagnostic; Source plan approved before native execution.

## Observed input, retained as RED

Root executed the complete d9 source tree with the exact frozen Python 3.11
interpreter and 157 installed distributions. The 12,000-identity / 60,000-input
observation diagnostic hit its 90-second deadline before member fsync or any
committed CURRENT. The child exceeded that deadline while preparing the
publication's first role, report. The completed 7.225 s prepare belongs to
the preceding funnel encoder; the later publication report prepare did not
return, and no checkpoint prepare ENTER occurred. Sampled stacks at
70, 80 and 90 s were in `_parts` / `capture` beneath
`PreparedPackedStorage.__init__` and `commit_generation`. Measured peak RSS was
1,289,154,560 bytes. These are observations, not proof that every remaining
second belongs to Capture allocations. This attribution corrects the initial
source plan after reviewing the actual ENTER order; the original RAW is intact.

Raw inputs, owned and preserved by Root:

- `/tmp/rc6-canonical-d9e7fb7d-big-gc-raw/native-big-gc-result.json`
- `/tmp/rc6-canonical-d9e7fb7d-big-gc-raw/child-stacks.log`

The diagnostic recorded 32,667 GC callback events, including 29 generation-2
start/stop pairs totaling 16.453 s. No GC event occurred in Source capture;
that capture returned in approximately 0.071 s. The earlier 0.25-second Source
capture failure was not reproduced here and its performance cause remains
unknown. This optimization neither closes nor relabels that failure.

## Change and equivalence boundary

The prior producer creates a frozen Capture object and a `(boundary, capture)`
tuple for every punctuation, key and scalar token, then retains and combines
those objects into output captures. Direct recursive append accumulates those
same tokens in a private bytearray and literal list. It materializes an
immutable Capture only when the old algorithm would flush, or when the
unchanged named/alias boundary produces one.

Flush occurs **before the same token** that would exceed `PACK_TARGET`, never
in the middle of a token. Shared/named subtrees retain the same predicate,
64-byte boundary test, cached bytes and reference lifetime. Literal marker
ordinals equal the old cumulative literal offset. Mutable root headers are
still recaptured; previously frozen static bytes do not become mutable views.

`PACK_TARGET=65536` remains a soft target. One larger scalar token can exceed
it, just as before; existing independent entry, unique, logical, marker, node,
depth, durable and expansion limits remain enforced. The change does not
increase a quota or claim that every pending buffer is at most 64 KiB.

The codec/version, canonical ASCII domain, compression, packet segmentation,
directory/instance/binding/reference order, all-occurrence SHA, JSON guards and
decoder remain unchanged. No GC policy or thresholds change. No cross-request
or cross-cut cache is introduced. No financial values, source clocks,
deadlines, identity population, math or admission rules change.

## Native guard plan (written, not yet executed)

New module: `tests/test_rc6_packed_capture_allocations.py`.

- `test_direct_capture_preserves_legacy_chunks_markers_and_compressed_wire`:
  reference the original generator loop across four soft targets and typed,
  volatile, named/aliased and oversized-token inputs; compare exact capture
  bytes, the complete compressed representation and independent canonical
  JSON. These are 16 declared parameter combinations.
- `test_volatile_root_sections_remain_single_exact_literal_capture`: four
  declared root names, with full literal bytes preserved.
- `test_direct_capture_keeps_combined_literal_capacity_guard`: four reduced
  capacity rejections and the exact four-literal boundary control. Root found
  the omitted local guard during the initial focal. The guard is restored
  before freezing bytes in `flush`; no default-limit inference substitutes
  for it. These five cases passed in the native focal of whole source 93489456.
- `test_plain_token_allocations_are_bounded_by_output_chunks_not_json_tokens`:
  count actual Capture constructor calls for 2,000 independent identities;
  assert new calls equal output chunks and compare with the original loop.
  This deterministic allocation guard does not assert a CPU time or RSS.
- `test_prepared_capture_freezes_static_bytes_and_recaptures_mutable_headers`:
  mutate the original static input and mutable headers after preparation;
  verify prior and new encoded snapshots preserve their specified bytes.
- `test_direct_capture_preserves_nonfinite_rejection`: three declared cases.
- `test_direct_capture_keeps_shape_limits_before_recursion`: cycle, depth,
  nodes and non-string-key cases.
- `test_native_worker_publication_full_reader_and_projection_keep_the_same_cut`:
  native PAPER producer plus worker, full committed reader and derived
  projection; match all role digests and the committed pointer.

The updated new module collected and passed 35 cases in the whole-source 93489456
frozen-source JUnit. Relevant existing guards
are the packed-storage, funnel-storage codec, generation and native runtime
wiring modules. Native test execution is serialized under a separate CPU slot.

## Pending evidence

No CPU or RSS savings have been measured. Both completed focals used a whole Git source
archive, zero product overlays, literal
`/workspace/venv_rc6_frozen311/bin/python`, exact 157 names/versions checked
before fixtures, preserved source SHA/modes, blocked network and import
closure. [NATIVE_EVIDENCE.json](NATIVE_EVIDENCE.json) binds both executed source
SHAs and the original raw hashes; XML, source indices and logs are retained as
lossless gzip of their original bytes. The 162 cases belong to 0b380438; the
corrected 35 cases belong to 93489456. No second 162-case run is claimed.

A subsequent native 12k/60k run requires Root's separate authorization
and the unchanged 90 s / 2 GiB / live 512 entries / live 128 MiB / archive
512 MiB / scratch 512 MiB constraints. This source checkpoint is not that run.
