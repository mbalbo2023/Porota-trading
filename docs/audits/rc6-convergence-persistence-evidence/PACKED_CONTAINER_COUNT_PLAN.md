# Packed container count: exact traversal change

WORKSTREAM_ID: `RC6_PACKED_CONTAINER_COUNT_20261005`  
Mode: `WRITE_OWNER`, local offline PAPER/SHADOW source only  
Branch: `work/rc6-packed-container-count-20261005`  
Base: `a301794c91093b262d5733e2b8027339e52246be`  
Scope: `rc6_shadow_runtime/packed_storage.py`, its allocation regression module,
and this workstream's evidence documents. No other owner's paths are written.

The native `400a677c` 12,000-identity run published a real PREOPEN generation
after entering the slow fsync and servicing five synthetic PAPER EXITs. It
then exceeded the original 90-second deadline during checkpoint restore.
It did not complete the requested full PREOPEN/OPEN caller. The independently
preserved auxiliary diagnostics restore that cut in 8.192 seconds, fail a
TIMED report capture with SIGSEGV, and fail the single NONE control at its
60-second diagnostic watchdog after entering preparation. Those diagnostics
do not measure publisher preparation time: the decoded report may have a
different alias layout. The SIGSEGV's cause remains unattributed. Their original
bytes and distinct source authorities remain in `capture_restore_real_cut/`.

The source change is limited to `_CaptureBuilder._count`. Scalar leaves return
without adding an identity or edge; the parent now performs that same
`isinstance(child, (dict, list, tuple))` check before entering another recursive
frame. Container arrivals still increment `incoming` before the previously
visited-object check. `objects` still retains each original object strongly,
and each distinct parent's children are visited only once. Dict values and
list/tuple iteration keep their original order and subclass behavior. Capture,
canonicalization, signed-zero keys, literals, markers, templates, mutable
headers, encoding, validation, and every bound remain unchanged.

The permanent regression reference freezes the old unfiltered `_count`; it
does not inherit the new production counter. Differential DAG cases compare
incoming counts, strong-reference identity, object insertion order, subclass
iteration, capture boundaries, literal bytes, the complete canonical compressed
envelope, and public decode. The DAG includes a repeated parent, a shared leaf
inside and outside it, and equal values with different identities. Additional
controls exercise container subclasses with overridden length/iteration,
non-JSON leaves using the existing `default=str`, typed scalar subclasses,
signed zero, bool/int separation, Unicode, NUL, exact clocks, and finite cycle
metadata followed by the unchanged public shape rejection.

The dispatch-count control verifies only the structural removal of calls on
scalar leaves. It does not assert a time or memory saving. Existing allocation,
capacity, nonfinite, depth, node, key, static/mutable-header, dual-codec,
adversarial-wire, and native writer/reader/projection guards remain required.
The coordinated focal subsequently passed 113/113 cases on the exact whole
Git archive of `ad5e10715467c27f9673643c7253e711c48d0934`: 1,845 source files,
the literal frozen Python 3.11 interpreter and its exact 157 distributions,
unchanged complete source hashes/modes/blob IDs, 94 imports from that archive,
zero alien imports or network/source-SQLite attempts, and no skipped cases.
Its original raw JUnit, receipt, driver, lock preflight, log, and full source
index are preserved losslessly in `packed_container_count/DOSSIER.json`.
The native result belongs to `ad5e1071`, not this later documentation commit.
A later integrated uninstrumented BIG90 run decides whether the full caller
fits the original deadline and RSS/quota constraints. This source change is
not evidence of that completion, a nine-hour retention horizon, an immutable
artifact, or runtime acceptance.
