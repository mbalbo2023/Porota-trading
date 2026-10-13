# Exact scalar terminal dispatch

WORKSTREAM_ID: `RC6_PACKED_SCALAR_DISPATCH_20261005`  
Mode: `WRITE_OWNER`, local offline PAPER/SHADOW source only  
Branch: `work/rc6-packed-scalar-dispatch-20261005`  
Base: `d05078845cd40b37ef75744ea80a1695c2b37dbb`  
Base tree: `e852da3496e7356fbf0a11cc50c697d69309e6d4`  
Owned scope: `rc6_shadow_runtime/packed_storage.py`, new
`tests/test_rc6_packed_scalar_dispatch.py`, associated evidence.

The native BIGd050 caller remains RED at the original 90-second deadline.
PREOPEN completed after 83.599 seconds; OPEN did not complete. Its original
raw receipt records four storage initializers totaling 41.255 seconds, seven
encodes totaling 7.383 seconds, four metrics calls totaling 2.943 seconds, one
publication totaling 51.347 seconds and three restores totaling 7.621 seconds.
These inclusive aggregates do not measure the scalar dispatch branch or
attribute a saving to the preceding volatile-byte reuse. The original caller
retained source and DATA custody, recorded 1,597,972,480 bytes of peak RSS and
five synthetic PAPER EXITs inside the real fsync barrier. These partial facts
do not replace full PREOPEN plus OPEN completion.

Source review shows a redundant terminal dispatch in `_CaptureBuilder._append`:
an exact builtin None/bool/int/float/str child recursively enters `_append`,
finds that it is not a container, bypasses named and small-container branches,
and reaches `buffer.append(self._scalar(child))`. Large native telemetry rows
have more than 16 fields and mix clocks with subtrees; the existing whole
small-container shortcut therefore does not cover this ordinary loop. No
branch frequency, isolated CPU time or improvement has been measured.

The change calls that same terminal statement directly from dict/list loops
only when `type(child)` is one of those five exact builtin types. Dict keys
still use the original scalar path, and volatile keys are handled first by
the existing binding encoder. Arrays keep their original commas, order and
fallback name. All containers, scalar subclasses and default=str values
retain recursive `_append`. There is no token grouping, new fits rule,
recoding, cache, shortcut hash or alias-plan sharing. Every scalar token is
still individually appended to the original buffer at the original point.

Only `_append` changes. `_scalar`, typed signed-zero keys, `_named`, `_count`,
strong references, volatile contexts, the buffer, static capture freezing,
mutable header recapture, wire encoding/decoding, hashes and all limits remain
identical. Existing caller/scalar-cache behavior remains observable; this
optimization does not add a source of trusted bytes. GC policy, clocks,
deadlines, quotas and financial behavior remain unchanged.

The independent append and capture oracle is frozen from baseline Git blob
`d55e0164a7c7be6325ad3b12ef7bc2182cdc6bd2`, changing only qualification of
module globals to `packed.`. New guards compare individual append/bind/boundary
operations, complete Capture chunks and literal occurrences, all compressed
wire bytes, ASCII logical SHA/length and the full reader with both sharing
modes. Fixtures include large rows, tiny/exact/oversized targets, lists/tuples,
DAG aliases and named fields, container iteration/getitem order, scalar
subclasses, False/int zero and both signed float zeroes, Unicode/NUL, long
strings/integers, private caller/scalar-cache adversaries, volatile roots,
direct root append, MAX_BINDINGS, nonfinite/default=str faults, reentrancy,
capacity rejections and static/mutable-header updates. A dispatch/order guard
checks only call structure and unchanged `_scalar` invocation order; it is
not a publisher performance measurement.

AST/oracle and independent Finance review closed without a material blocker on
`270242ddd9e1f1fc8c158835304dd2ca186208f2`, tree
`6d6dc9461a772030daa2c7d614b1473ecf98903e`. Static comparison confirms that
only the productive `_append` method changed and both oracle methods match
the baseline AST after removing only `packed.` qualification.

The authorized six-module focal passed **365/365** cases in one collection:
94 scalar dispatch, 74 volatile bindings, 84 small containers, 43 allocations,
48 packed storage and 22 funnel codec. The 94 scalar cases are new; the other
271 cases overlap the previous focal and are not added as independent coverage.
JUnit records zero failures, errors, skips or duplicate
nodes. PID 224746 ran the literal `/workspace/venv_rc6_frozen311/bin/python`
(Python 3.11.16) after verifying 157 lock names and versions before fixtures;
this is not a byte proof of installed distributions. All 1,975 source Git file
SHA256s, modes and blob IDs remained identical, with zero overlays, 98 own
imports and zero alien imports. Network and protected source-SQLite attempts
were zero. DATA400 was not an input and no DATA400 all-stat snapshot is claimed.
The process exited zero and its PID was absent from `/proc` after completion.
The 10.658-second focal duration includes setup, not publisher performance.

[The Native dossier](packed_scalar_dispatch/DOSSIER.json) preserves 16 originals
losslessly, including wrapper, source export driver/index, locks, log, JUnit,
receipt, original plan, tested sources, baseline and main-PID exit observation.
JUnit SHA256 is
`b35a8255a8bf67f21c80581727e763245ddfca28355bc7bfba23738a0d08f172`;
receipt SHA256 is
`8144d39d70d054318ad6583463add9cdea97995e5c54add05f92affe4390aa2a`.
No publisher run or auxiliary decode replay was executed for this change.

Two additional caller observations are kept separate. The same `engines`
object appears in report and checkpoint, but separate builders may have
different alias thresholds and named-cache state; sharing root captures or
`incoming` between roles is not part of this change. Publication also encodes
each role in both mandatory packs, first for reservation and then for the
retention metadata. Those packs, their full logical SHA over every occurrence
and exact admission must remain. For gzip roles, `persistence.pack()` first
calls `_encode(envelope)` to check uncompressed JSON length, then
`envelope_components(envelope)` canonicalizes the storage envelope again.
A future separately reviewed API might return exact raw-parts length with
the same compressed components for strictly builtin native V2 envelopes.
That could avoid a duplicate materialization while preserving the raw-byte
cap, but it has no measured weight and is not implemented here. Legacy/custom
default=str call count and effects must retain their original fallback.

The original full canonical 12,000-identity/60,000-physical-observation PREOPEN
plus OPEN caller must still meet 90 seconds, 2 GiB RSS and every live/archive/
scratch quota, preserving actual consumption/truncation and real fsync with
five PAPER EXITs. No saving or accepted BIG90, retention horizon, complete
UX/browser, immutable artifact or runtime gate is claimed. The prior TIMED
auxiliary diagnostic SIGSEGV remains unattributed and its RAW stays preserved.
