# Lossless storage V2 and component archive V3 proposal

Status: **storage V2 candidate implemented with dual readers and native focal
validation; archive V3 integration and all large/horizon acceptance remain
pending**. The preceding operational source checkpoint is `6ed97987`.
The original inputs are four COMPLETE synthetic native cuts on `732b1e51`,
with 1,200 identities and 6,000 input observation rows. No provider is called.
The existing V2 generation protocol, CURRENT, external local high-water,
original V1 storage remains readable, and existing V2 archive objects/receipts retain their original bytes and dispatch.

## Findings and rejected models

The operational archive uses 1.832–1.846 MB per OPEN generation at this profile.
Its fixed 512 MiB quota cannot retain 1,081 generations over nine hours at
30-second cadence. Each tick and its clocks must remain represented.

Exact deltas of already-compressed tar bytes did not help. Independently gzipping
every small canonical fragment also failed: the initial field model emitted
326,048 references for the OPEN report and inflated the durable member to about
7 MB. Integer references fixed the reference overhead but did not recover
compression between small fragments. Those results remain negative evidence.

The alias-aware templated model preserved the entire canonical stream, every
native funnel field, and every byte of its five modeled archive members. Its
OPEN3→OPEN4 incremental model was 744,414 bytes, predominantly 656 KiB of new
whole SQLite pages. It still inflated each report/checkpoint source member to
about 6.5 MB. This model is therefore insufficient for the large live quota.
The raw result is `/tmp/rc6-templated-fragment-capacity-prototype/result.json`.
It is a private model, with `acceptance_complete=false`; it does not establish
an archive, UI, or resource gate.

Independent exact-page modeling by Finance preserved the original 2,211,840-byte
projection image. The full page pack was 1,053,283 bytes and its next delta pack
117,041 bytes, including the pack index. This result establishes feasibility
of portable decoding with the original previous pages. It does not establish
the whole archive quota, sustained growth, ACK, garbage collection, or horizon.

## Storage format under evaluation

Schema: `rc6.lossless-json-storage.v2`. Codec:
`GZIP_PACKED_TEMPLATE_CANONICAL_ASCII_JSON_V2`. V1 remains an explicit decoder
branch; an unknown schema or codec must fail closed. A helper recognizing both
schemas must replace every producer/consumer detector, including funnel recovery
and the returned actual `storage_schema` proof. A legacy archive never transcodes.

The logical domain is the exact canonical ASCII JSON stream used by storage V1:
sorted string keys, compact separators, `ensure_ascii=True`, no nonfinite JSON.
It differs from the UTF-8 funnel fingerprint domain; both hashes remain checked.
Every original source/known/effective clock string and null is preserved.

The candidate envelope contains:

| Field | Contract |
| --- | --- |
| `schema`, `codec` | Exact version strings |
| `packets` | Unique original gzip packets, each with compressed-byte SHA256 and strict ASCII base64 |
| `templates_count`, `literals_count` | Nonnegative integers; booleans are invalid |
| `directory` | SHA-bound gzip of fixed uint32 triples `(packet, offset, length)`; template entries precede literal entries |
| `instances` | SHA-bound gzip of uint32 triples `(template, binding_start, binding_count)`; bindings have dense local slots |
| `bindings` | SHA-bound gzip of uint32 literal ordinals; distinct slots may bind the same unique literal bytes |
| `references` | SHA-bound gzip of uint32 instance ordinals in complete logical order, including all repetitions |
| `logical_bytes`, `logical_sha256` | Length and SHA256 of every expanded logical occurrence in order |
| `storage_sha256` | Canonical hash of the complete stored envelope excluding this field |

Each template is an ASCII JSON fragment with a reserved zero byte followed by
a big-endian uint32 literal-slot index. Canonical ASCII JSON cannot contain a
literal zero byte. An exact original JSON lexeme replaces each marker. This
preserves Unicode escaping, negative zero, number spelling, scalar types, and
JSON strings containing escaped control characters. Literal bindings are a
compression device and introduce no financial identity or authority.

