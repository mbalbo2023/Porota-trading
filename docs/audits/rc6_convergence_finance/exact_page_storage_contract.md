# Exact source-page storage contract

Status: isolated helper; custody, archive integration and final gates remain the
persistence owner's responsibility. Root authorized the new module after API
agreement. Neither this helper nor its pack grants ACK, deletion, financial
entry, provider, GC or capacity authority.

`rc6_shadow_runtime/exact_page_storage.py` uses only the Python standard library.
`encode_page(target_bytes, base_original_bytes=None)` returns a closed record
with codec, encoded bytes, target SHA/length and optional original-base SHA.
`decode_page(record, base_original_bytes=None)` restores precisely 4096 bytes,
verifies the original base, gzip CRC or zlib Adler, EOF, absence of trailing
compressed data and the complete target-page SHA. A standalone page record is
not authenticated; its archive caller must bind it to the immutable original
member and pack hashes.

`encode_page_pack(target_bytes, previous_bytes=None, previous_depth=0,
force_full=False)` returns one binary pack and diagnostics. The file consists
of a 92-byte header, a fixed 72-byte ordered index record per page, and the
persisted encoded pages. No page creates its own filesystem object. A pack
binds target file SHA/length, previous original-file SHA, depth, page ordinal,
codec, original base-page SHA and original target-page SHA. Grown and shortened
files retain their exact byte lengths. An identical previous file gets an
independent pack to prevent a SHA-addressed self-reference; the archive owner
still preserves a recipe and receipt for each generation.

`inspect_page_pack(pack, expected_pack_sha256=..., expected_target_sha256=...)`
checks only wire hash and the closed typed index. It explicitly does not claim
source reconstruction. `decode_page_pack` requires the same independent hashes
plus the previous original bytes and their verified depth when dependent. It
uses decoder operations only and checks the reconstructed whole-file SHA. A
receipt cannot bind merely the hash claimed inside the pack; the expected
original target SHA comes from the preserved native manifest/member.

Bounds are page 4096 bytes, source 64 MiB, encoded page at most 4224 bytes,
16384 pages, and pack at most 70385756 bytes including index/header. These are
helper bounds, not increases to active 128 MiB or archive 512 MiB quotas. The
header/index/payload, receipt/recipe, source/anchor dependencies, location index,
locks, directories, allocated file space and simultaneous staging must all be
counted by archive admission.

The hard dependency limit is 32 edges. A previous depth 32 forces a full anchor.
For an anchor every 32 generations the owner uses `force_full`, giving at most
31 edges per such chain. The helper verifies supplied depths and exact original
previous-file bytes, but does not resolve filesystem paths or authenticate an
ancestor chain by itself. The archive owner must derive the prior depth from
the verified chain under its custody lock, reject cycles/missing dependencies,
and retain all components reachable from every retained committed recipe.
It must reconstruct all five original members and verify their complete native
manifest/member hashes before ACK and any source deletion. Restoration never
re-encodes an archive recipe or reads latest mutable source tables.

Private prototype evidence: 61 pytest cases passed with no skips/failures/errors,
plugin autoload disabled, bytecode writes disabled, network blocked and no
product-repository imports. Two existing synthetic native projection images
from 732b1e51 remained byte/metadata/atime identical. Their 2211840-byte target
restored exactly; the initial full pack was 1053283 bytes and the next pack
117041 bytes, including 38880 index bytes and 92 header bytes. That is a
viability result for two cuts, not a sustained archive horizon. The private
receipt is preserved as `exact_page_storage_prototype_receipt.json`, with SHA256
`1af0e3654b865a95141f00aec115d05fd6841fb8ef3c59e92ee2b37a1b5cc41e`.
The exact executed prototype/test bytes are preserved under
`probes/exact_page_storage_prototype.py` and
`probes/exact_page_storage_prototype_tests.py`. The preserved private test
still names its original `/tmp` source/artifact paths; it is evidence of that
execution and does not claim a standalone replay environment. Its 61 cases
are not 61 independent financial scenarios.

The committed suite replaces the private artifact-path case with actual
in-memory SQLite revisions, Unicode and microsecond data; its source-level
execution is pending release of the exclusive FullGov measurement.

ArchiveV3 integration, crash/ACK/namespace/chain/GC tests, the complete normal
1200/1201-tick physical horizon and browser/final frozen-source gates remain
unverified. Real 20-session provenance stays EXTERNAL_NO_VERIFICADO and economic
edge stays EDGE_NO_DEMOSTRADO. Original requirement/scenario counts are unchanged.
