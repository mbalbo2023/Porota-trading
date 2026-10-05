# Exact volatile scalar reuse

WORKSTREAM_ID: `RC6_PACKED_VOLATILE_SCALARS_20261005`  
Mode: `WRITE_OWNER`, offline local PAPER/SHADOW source only  
Branch: `work/rc6-packed-volatile-leaves-20261005`  
Base: `c42bfdba71d6b5f046c79ff8da8cd57eeedf78dd`  
Owned scope: `rc6_shadow_runtime/packed_storage.py`, associated new
`tests/test_rc6_packed_volatile_scalars.py`, and this workstream's evidence.

The complete native BIG9d32 caller remains RED at the original 90-second
deadline. It completed PREOPEN after 89.741 seconds, performed the real slow
fsync and five synthetic PAPER EXITs inside it, and did not complete OPEN.
The original raw result records `storage_prepare` as an aggregate of four
initializers totaling 41.796 seconds, `storage_encode` as seven calls totaling
10.748 seconds, `storage_metrics` as four calls totaling 3.729 seconds, and
checkpoint restore as three calls totaling 8.995 seconds. These are inclusive
handler measurements, not a breakdown of the volatile scalar branch. The
coordinator preserves the original raw receipt and source/DATA custody in
`docs/audits/convergence/evidence/native-big-9d32d326-157/`. No saving is
attributed to the preceding small-container optimization or this new proposal.

Source review identifies a repeated operation: `_append` converts every
volatile child with `_canonical(child)` before binding it. Volatile roots use
the same fresh conversion. This omits the existing typed reuse available to
ordinary scalar leaves. Native source candidates, telemetry, instruments and
other reports contain source/receipt clocks and repeated null/bool values,
but neither branch frequency nor its individual duration has been measured.

The proposed private binding encoder reuses only bytes of exact builtin
None/bool/int/float/str leaves within **one** `capture()` call. Keys distinguish
the Python type and use `float.hex()` for signed zero. Subclasses, default=str,
containers, strings above 256 characters and integers above 4096 bits retain
the original fresh canonical conversion. The last two sizes are optimization
heuristics only; they never add a rejection or change storage acceptance.
The map admits at most 4096 entries and at most 1 MiB of retained canonical
bytes. That byte budget does **not** describe total Python map/key overhead or
RSS. Its key/value sizes and entry count are separately bounded; the native
publisher RSS requirement remains unchanged and pending measurement. Entries
or bytes that do not fit take the original conversion, without eviction of a
literal, additional acceptance limit or changed value.

This map is separate from both the caller-supplied named-capture cache and
the ordinary scalar cache. Existing poisoned/short named entries and plain
scalar entries cannot supply volatile literal bytes. No map crosses a capture
call, preparation or cut. `finally` restores an active outer capture context
after reentrancy, or clears the top-level context, on success and failure.
Direct append outside a capture keeps the original fresh conversion.

Every occurrence still emits its own literal slot, including equal clocks.
The buffer, marker indexing/order, alias counts, `_named`, canonical domain,
chunk boundaries, compressed encoding, ordered full logical SHA, public
readers, all capacity/shape/nonfinite checks, clocks, GC policy and deadlines
remain unchanged. Static prepared bytes still freeze once; mutable root
headers still capture afresh. No financial formula, routing or entry authority
changes.

The independent append/capture oracle is frozen from baseline Git blob
`f2d2c238c64b93a3a414d3427dbe9464e7812ffb`, with only module qualification
adjusted. Differential guards cover complete captured chunks/literals,
compressed envelopes and full decode, exact remaining capacity targets,
Unicode/NUL, bool/int/signed-zero separation, aliases/named boundaries,
unknown/default=str/subclass behavior, caller cache attacks, private budget
overflow, MAX_BINDINGS without coalescing, nonfinite/fault cleanup, nested
capture success/failure and static/mutable-header freezing. Canonical dispatch
counts verify only call structure; they are not CPU/RSS measurements.

SOURCE review and AST comparison precede an exact clean commit and independent
Finance review. Native focal execution requires a coordinated CPU slot and a
whole Git archive, the literal frozen Python 3.11 interpreter with its exact
157 locked versions, unchanged source hashes/modes/blob IDs and raw JUnit.
No Native test, big publisher run or auxiliary decode replay has been executed
for this proposal. A fresh integrated uninstrumented full PREOPEN/OPEN caller
must still meet the original 90-second deadline, 2-GiB RSS and original
live/archive/scratch quotas. The retention horizon, complete UX/browser bounds,
immutable artifact/runtime gates and the cause of the prior TIMED SIGSEGV
remain separate pending obligations.