The writer captures a private immutable graph within one publication. It may
reuse serialized bytes for shared physical subtrees; it must count every logical
alias expansion. Packets restore compression across small templates. A tentative
64 KiB packet target is subject to measured large-entry bounds and the existing
generation durable 64 MiB/logical 512 MiB, funnel durable 32 MiB/logical 64 MiB,
32 million nodes, depth 64, live 128 MiB, and RSS 2 GiB limits. These limits are not increased by this design.
Candidate independent guards are 16,384 packets, 262,144 entries/instances,
1,048,576 references, 32 million bindings, 64 MiB per entry, at most 256 MiB of
unique template/literal bytes, 262,144 marker occurrences per template and
1,048,576 total markers. The 64 KiB packet target is soft; a large entry has its
separate hard guard. These new caps still require the native 12,000-identity
profile; the small and 1,200-identity checks do not certify that profile.

A read verifies every original packet's compressed SHA and complete gzip CRC
before reuse. Directory ranges must be typed, bounded, ordered and complete;
unindexed bytes, duplicate packets, unknown entries, overlapping ranges,
out-of-range ordinals, unused templates/literals/slots, truncated markers and
trailing compressed data fail closed. The linear reference expansion updates
length and SHA for **all** occurrences, including repetitions. Expanded byte
reuse is limited to this role and this call, after binding its literal table.
There is no cache or trusted flag across requests or generations.

The full reader additionally parses fresh JSON objects, rejects duplicate keys
and nonfinite values, applies node/depth checks to the complete logical graph,
and re-encodes the canonical ASCII stream. Mutable funnel recovery receives a
fresh graph; sharing verified bytes must not share mutable dictionaries/lists.
The UI's honest level remains `WIRE_AND_PROJECTION_SEMANTICS`, with all four
roles, typed headers, custody, derivation, CURRENT and complete wire hashes.

New publications recompute every outer payload digest, cross-payload link,
status digest, manifest link and projection derivation after emitting the new
nested funnel representation. Exact native state equality is insufficient by
itself: the encoded nested representation changes outer bytes and hashes.

## Archive format under evaluation

Schema: `RC6_SHADOW_ARCHIVE_COMPONENT_RECIPE_V3`, with an explicitly new receipt
schema, object URI and archiver version. An archive receipt cannot reinterpret
a V2 tar SHA as a V3 recipe SHA. Existing receipts and checkpoint history keep
their original byte hashes and sequence; verification dispatch is explicit.

Each generation keeps a complete recipe for `report.json.gz`,
`checkpoint.json.gz`, `status.json` and `manifest.json`; `projection.sqlite`
is present exactly when it is a member of the original cut. Legacy four-member
cuts remain four-member cuts.
Each member has its complete original byte length and SHA256. Gzip source
members are reconstructed by concatenating their original compressed framing
components. Plain members are decoded from stored portable gzip components.
The recipe references stable content-index ordinals through typed compressed
uint32 streams; a component's full compressed SHA and location remain bound in
the recipe and original immutable pack index. Pack headers used to find a
candidate CID are bounded lookup hints; full pack/CID validation precedes reuse.
There is no independent mutable CAS database. Recovery never invokes a compression encoder.

SQLite uses a packed exact-page object per generation. The page helper binds
the whole original target SHA, pack SHA, previous original-file SHA, page
ordinal/base SHA, codec and bounded dependency depth. An explicit full anchor
limits depth to at most 32. A delta uses the actual previous original page as
zlib dictionary and validates EOF/trailing data/CRC and the complete restored
page/file SHA. Shared deflate bytes do not imply shared logical rows; the
original dictionary, footer, full BLOB, SQLite image and manifest stay bound.

Content is packed into owned files, rather than one file per small component.
All residency, index, recipes, receipts, checkpoints, locks, directories,
temporary files and simultaneous staging count toward both logical and
allocated-byte admission. Files remain private, owned, one-link regular files;
directories remain private and aliases/unknown namespace fail closed. The
32768 archive-file limit and physical 512 MiB quota apply to the full peak.
The stdlib inspector must evolve its whitelist explicitly for V3 and retain
its metadata-only verification level.

