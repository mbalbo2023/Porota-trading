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

SOURCE and independent Finance review closed without a material blocker on
`d51b4577d2a41443c8fb453b768d5e2c88448e8d`, tree
`ceaa55e1852308fc46c4178928eae18d36aea962`. Before that commit, Finance
identified an unqualified `PACK_TARGET` name in the new frozen oracle. It was
corrected to `packed.PACK_TARGET` before any Native execution. The original
WIP and its classification remain preserved as a static preparation defect,
not a product RED.

The authorized five-module focal on the complete Git archive passed
**271/271** cases: 74 volatile bindings, 84 small containers, 43 allocations,
48 packed storage and 22 funnel storage codec. JUnit has zero failures,
errors, skips or duplicate nodes. PID 220202 used the literal
`/workspace/venv_rc6_frozen311/bin/python` (Python 3.11.16); preflight verified
157 lock names and versions before any fixtures. This is not a byte proof of
installed distributions. All 1,940 Git file hashes, modes and blob IDs remained
identical, with zero overlays, 97 source imports and zero alien imports.
Network and protected source-SQLite attempts were both zero; DATA400 was not
an input and no DATA400 before/after metadata snapshot is claimed. The
11.271-second focal duration includes setup and is not publisher performance.

The exact original wrapper, log, preflight, JUnit, receipt, source index,
tested sources, original plan, locks, frozen baseline and pre-Native oracle
correction are stored losslessly in
[the Native dossier](packed_volatile_scalars/DOSSIER.json). Its JUnit SHA256 is
`24be2472dc3d1ae1056cf283535b354372ebbb2c80377ed6ad02154f62bdcbcb`;
receipt SHA256 is
`334f07896f4bcb30398b2502f04e3ba2aa89e99c596e64558001036a5b3a128f`.
No Native publisher run or auxiliary decode replay was executed for this
change. A fresh integrated uninstrumented full PREOPEN/OPEN caller must still
meet the original 90-second deadline, 2-GiB RSS and original live/archive/
scratch quotas. No branch frequency, saving or publisher CPU result is claimed.
The retention horizon, complete UX/browser bounds, immutable artifact/runtime
gates and the cause of the prior TIMED SIGSEGV remain separate pending
obligations.