Publication order is original components and page pack durable plus directory
fsync, recipe durable, complete reconstruction/CRC/SHA and exact member-set/
manifest verification from archive bytes, receipt durable, chain/head durable,
then local ACK. Rotation and delete intent remain forbidden before verified ACK.
Interrupted ACK recovery validates membership in the newer receipt chain and
regenerates the old ACK without rewinding the head. Missing/corrupt dependencies
prevent ACK and source deletion. Allocations and abandoned staging require
persistent recovery intent; the archive must not accumulate untracked debt.

A rolling wheel plus recovery needs a bounded receipt/checkpoint compaction
protocol. Garbage collection derives reachability from committed recipes and
the intact chain, not a mutable refcount alone. Pack compaction copies the exact
original component bytes to durable new packs, atomically seals a new location
index, and persists deletion intent before deleting obsolete packs. No component
reachable from a retained committed recipe may disappear. Receipt high-water
and the compacted boundary remain durable and never become reused sequence.

## Gates still required

The next private measurement must establish source compression and incremental
physical costs for PREOPEN, OPEN, OPEN+30 and OPEN+60 before production work.
No last-pair extrapolation constitutes the nine-hour contract. Native 1,201-tick
ten-hour execution with restarts and at least a full wheel plus recovery must
measure all physical peaks at the fixed quotas. The 2,000-generation archive
rotation/checkpoint/recovery test must run on the new format. A five-identity
horizon remains separately labeled and does not certify the 1,200/12,000 profiles.

The full canonical-factory 12,000-identity/60,000-input-row producer must publish
both ticks within 90 seconds and RSS 2 GiB, enter and complete the actual blocked
fsync, execute five synthetic PAPER EXITs during that block, and preserve source
bytes/provider zero. Its native read truncation must remain explicit.
Every large UI request/render remains bounded by 1 second and 4 MiB, with all
identities/cohorts navigable; health remains bounded by 2 seconds and uses the
current factory fingerprint. Final gates require one integrated source SHA/tree,
fresh source/custody inventories and honest verification levels.

## Concrete private model and focal checkpoint

The actual product candidate, not the rejected per-fragment model, processed
four retained COMPLETE native 1,200-identity/6,000-input cuts in 43.217 seconds,
with 294,895,616-byte RSS. All source bytes, atimes, mtimes and ctimes remained
identical. The OPEN3→4 source report was 559,730 bytes and checkpoint 880,637
bytes. The exact page pack was 117,042 bytes for a 2,211,840-byte original image.
Original modeled members reconstructed byte-for-byte without a recovery encoder.

Its private incremental model was 326,092 bytes: 312,930 new original components,
1,434 compressed recipe bytes, 1,488 modeled index bytes and explicit allowances.
The projected 1,201-cut/10-hour total was 393,394,407 bytes. This is **not physical
quota admission, a published archive, sustained capacity, or a horizon gate**:
real recipes, physical allocation, staging, anchor periods, receipts, dependency
recovery, rolling GC and cadence must still be exercised in the integrated code.
The raw result remains `/tmp/rc6-product-packed-capacity-model-v2-r1/result.json`.

Native focal validation passed 168/168 nodes, including dual V1/V2 readers,
Unicode/types/negative zero, every repeated occurrence, private fresh decoded
objects, mutation/CRC/resealed index attacks, node/depth/canonical guards,
original generation publication/SIGKILL/ENOSPC tests and a small canonical-factory
stress with actual fsync and five PAPER exits. JUnit:
`/tmp/rc6_packed_storage_native_forward_final.xml`.

An earlier new fixture attempted an external reader under its own writer lock;
it correctly failed and was changed to the writer's `read_generation`. The first
implementation bypassed the historical `gzip.compress` fault hook; restoring the
native deterministic gzip encoder retained that original adversarial guarantee.
Both preliminary negatives remain distinct from the final focal pass. Neither
focal nor model closes the 12k/60k, 90-second, 2-GiB, UI one-second, SRE two-second
or literal nine-hour retention requirements.
